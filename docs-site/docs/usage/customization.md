---
id: customization
title: Customization
sidebar_position: 4
---

# Customization

Every extension point in `agentprdiff` is a small, plain Python callable.
You never have to subclass anything to add behavior.

## Custom graders

A grader is `Callable[[Trace], GradeResult]`. Wrap any logic you want.

```python
from agentprdiff import GradeResult, Trace

def first_tool_was(name: str):
    """Pass iff the first tool call has the given name."""
    def _grader(trace: Trace) -> GradeResult:
        first = trace.tool_calls[0].name if trace.tool_calls else None
        passed = first == name
        return GradeResult(
            passed=passed,
            grader_name=f"first_tool_was({name!r})",
            reason=f"first tool was {first!r}",
        )
    return _grader
```

Use it like any built-in grader:

```python
case(name="refund", input="…", expect=[first_tool_was("lookup_order")])
```

### Reading metadata you put on the trace

```python
def metadata_eq(key: str, expected):
    def _grader(trace: Trace) -> GradeResult:
        actual = trace.metadata.get(key)
        return GradeResult(
            passed=(actual == expected),
            grader_name=f"metadata_eq({key!r}, {expected!r})",
            reason=f"metadata[{key!r}] = {actual!r}",
        )
    return _grader
```

### Asserting on LLM-call shapes

```python
def model_used(model: str):
    def _grader(trace: Trace) -> GradeResult:
        models = {c.model for c in trace.llm_calls}
        return GradeResult(
            passed=(model in models),
            grader_name=f"model_used({model!r})",
            reason=f"models seen: {sorted(models)}",
        )
    return _grader
```

### Checking the final tool's result payload

```python
def last_tool_returned_status(status: str):
    def _grader(trace: Trace) -> GradeResult:
        last = trace.tool_calls[-1] if trace.tool_calls else None
        actual = (last.result or {}).get("status") if last else None
        return GradeResult(
            passed=(actual == status),
            grader_name=f"last_tool_returned_status({status!r})",
            reason=f"last tool returned status={actual!r}",
        )
    return _grader
```

## Custom semantic-grader judges

A judge is `Callable[[str, Trace], tuple[bool, str]]`. The first tuple
element is pass/fail; the second is a free-form reason that lands in the
report.

### Regex judge (deterministic, no LLM)

```python
import re
from agentprdiff import Trace
from agentprdiff.graders import semantic

def regex_judge(rubric: str, trace: Trace) -> tuple[bool, str]:
    passed = bool(re.search(rubric, str(trace.output or ""), re.I))
    return passed, ("matched" if passed else "no match")

expect = [semantic(r"refund.*\d+\s*business days", judge=regex_judge)]
```

### Embedding-similarity judge

```python
import os
from openai import OpenAI

def cosine_judge(rubric: str, trace: Trace) -> tuple[bool, str]:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    out = str(trace.output or "")
    e = client.embeddings.create(
        model="text-embedding-3-small",
        input=[rubric, out],
    ).data
    a, b = e[0].embedding, e[1].embedding
    sim = sum(x*y for x, y in zip(a, b))
    passed = sim > 0.78
    return passed, f"cosine={sim:.3f} (threshold 0.78)"

expect = [semantic("agent acknowledges the refund", judge=cosine_judge)]
```

### Pinning the judge per-case

Pass `judge=` explicitly to override the env autodetect — useful in mixed
suites where a few cases need a real judge and the rest can stay free:

```python
from agentprdiff.graders.semantic import openai_judge, fake_judge

JUDGE = openai_judge(model="gpt-4o-mini")

cases = [
    case(name="refund", input="…", expect=[semantic("rubric", judge=JUDGE)]),
    case(name="echo",   input="…", expect=[semantic("rubric", judge=fake_judge)]),
]
```

## Custom reporters

Reporters render a `RunReport`. The package ships three:

- `TerminalReporter` — Rich table for CI logs.
- `JsonReporter` — JSON envelope for artifact archiving.
- `ReviewReporter` — verbose per-case panel for `agentprdiff review`.

To add a JUnit-XML reporter (a common request):

```python
from xml.etree.ElementTree import Element, SubElement, ElementTree
from agentprdiff.runner import RunReport

class JUnitReporter:
    def render(self, report: RunReport, path) -> None:
        suites = Element("testsuites")
        ts = SubElement(suites, "testsuite",
                        name=report.suite_name,
                        tests=str(report.cases_total),
                        failures=str(report.cases_regressed))
        for cr in report.case_reports:
            tc = SubElement(ts, "testcase", classname=report.suite_name, name=cr.case_name)
            if cr.has_regression:
                fail = SubElement(tc, "failure", message="regression")
                fail.text = "; ".join(
                    f"{r.grader_name}: {r.reason}"
                    for r in cr.grader_results if not r.passed
                )
        ElementTree(suites).write(path, encoding="utf-8", xml_declaration=True)
```

Use it from your own driver:

```python
from agentprdiff import Runner, BaselineStore
from agentprdiff.loader import load_suites

store, runner = BaselineStore(), Runner(BaselineStore())
junit = JUnitReporter()

for s in load_suites("suite.py"):
    report = runner.check(s)
    junit.render(report, f"junit-{s.name}.xml")
```

## Custom Trace metadata

`Trace` allows arbitrary extra fields (it's `pydantic` with
`extra="allow"`) and exposes a typed `metadata: dict[str, Any]` for tags
that round-trip cleanly to JSON.

```python
trace.metadata["model_temperature"] = 0.2
trace.metadata["request_id"] = "abc-123"
trace.extra["my_internal_thing"] = {"foo": [1, 2, 3]}  # also persisted
```

Custom graders can read either. Anything the differ doesn't natively
understand simply round-trips — useful for downstream analytics scripts.

## Plugging a custom store backend

`BaselineStore` writes to the local filesystem, which is the right default.
If you need to back baselines with S3, GCS, or a database:

1. Subclass `BaselineStore`.
2. Override `save_baseline`, `load_baseline`, `save_run_trace`, and
   `ensure_initialized`.
3. Inject it into `Runner(your_store)`.

```python
from agentprdiff import BaselineStore, Runner, Trace

class S3BaselineStore(BaselineStore):
    def __init__(self, bucket: str):
        self.bucket = bucket
        self.root = f"s3://{bucket}/agentprdiff"

    def save_baseline(self, trace: Trace):
        # boto3 put_object(...)
        ...

    def load_baseline(self, suite_name, case_name):
        # boto3 get_object(...) → Trace.model_validate_json(...)
        ...

    def save_run_trace(self, run_id, trace: Trace):
        ...

    def ensure_initialized(self) -> None:
        ...  # nothing to do for S3
```

The CLI cannot use a custom store directly — wrap your runner in your own
entry point.

## Custom CLI commands

`agentprdiff.cli.main` is a Click group. To add a project-specific
subcommand without forking the package, write a thin wrapper:

```python title="my_cli.py"
import click
from agentprdiff.cli import main

@main.command("export-csv")
@click.argument("suite_file")
def export_csv(suite_file):
    """Run the suite and export results to CSV."""
    ...

if __name__ == "__main__":
    main()
```

Run as `python -m my_cli export-csv suite.py`.

---
id: python-api
title: Python API
sidebar_position: 1
---

# Python API Reference

Everything importable from `agentprdiff` and its submodules.

## Top-level imports

```python
from agentprdiff import (
    # data model
    Suite, Case, Trace, LLMCall, ToolCall,
    # graders
    Grader, GradeResult,
    # diffing
    TraceDelta, AssertionChange, diff_traces,
    # runner
    Runner, RunReport, CaseReport,
    # storage
    BaselineStore,
    # constructors
    suite, case, run_agent,
    # version
    __version__,
)
```

## `suite(name, agent, cases, description="")`

Construct a `Suite`.

| Param | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Stable identifier; used as the directory under `.agentprdiff/baselines/`. |
| `agent` | `Callable[[Any], Any]` | required | Your agent. May return `output` or `(output, Trace)`. |
| `cases` | `list[Case]` | required | One per behavior you want to pin. |
| `description` | `str` | `""` | Free-form note for the maintainer. |

```python
my_suite = suite(name="billing", agent=my_agent, cases=[...])
```

## `case(name, input, expect, tags=None)`

Construct a `Case`.

| Param | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Stable identifier; used as the JSON filename. |
| `input` | `Any` | required | Forwarded verbatim to `agent(input)`. |
| `expect` | `list[Grader]` | required | Each grader is `Callable[[Trace], GradeResult]`. |
| `tags` | `list[str] \| None` | `None` | Free-form tags for grouping / filtering by future tooling. |

```python
case(name="happy_path", input="…", expect=[contains("ok")], tags=["smoke"])
```

## `Suite`

Pydantic model.

```python
class Suite(BaseModel):
    name: str
    agent: AgentFn
    cases: list[Case]
    description: str = ""
```

`Suite` is what the loader harvests from your suite file (every
module-level instance is run).

## `Case`

```python
class Case(BaseModel):
    name: str
    input: Any
    expect: list[Grader] = []
    tags: list[str] = []
```

## `Trace`

The unit of comparison. JSON-serializable.

```python
class Trace(BaseModel):
    case_name: str
    suite_name: str
    input: Any
    output: Any = None
    llm_calls: list[LLMCall] = []
    tool_calls: list[ToolCall] = []
    total_cost_usd: float = 0.0
    total_latency_ms: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    error: str | None = None
    metadata: dict[str, Any] = {}
    run_id: str          # auto: 12-char hex
    created_at: str      # auto: ISO-8601 UTC

    def record_llm_call(self, call: LLMCall) -> None: ...
    def record_tool_call(self, call: ToolCall) -> None: ...
```

`record_llm_call` / `record_tool_call` append to the lists *and* update
the running cost / latency / token totals.

`Trace` is configured with `extra="allow"` so you can attach ad-hoc
fields and they will round-trip through JSON.

## `LLMCall`

```python
class LLMCall(BaseModel):
    provider: str                                  # "openai", "anthropic", ...
    model: str                                     # "gpt-4o-mini", ...
    input_messages: list[dict[str, Any]] = []
    output_text: str = ""
    tool_calls: list[dict[str, Any]] = []          # raw model-emitted tool calls
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    timestamp: str = ""
```

## `ToolCall`

```python
class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = {}
    result: Any = None
    latency_ms: float = 0.0
    error: str | None = None
```

## `GradeResult`

```python
class GradeResult(BaseModel):
    passed: bool
    grader_name: str
    reason: str = ""
    metadata: dict[str, Any] = {}
```

`grader_name` is what reporters use as the human label — keep it
descriptive (`contains('refund')`, not `<lambda>`).

## `Grader`

Type alias.

```python
Grader = Callable[[Trace], GradeResult]
```

A grader is just a function. See [Graders](./graders.md) for the ten
built-ins and patterns for custom ones.

## `run_agent(agent, *, suite_name, case_name, input_value)`

Low-level. Most users don't call this directly — `Runner` handles it.

```python
trace: Trace = run_agent(
    my_agent,
    suite_name="billing",
    case_name="happy",
    input_value="hello",
)
```

Behavior:

- Calls `agent(input_value)` inside a try/except with wall-clock timing.
- If the agent returns `(output, Trace)`, uses that trace and fills in
  suite/case/input/output.
- If the agent returns just an output, wraps it in a fresh trace with
  wall-clock latency.
- On exception, returns a trace with `error` populated.

## `Runner`

```python
class Runner:
    def __init__(self, store: BaselineStore) -> None: ...
    def record(self, suite: Suite) -> RunReport: ...
    def check(self, suite: Suite) -> RunReport: ...
```

`record` saves each trace as the baseline. `check` saves to
`runs/<timestamp>/` and compares against the baseline, producing a
`TraceDelta` per case.

```python
from agentprdiff import Runner, BaselineStore

store = BaselineStore()
runner = Runner(store)
report = runner.check(my_suite)
print(report.cases_regressed, "regressed of", report.cases_total)
```

## `RunReport`

```python
class RunReport(BaseModel):
    suite_name: str
    mode: str                              # "record" or "check"
    case_reports: list[CaseReport] = []

    @property
    def cases_passed(self) -> int: ...
    @property
    def cases_total(self) -> int: ...
    @property
    def cases_regressed(self) -> int: ...
    @property
    def has_regression(self) -> bool: ...
```

## `CaseReport`

```python
class CaseReport(BaseModel):
    suite_name: str
    case_name: str
    trace: Trace
    grader_results: list[GradeResult]
    delta: TraceDelta | None = None

    @property
    def passed(self) -> bool: ...
    @property
    def has_regression(self) -> bool: ...
```

`passed` = all graders passed *and* `trace.error is None`.
`has_regression` = `not passed` *or* `delta.has_regression`.

## `TraceDelta`

```python
class TraceDelta(BaseModel):
    suite_name: str
    case_name: str
    baseline_exists: bool
    assertion_changes: list[AssertionChange] = []
    cost_delta_usd: float = 0.0
    latency_delta_ms: float = 0.0
    prompt_tokens_delta: int = 0
    completion_tokens_delta: int = 0
    tool_sequence_changed: bool = False
    baseline_tool_sequence: list[str] = []
    current_tool_sequence: list[str] = []
    output_changed: bool = False
    output_diff: str = ""
    current_error: str | None = None
    baseline_error: str | None = None

    @property
    def regressions(self) -> list[AssertionChange]: ...
    @property
    def improvements(self) -> list[AssertionChange]: ...
    @property
    def has_regression(self) -> bool: ...
```

## `AssertionChange`

```python
class AssertionChange(BaseModel):
    grader_name: str
    baseline_passed: bool | None    # None = grader didn't exist in baseline
    current_passed: bool
    current_reason: str = ""

    @property
    def is_regression(self) -> bool: ...    # was passing/absent, now failing
    @property
    def is_improvement(self) -> bool: ...   # was failing, now passing
```

## `diff_traces(*, baseline, current, current_results, baseline_results=None)`

Build a `TraceDelta`. Used by `Runner.check`; rarely called directly.

| Param | Type | Description |
|---|---|---|
| `baseline` | `Trace \| None` | The committed baseline, if any. |
| `current` | `Trace` | The fresh run trace. |
| `current_results` | `list[GradeResult]` | Grader outcomes for the current trace. |
| `baseline_results` | `list[GradeResult] \| None` | Grader outcomes for the baseline (recommended; otherwise the differ can't compute per-assertion regressions accurately). |

## `BaselineStore`

```python
class BaselineStore:
    def __init__(self, root: Path | str = ".agentprdiff") -> None: ...

    @property
    def baselines_dir(self) -> Path: ...
    @property
    def runs_dir(self) -> Path: ...

    def baseline_path(self, suite_name: str, case_name: str) -> Path: ...
    def run_path(self, run_id: str, suite_name: str, case_name: str) -> Path: ...

    def save_baseline(self, trace: Trace) -> Path: ...
    def load_baseline(self, suite_name: str, case_name: str) -> Trace | None: ...
    def save_run_trace(self, run_id: str, trace: Trace) -> Path: ...
    def ensure_initialized(self) -> None: ...
    def fresh_run_id(self) -> str: ...    # "20260425T195727Z"
```

Subclass to back baselines with S3, GCS, or a database — see
[Customization → Plugging a custom store backend](../usage/customization.md#plugging-a-custom-store-backend).

## `agentprdiff.loader.load_suites(path)`

Import a suite file and return every module-level `Suite` it defines.
Raises `FileNotFoundError`, `IsADirectoryError`, or `ValueError` (no
suites found).

```python
from agentprdiff.loader import load_suites
suites = load_suites("suites/billing.py")
```

## `agentprdiff.filtering`

```python
from agentprdiff.filtering import Pattern, parse_patterns, apply_filter

patterns = parse_patterns(["~slow", "billing:refund*"])
narrowed = apply_filter(suites, include=patterns, exclude=[])
```

See [CLI → Filtering](./cli.md#filter-syntax).

## `agentprdiff.scaffold`

```python
from pathlib import Path
from agentprdiff.scaffold import scaffold, VALID_RECIPES

result = scaffold("billing", recipe="sync-openai", root=Path("."))
print(result.written, result.skipped)
```

`VALID_RECIPES = ("sync-openai", "async-openai", "stubbed")`.

## `agentprdiff.adapters`

See [Adapters reference](./adapters.md).

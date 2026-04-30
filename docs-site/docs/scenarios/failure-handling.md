---
id: failure-handling
title: Failure Handling
sidebar_position: 8
---

# Scenario 8 — Failure Handling

How `agentprdiff` behaves when things go wrong, and how to make those
failures useful.

## Failure 1 — The agent raises an uncaught exception

### What happens

`run_agent` wraps the agent call in a try/except. On exception:

```python
except Exception as exc:
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return Trace(
        suite_name=suite_name,
        case_name=case_name,
        input=input_value,
        output=None,
        error=f"{type(exc).__name__}: {exc}",
        total_latency_ms=elapsed_ms,
    )
```

The trace stores the exception type and message in `Trace.error`. The case
is marked as a regression (because `Trace.error` is non-null and the
baseline didn't have one). CI exits 1.

### How to make it actionable

In `record` mode, an exception sets `any_error = True` and the CLI
*also* exits 1 — so you can't accidentally commit a baseline that
contains an exception. Fix the agent first, then re-record.

### Pin failure modes intentionally

Sometimes "the agent should *politely* refuse" is the contract. Don't
let it raise:

```python
# in your agent
try:
    response = client.chat.completions.create(...)
except APIError as exc:
    return f"Sorry, our service is temporarily unavailable: {exc}"
```

```python
# in your suite
case(
    name="api_error_returns_polite_message",
    input="…",
    expect=[
        contains("temporarily unavailable"),
        latency_lt_ms(2_000),
    ],
)
```

The case now pins your fallback path.

## Failure 2 — A grader itself raises

### What happens

If a custom grader raises, the failure is wrapped into a `GradeResult`
and the case fails — it does not crash the run. Specifically, the
`semantic` grader catches judge exceptions:

```python
try:
    passed, reason = backend(rubric, trace)
except Exception as exc:
    return GradeResult(
        passed=False,
        grader_name=f"semantic({rubric!r})",
        reason=f"judge raised {type(exc).__name__}: {exc}",
    )
```

Built-in deterministic graders are written defensively and don't raise on
realistic inputs.

### Tip for custom graders

Wrap your logic in a try/except and put the exception text on the
`reason`:

```python
def my_grader(...):
    def _grader(trace):
        try:
            ...
        except Exception as exc:
            return GradeResult(
                passed=False,
                grader_name="my_grader(...)",
                reason=f"raised {type(exc).__name__}: {exc}",
            )
    return _grader
```

A grader that raises mid-run kills the case but doesn't kill the suite —
all subsequent cases still execute.

## Failure 3 — The judge is unreachable

### Symptom

Network blip, rate limit, or revoked API key. The `semantic` grader
catches it and reports `judge raised <ExceptionType>: <message>`. The
case fails. Other cases keep running.

### Mitigation

Pin a deterministic fallback when you must:

```python
from agentprdiff.graders.semantic import openai_judge, fake_judge

def resilient_judge(rubric, trace):
    try:
        return openai_judge()(rubric, trace)
    except Exception:
        return fake_judge(rubric, trace)

expect = [semantic("…", judge=resilient_judge)]
```

This trades fidelity for reliability — your CI stays green if OpenAI is
down, but you've also disabled the real judge for that case.

## Failure 4 — Filter matched zero cases

### Symptom

```bash
agentprdiff check suite.py --case '*does-not-exist*'
```

```
error: no cases matched --case/--skip filters.
available cases: ...
```

Exit code: `2`. The CI step fails *loudly* — distinct from `1` (real
regression) and `0` (clean run).

### Mitigation

`--list` first:

```bash
agentprdiff check suite.py --list
```

Or wrap the CI invocation:

```bash
agentprdiff check suite.py --list | grep -q "$CASE" || { echo "no such case"; exit 2; }
```

## Failure 5 — Baseline file is corrupt

### Symptom

Someone edited the baseline JSON by hand and broke the schema:

```
pydantic.ValidationError: 1 validation error for Trace
total_cost_usd
  Input should be a valid number ...
```

`load_baseline` raises and the runner crashes for that suite.

### Mitigation

```bash
git checkout HEAD -- .agentprdiff/baselines/<suite>/<case>.json
```

Or re-record from scratch:

```bash
rm .agentprdiff/baselines/<suite>/<case>.json
agentprdiff record suite.py --case <case>
```

Never edit baselines by hand.

## Failure 6 — Agent hangs

### Symptom

A model call hangs (network), an `await` deadlocks (asyncio bug), or a
tool spins.

### Mitigation

`agentprdiff` does not enforce a wall-clock timeout — the calling
process is responsible for one. In CI:

```bash
timeout 600 agentprdiff check suites/*.py
```

Inside your agent code, give your SDK client a short HTTP timeout:

```python
client = OpenAI(timeout=30.0)
```

Or wrap the whole agent call:

```python
import signal

class _Timeout(Exception): pass

def my_agent(query, *, timeout=20):
    def _alarm(*_): raise _Timeout("agent exceeded budget")
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(timeout)
    try:
        return _run(query)
    finally:
        signal.alarm(0)
```

`_Timeout` propagates as a normal exception — it lands on `Trace.error`
and the case fails the way any other exception would.

## Failure 7 — Network egress disabled (sandboxed CI)

### Symptom

The judge or your agent's SDK fails with a connection error. `fake_judge`
becomes the sane default:

```bash
AGENTGUARD_JUDGE=fake agentprdiff check suite.py
```

For agents that can't run without network, gate the entire suite on
`secrets.OPENAI_API_KEY` (the scaffolded workflow does this). When the
key is absent, emit a `::warning::` and exit 0.

## A failure-mode checklist

Before shipping a suite, verify all of the following are visible:

| Failure | Visible where |
|---|---|
| Agent raises | Notes column: `error: <ExceptionType>: <message>` |
| Grader fails | Notes column: `<grader_name> <reason>` |
| Judge unreachable | Notes column: `judge raised ...` |
| First-run-bad | `REGRESSION` even without baseline |
| Filter typo | Exit 2 + available case names |
| Baseline corrupt | Pydantic validation error stack trace |
| Cost / latency drift without grader | Cost Δ / Latency Δ columns (no exit-1) |
| Cost / latency drift with grader | Notes column + exit 1 |

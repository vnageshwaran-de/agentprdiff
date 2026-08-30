---
id: configuration
title: Configuration
sidebar_position: 3
---

# Configuration

`agentprdiff` is intentionally light on configuration — almost everything
that's not a CLI flag lives in your suite file or as an env var.

## CLI flags (group-level)

| Flag | Default | What it does |
|---|---|---|
| `--root PATH` | `.agentprdiff` | Where baselines and runs live. Useful in monorepos. |
| `--version` | — | Print the installed version and exit. |
| `--help` | — | Click-generated help. |

```bash
agentprdiff --root .agentprdiff/billing record suites/billing.py
```

The `--root` flag goes **before** the subcommand because it belongs to the
top-level Click group.

## CLI flags (per-command)

| Command | Flag | Default | Notes |
|---|---|---|---|
| `record`, `check`, `review` | `--case PATTERN` | — | Repeatable; comma-separated; supports globs and `~` negation; `suite:case` qualifier. |
| `record`, `check`, `review` | `--skip PATTERN` | — | Same syntax as `--case`. |
| `record`, `check`, `review` | `--list` | — | Print suite/case names without running. |
| `record`, `check` | `--json-out PATH` | — | Write a JSON report to `PATH`. Overwrites every run. |
| `check` | `--fail-on/--no-fail-on` | `--fail-on` | When `--no-fail-on`, regressions are reported but exit code stays 0. |
| `check` | `--strict-judge` | off | Exit 1 when any `semantic()` grader was judged by `fake_judge` via **silent fallback** (no judge env var, no API key). Recommended in CI; will become the default in v1.0. Explicit `AGENTPRDIFF_JUDGE=fake` still passes. |
| `check` | `--runs N` | `1` | Execute each case N times; the case passes when at least its `min_pass_rate` fraction of attempts fully pass. The flakiness guard for stochastic agents — see below. |
| `scaffold` | `--recipe {sync-openai,async-openai,stubbed}` | `sync-openai` | Picks the eval-wrapper template. |
| `scaffold` | `--dir PATH` | `.` | Project root to scaffold into. |

## Flakiness: `--runs` + `min_pass_rate`

Agents are stochastic — even at `temperature=0`, providers don't guarantee
identical outputs, so a borderline case will intermittently fail a
single-shot check. Intermittent false failures are how teams quietly stop
running a CI gate; this is the escape valve:

```python
case(
    name="refund_happy_path",
    input="I want a refund for order #1234",
    expect=[contains("refund"), tool_called("lookup_order")],
    min_pass_rate=0.6,          # at least 60 % of attempts must fully pass
)
```

```bash
agentprdiff check suite.py --runs 3    # 2-of-3 passes → case passes
```

Semantics:

- **An attempt "fully passes"** when every grader passes and the agent
  didn't raise. The case passes when
  `passed_attempts / total_attempts >= min_pass_rate`.
- **`min_pass_rate` defaults to `1.0`** — every attempt must pass, so
  behavior without `--runs` (or with `--runs 1`) is exactly the single-shot
  behavior you had before.
- **Diffs use a representative attempt**: the last fully-passing attempt
  when one exists (the behavior you're accepting), otherwise the last
  attempt (so the report shows what went wrong). The terminal report shows
  the tally, e.g. `2/3 runs passed (required ≥ 60%)`.
- **`record` always runs once** — a baseline is a single known-good trace.
- Reserve low `min_pass_rate` values for cases with a genuinely stochastic
  step. A deterministic assertion that needs `0.5` to stay green is telling
  you the assertion (or the agent) is wrong.

Cost note: `--runs 3` triples agent invocations (and judge calls for
`semantic()` graders) for every case, so scope it to the suites that need it.

## Environment variables

### Selecting the semantic-grader judge

The `semantic()` grader picks a judge in this order:

1. `AGENTPRDIFF_JUDGE=fake` → deterministic keyword matching.
2. `AGENTPRDIFF_JUDGE=openai` *or* `OPENAI_API_KEY` set → `openai_judge()`
   (default model `gpt-4o-mini`).
3. `AGENTPRDIFF_JUDGE=anthropic` *or* `ANTHROPIC_API_KEY` set →
   `anthropic_judge()` (default model `claude-haiku-4-5-20251001`).
4. Otherwise → `fake_judge` (silent fallback).

:::caution Silent fallback
Step 4 means a pipeline with no judge configured still goes green — but its
semantic assertions were graded by keyword matching, not a real judge. In CI,
run `agentprdiff check --strict-judge` to turn that configuration into a
failure instead. Strict mode will become the default in v1.0.
:::

:::note Legacy name
Before v0.5.0 this variable was called `AGENTGUARD_JUDGE`. The old name still
works and emits a `DeprecationWarning`; it will be removed in v1.0. When both
are set, `AGENTPRDIFF_JUDGE` wins.
:::

When any case in a run uses `semantic()`, the terminal reporter prints a
banner like:

```
semantic judge: openai/gpt-4o-mini (OPENAI_API_KEY set)
```

It's coloured **yellow** when `fake_judge` would be used so the silent
fallback never sneaks past code review.

### What `agentprdiff` does *not* read

The library does not look for your agent's API keys. Your agent is plain
Python; it reads whatever env vars it always read.

## Pricing tables

The OpenAI / Anthropic adapters fill in `LLMCall.cost_usd` from a per-model
price table at `agentprdiff.adapters.pricing.DEFAULT_PRICES`. Three ways to
override:

### 1. Per call

```python
from agentprdiff.adapters.openai import instrument_client

PRICES = {"my-finetune-v3": (0.0009, 0.0018)}  # ($/1k input, $/1k output)

def my_agent(query):
    client = OpenAI()
    with instrument_client(client, prices=PRICES) as trace:
        ...
```

### 2. Per process

```python
from agentprdiff.adapters import register_prices

register_prices({"my-finetune-v3": (0.0009, 0.0018)})
```

Once at import time. Subsequent `instrument_client` calls see the merged
table.

### 3. Globally

```python
import agentprdiff.adapters.pricing as p
p.DEFAULT_PRICES = {"my-finetune-v3": (0.0009, 0.0018)}
```

Replaces the bundled defaults entirely.

### Missing-model behavior

If a model isn't in the active table, `cost_usd` is recorded as `0.0` and
the adapter emits **one** `RuntimeWarning` per process per model:

```
[agentprdiff] no pricing entry for model 'foo-bar-v9'; cost_usd will be
recorded as 0.0. Pass prices={...} to instrument_client(...) or call
agentprdiff.adapters.register_prices({...}) to fix.
```

Loud-but-not-spammy. Cost-budget regressions caused by missing pricing are
visible at adoption time.

## The `.agentprdiff/` layout

```
.agentprdiff/
├── .gitignore         ← ignores runs/
├── baselines/         ← committed
│   └── <suite>/
│       └── <case>.json
└── runs/              ← gitignored
    └── 20260425T195727Z/
        └── <suite>/
            └── <case>.json
```

To use a non-default location, pass `--root` (CLI) or construct
`BaselineStore(root=...)` (library).

## Filename safety

Suite and case names are slugified for filesystem paths:

```python
def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name) or "_"
```

So `case(name="refund: happy path")` lands at
`baselines/<suite>/refund__happy_path.json`. Keep names ASCII-snake_case to
avoid surprises.

## Programmatic configuration (in your suite file)

Anything that needs a Python expression goes in `suite.py`:

```python
from agentprdiff import case, suite
from agentprdiff.graders import contains, latency_lt_ms
from agentprdiff.graders.semantic import openai_judge
from agentprdiff.adapters import register_prices

# Custom pricing for an internal fine-tune.
register_prices({"acme-llama-3-fine": (0.0003, 0.0006)})

# Pin the judge model — overrides AGENTPRDIFF_JUDGE.
JUDGE = openai_judge(model="gpt-4o-mini")

billing = suite(
    name="billing",
    agent=billing_agent,
    cases=[
        case(
            name="refund_happy_path",
            input="…",
            expect=[
                contains("refund"),
                latency_lt_ms(8_000),
                semantic("agent acknowledges the refund", judge=JUDGE),
            ],
        ),
    ],
)
```

This style keeps configuration colocated with the cases that depend on it
— easier to review, easier to grep.

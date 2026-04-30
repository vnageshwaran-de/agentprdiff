---
id: large-suites
title: Large Suites & Multi-File Agents
sidebar_position: 2
---

# Scenario 2 — Large Suites & Multi-File Agents

When you've outgrown a single suite, organize cases by behavior dossier and
run them together.

## Problem

A production billing assistant has accumulated:

- 18 happy-path refund variants,
- 6 abuse-detection cases,
- 4 escalation cases,
- 9 multilingual cases.

Putting them all in one `suite.py` is fine until the first time you want
to iterate on just the multilingual ones. We want clean per-domain files,
shared agent wiring, and a single CI invocation.

## Layout

```
my_app/
├── agent/
│   └── billing_agent.py           # production code, untouched
├── suites/
│   ├── __init__.py
│   ├── _eval_agent.py             # one-time wrapper around production agent
│   ├── _stubs.py                  # deterministic stand-ins for side effects
│   ├── refund.py                  # 18 cases
│   ├── abuse.py                   # 6 cases
│   ├── escalation.py              # 4 cases
│   ├── multilingual.py            # 9 cases
│   └── README.md                  # adoption notes
├── .agentprdiff/
│   └── baselines/                 # committed; one folder per suite
└── .github/workflows/agentprdiff.yml
```

`agentprdiff scaffold` creates exactly this layout for a new suite name:

```bash
agentprdiff scaffold refund --recipe sync-openai
```

## Code: shared wrapper

`suites/_eval_agent.py` is the only place that knows how to instrument
your production agent. Each suite imports it and reuses it.

```python title="suites/_eval_agent.py"
from agentprdiff import Trace
from agentprdiff.adapters.openai import instrument_client, instrument_tools
from openai import OpenAI

from agent.billing_agent import run_agent
from suites._stubs import STUB_TOOL_MAP

def eval_agent(query: str) -> tuple[str, Trace]:
    client = OpenAI()
    with instrument_client(client) as trace:
        tools = instrument_tools(STUB_TOOL_MAP, trace)
        output = run_agent(query, client=client, tools=tools)
        return output, trace
```

## Code: per-domain suite

```python title="suites/refund.py"
from agentprdiff import case, suite
from agentprdiff.graders import contains, cost_lt_usd, latency_lt_ms, tool_called
from suites._eval_agent import eval_agent

refund_suite = suite(
    name="refund",
    agent=eval_agent,
    cases=[
        case(name="basic_refund_card",       input="…", expect=[contains("refund"), tool_called("lookup_order"), cost_lt_usd(0.02)]),
        case(name="basic_refund_paypal",     input="…", expect=[contains("refund"), tool_called("lookup_order"), cost_lt_usd(0.02)]),
        case(name="partial_refund",          input="…", expect=[contains("partial"), tool_called("calc_partial"), cost_lt_usd(0.02)]),
        case(name="refund_after_30_days",    input="…", expect=[contains("policy"), latency_lt_ms(8_000)]),
        # ...14 more refund variants...
    ],
)
```

## Run all suites in one CI step

```bash
agentprdiff check suites/*.py --json-out artifacts/agentprdiff.json
```

## Iterate on one domain locally

```bash
# Discover what's in the multilingual suite.
agentprdiff check suites/multilingual.py --list

# Run just the German cases.
agentprdiff check suites/multilingual.py --case "*_de_*"

# Live iteration on a single case.
agentprdiff review suites/multilingual.py --case basic_refund_de
```

## Multiple suites, multiple agents, one check

A single suite file can declare more than one `Suite`:

```python title="suites/orchestration.py"
from agentprdiff import case, suite
from agentprdiff.graders import contains, tool_called

billing = suite(name="billing", agent=billing_agent, cases=[...])
triage  = suite(name="triage",  agent=triage_agent,  cases=[...])
escalation = suite(name="escalation", agent=escalation_agent, cases=[...])
```

`agentprdiff check suites/orchestration.py` prints three tables, one per
suite, and aggregates the exit code.

## Expected output (truncated)

```
agentprdiff check — suite refund  (18/18 passed, 0 regressed)
…(table)…
agentprdiff check — suite abuse  (6/6 passed, 0 regressed)
…(table)…
agentprdiff check — suite escalation  (4/4 passed, 0 regressed)
…(table)…
agentprdiff check — suite multilingual  (9/9 passed, 0 regressed)
…(table)…
✓ no regressions.
```

## Explanation

- The runner doesn't care how many suites a file defines. The loader walks
  module-level `Suite` instances and runs each independently.
- Per-suite files keep cases discoverable in your editor and make
  PR-scoped reviews trivial — a code reviewer sees `suites/refund.py` in
  the diff and knows to look at `.agentprdiff/baselines/refund/` next to
  it.
- Sharing `_eval_agent.py` is what makes per-domain files cheap to add.
- Globbing `suites/*.py` from CI keeps the workflow YAML stable as you add
  files.

## Tips for very large suites

- **Tag slow cases.** `case(name="...", tags=["slow"])`. Then
  `agentprdiff check --skip slow` for the fast PR gate, and a separate
  nightly job for the full set.
- **Use `--case` filtering in PR descriptions** so a maintainer can
  reproduce a specific failure with one copy/paste.
- **Split by latency budget.** Cases that legitimately need 30s of
  multi-turn agent loops shouldn't be alongside <1s cases — separate
  suites means separate `.agentprdiff/baselines/` folders and faster
  re-records.

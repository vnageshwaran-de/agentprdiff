---
id: quickstart
title: Quickstart
sidebar_position: 3
---

# Quickstart

Two paths to first-green CI. Pick the one that fits.

| Path | When to pick it | Time |
|---|---|---|
| **A. AI-agent driven** *(recommended)* | You're using Claude Code, Cursor, Aider, Copilot, or any agentic IDE. The agent reads `AGENTS.md`, finds your production agent, proposes cases, and writes the whole suite for you. | ~15-20 min |
| **B. Manual** | You'd rather write the suite yourself, or you don't have an AI assistant set up. | ~30-60 min |

Both paths produce the same files and the same green CI gate. Path A is faster and is the way most adopters bootstrap; Path B is what's underneath the hood.

---

## Path A — Let an AI agent adopt the package for you (recommended) { #path-a }

The killer feature most people miss: **you don't write the suite by hand.**
`agentprdiff` ships an [`AGENTS.md`](https://github.com/vnageshwaran-de/agentprdiff/blob/main/AGENTS.md)
playbook designed to be consumed by AI coding assistants. Point Claude
Code, Cursor, Aider, Copilot — anything agentic — at it, and the
assistant will:

1. Find the production LLM agent in your repo.
2. Propose 5-10 case contracts and ask you to confirm them.
3. Generate `suites/<project>.py`, `_eval_agent.py`, `_stubs.py`, the
   case dossier, and a CI workflow.
4. Run `agentprdiff record` to capture baselines.
5. Hand you a green build to commit.

You stay in the loop at exactly two checkpoints (the production agent it
found, and the proposed cases) and approve the rest.

### Step 1 — install

```bash
pip install agentprdiff
```

### Step 2 — open your AI assistant in your project

Claude Code, Cursor, Aider, Copilot Chat — any assistant that can read
files and run commands. Make sure it's pointed at the project root of
the agent you want to test.

### Step 3 — paste the adoption prompt

Copy this prompt verbatim, paste into your AI assistant, hit enter:

```text
Add agentprdiff to this repo. Follow AGENTS.md.

Before writing any code, do these in order:

1. Find the agent (Step 1 in AGENTS.md). Tell me the entry function,
   the system prompt, and the tool dispatch dict so I can confirm you
   found the right things.

2. Propose 5-10 cases (Step 2). For each case, write one sentence
   that names: the input, what the agent must do, what it must NOT
   do, and a rough cost/latency budget. Show me the list before
   writing the suite.

3. Once I approve the case list, execute Steps 3 through 7. Use the
   OpenAI-compatible adapter unless the agent visibly uses native
   Anthropic. Stub any tool with side effects.

4. After `agentprdiff record`, tell me explicitly whether any case
   failed during recording — those are real findings, not test bugs.

Don't modify any production code. The whole integration goes in a
new suites/ folder.
```

> If your assistant doesn't auto-fetch URLs, prepend:
>
> ```text
> First, fetch and read https://github.com/vnageshwaran-de/agentprdiff/blob/main/AGENTS.md
> into context. Acknowledge when done, then continue.
> ```

### Step 4 — review the two checkpoints

The assistant pauses twice:

- **Checkpoint 1: "I found the agent here."** Confirm the file paths
  and entry function look right. Adjust if it picked the wrong module.
- **Checkpoint 2: "Here are the proposed cases."** Read the table.
  Add cases for behaviors you care about; remove cases that test things
  you genuinely don't care about. This is the most important 3 minutes
  of the whole adoption.

### Step 5 — commit the result

After the assistant finishes, you'll have:

```
suites/<project>.py            # the suite definition
suites/<project>_cases.md      # human-readable case dossier
suites/_eval_agent.py          # eval-mode wrapper
suites/_stubs.py               # deterministic stubs for side-effecting tools
.agentprdiff/baselines/...     # the recorded JSON baselines
.github/workflows/agentprdiff.yml  # CI workflow
```

```bash
git add suites/ .agentprdiff/baselines/ .github/workflows/agentprdiff.yml
git commit -m "Add agentprdiff regression suite"
git push
```

That's it. The CI workflow runs on every PR; baselines are committed so
reviewers see behavior changes in the diff alongside code changes.

> **Three adoption-prompt levels** (minimum, recommended, contract-driven)
> are documented at [docs/ai-driven-adoption.md](https://github.com/vnageshwaran-de/agentprdiff/blob/main/docs/ai-driven-adoption.md)
> on the repo. The prompt above is "Level 2 — recommended."

### Why this works

`AGENTS.md` is roughly 700 lines of dense, grep-friendly instructions
written *for* AI assistants — what to look for in the codebase, what
files to produce, what shape they should have, what mistakes to avoid.
Pinning it at the start of the session is what stops the assistant from
hallucinating the API based on stale training data.

The output is identical to what you'd write by hand following Path B —
just much faster, with case suggestions you might not have thought of.

---

## Path B — Write the suite yourself { #path-b }

If you don't have an AI assistant handy, or you want to learn the shape
of a suite before delegating it, here's the by-hand version.

### 1. Install

```bash
pip install agentprdiff
```

### 2. Initialize the project

From the repository root of the agent you want to test:

```bash
agentprdiff init
```

This creates a `.agentprdiff/` directory:

```
.agentprdiff/
├── baselines/   # commit this directory
├── runs/        # gitignored automatically
└── .gitignore   # ignores runs/ for you
```

### 3. Wrap your agent

Your agent is any callable `(input) -> output` *or*
`(input) -> (output, Trace)`. The trace flavour unlocks tool / cost /
latency assertions.

```python title="my_agent.py"
from agentprdiff import LLMCall, ToolCall, Trace

def my_agent(query: str) -> tuple[str, Trace]:
    trace = Trace(suite_name="", case_name="", input=query)

    # ── pretend we called an LLM ───────────────────────────────────────────
    trace.record_llm_call(LLMCall(
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_messages=[{"role": "user", "content": query}],
        output_text="Looking up your order…",
        prompt_tokens=18, completion_tokens=12,
        cost_usd=0.0002, latency_ms=180.0,
    ))

    # ── pretend we called a tool ───────────────────────────────────────────
    trace.record_tool_call(ToolCall(
        name="lookup_order",
        arguments={"order_id": "1234"},
        result={"status": "delivered", "amount_usd": 89.0},
        latency_ms=8.0,
    ))

    final_text = "Refund of $89.00 processed; expect it in 3-5 business days."
    return final_text, trace
```

> Already on the OpenAI or Anthropic Python SDK? Skip the manual wiring
> and use the [SDK adapters](./api/adapters.md) — one `with` block records
> every model and tool call automatically.

### 4. Write a suite

```python title="suite.py"
from agentprdiff import case, suite
from agentprdiff.graders import (
    contains, cost_lt_usd, latency_lt_ms, semantic, tool_called,
)
from my_agent import my_agent

billing = suite(
    name="billing",
    agent=my_agent,
    cases=[
        case(
            name="refund_happy_path",
            input="I want a refund for order #1234",
            expect=[
                contains("refund"),
                tool_called("lookup_order"),
                semantic("agent acknowledges the refund and explains the timeline"),
                latency_lt_ms(10_000),
                cost_lt_usd(0.02),
            ],
        ),
    ],
)
```

> **Don't want to write this from scratch?** Run
> `agentprdiff scaffold <project_name> --recipe sync-openai` to stamp out
> the canonical layout — `suites/__init__.py`, `_eval_agent.py`,
> `_stubs.py`, `<name>.py`, `<name>_cases.md`, `suites/README.md`, plus
> `.github/workflows/agentprdiff.yml` — with `TODO:` markers where you
> wire in your specific agent. Existing files are never overwritten.

### 5. Record the baseline

```bash
agentprdiff record suite.py
```

Output:

```
agentprdiff record — suite billing  (1/1 passed, 0 regressed)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━┓
┃ Case               ┃ Result ┃ Cost Δ ┃ Latency ┃ Notes ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━┩
│ refund_happy_path  │ PASS   │        │         │  —    │
└────────────────────┴────────┴────────┴─────────┴───────┘
```

A new file appears at `.agentprdiff/baselines/billing/refund_happy_path.json`.
**Commit it.** Reviewers will see future trace changes as ordinary git
diffs in your PR.

### 6. Check on every PR

```bash
agentprdiff check suite.py
```

- Exit `0` when every case still matches its baseline.
- Exit `1` on any regression — pass→fail flip, new exception, or missing
  baseline + failing assertion.

### 7. Wire it into CI

```yaml title=".github/workflows/agents.yml"
name: agent-regression
on: [pull_request]
permissions:
  contents: read
jobs:
  agentprdiff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e ".[dev]"
      - run: agentprdiff check suites/*.py --json-out artifacts/agentprdiff.json
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: agentprdiff, path: artifacts/ }
```

That's it. Five commands, one Python file, zero framework lock-in.

---

## Where next

- [Core concepts](./concepts.md) — how the pieces fit together.
- [Usage guide](./usage/basic.md) — patterns for real agents.
- [Scenarios](./scenarios/simple-suite.md) — runnable end-to-end examples.
- [`AGENTS.md`](https://github.com/vnageshwaran-de/agentprdiff/blob/main/AGENTS.md) — the full AI-agent adoption playbook (read this if you're building a custom adoption prompt).

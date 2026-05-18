# Customer Support Agent — agentprdiff Video Tutorial

A **LangGraph ReAct agent** paired with **agentprdiff** snapshot tests to demonstrate
how to catch behavioral regressions when models, prompts, or tools change.

> **Tutorial narrative:** Build → Record → Break → Catch → Fix

---

## What you'll build

| File | Purpose |
|---|---|
| `agent.py` | LangGraph ReAct agent with 3 tools: `lookup_order`, `process_refund`, `check_policy` |
| `suite.py` | 12 agentprdiff test cases across 5 suites covering all 10 built-in graders |
| `AGENTS.md` | Persistent instructions for AI coding agents working in this repo |
| `.github/workflows/agentprdiff.yml` | CI: runs `agentprdiff check` on every PR |

---

## Prerequisites

- Python 3.11+
- An Anthropic API key (`ANTHROPIC_API_KEY`)

---

## Quick start

```bash
# 1. Enter the project
cd video-tutorials/customer_support_agent

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set your API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# 4. Smoke test the agent manually
python agent.py

# 5. Record baselines (run once on the known-good version)
agentprdiff record suite.py

# 6. Check for regressions
agentprdiff check suite.py
```

---

## Tutorial walkthrough

### Step 1 — Explore the agent

Open `agent.py`. The agent is a standard LangGraph `StateGraph` with three nodes:

```
HumanMessage → [agent] → [tools] → [agent] → ... → AIMessage
```

Three mock tools simulate a real backend (no real database needed):

- **`lookup_order(order_id)`** — returns order status, item, category, amount
- **`process_refund(order_id, reason)`** — approves refunds on delivered orders only
- **`check_policy(category)`** — returns the return/refund policy for an item category

### Step 2 — Run the agent manually

```bash
python agent.py
```

You'll see three queries answered: order status, refund request, and policy lookup.

### Step 3 — Understand the test suite

Open `suite.py`. It contains **5 suites** covering distinct behavior categories:

| Suite | Cases | Key graders demonstrated |
|---|---|---|
| `refund_flow` | 3 | `tool_sequence`, `no_tool_called`, `regex_match`, `semantic` |
| `policy_queries` | 3 | `tool_called`, `output_length_lt`, `contains_any` |
| `order_status` | 2 | `no_tool_called` (agent doesn't over-call) |
| `multi_step_reasoning` | 2 | `tool_sequence` (3 steps), `cost_lt_usd` |
| `out_of_scope` | 2 | `no_tool_called` (all tools), graceful fallback |

All 10 agentprdiff graders are used:
`contains` · `contains_any` · `regex_match` · `tool_called` · `tool_sequence` ·
`no_tool_called` · `output_length_lt` · `latency_lt_ms` · `cost_lt_usd` · `semantic`

### Step 4 — Record baselines

```bash
agentprdiff record suite.py
```

This runs every case once and writes JSON snapshots to `.agentprdiff/baselines/`.
Commit these files — they are the "known good" reference for CI.

```bash
git add .agentprdiff/baselines/
git commit -m "chore: record initial agentprdiff baselines"
```

### Step 5 — Introduce a regression (the "aha!" moment)

Swap the model to an older, less capable one:

```bash
export ANTHROPIC_MODEL=claude-3-haiku-20240307
```

Now run the check:

```bash
agentprdiff check suite.py
```

You'll see failures like:

```
FAIL  refund_flow / refund_happy_path
  tool_sequence(["lookup_order", "process_refund"]) — FAILED
    actual sequence: ["lookup_order"]   ← haiku skipped the refund step

FAIL  multi_step_reasoning / full_refund_journey
  semantic(...) — FAILED
    judge: "agent acknowledged the issue but did not process the refund"
```

This is the core value of agentprdiff: **a model swap that looks safe silently changes behavior**.

### Step 6 — Fix or re-record

**Option A — Fix the regression** (revert the model swap):

```bash
export ANTHROPIC_MODEL=claude-3-5-haiku-20241022
agentprdiff check suite.py   # passes again
```

**Option B — Accept the new behavior** (intentional change):

```bash
agentprdiff record suite.py
git add .agentprdiff/baselines/
git commit -m "chore: update baselines after model change"
# Write a ## Behavior Change section in your PR description
```

### Step 7 — CI enforces it forever

Every PR that touches this directory triggers the GitHub Actions workflow.
If `agentprdiff check` exits non-zero, the PR is blocked. Reviewers see
the baseline diff in the uploaded artifact.

---

## All 10 graders — quick reference

| Grader | What it checks | Example in suite.py |
|---|---|---|
| `contains(text)` | Output contains substring | `contains("refund")` |
| `contains_any([...])` | Output contains at least one substring | `contains_any(["30 days", "30-day"])` |
| `regex_match(pattern)` | Output matches regex | `regex_match(r"REF-\d+")` |
| `tool_called(name)` | Tool was called at least once | `tool_called("lookup_order")` |
| `tool_sequence([...])` | Tools were called in this exact order | `tool_sequence(["lookup_order", "process_refund"])` |
| `no_tool_called(name)` | Tool was never called | `no_tool_called("process_refund")` |
| `output_length_lt(n)` | Output is fewer than n characters | `output_length_lt(400)` |
| `latency_lt_ms(ms)` | End-to-end latency under budget | `latency_lt_ms(10_000)` |
| `cost_lt_usd(usd)` | Token cost under budget | `cost_lt_usd(0.05)` |
| `semantic(description)` | LLM-as-judge checks intent | `semantic("agent confirms refund approved")` |

---

## Project conventions

See `AGENTS.md` for the full set of rules AI coding agents must follow when
modifying this project — including when to re-record baselines, code style,
and what they must never touch.

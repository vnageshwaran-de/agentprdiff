# Regression tour

A complete walkthrough of every grader and every failure mode in `agentprdiff`. Runs without API keys (uses `fake_judge` for the semantic grader so no OpenAI/Anthropic key is required).

## What this exercises

- All 10 deterministic and semantic graders: `contains`, `contains_any`, `regex_match`, `tool_called`, `tool_sequence`, `no_tool_called`, `output_length_lt`, `latency_lt_ms`, `cost_lt_usd`, `semantic`.
- All 6 regression scenarios the differ can detect: output drift, extra tool, missing tool, tool reordering, latency regression, cost regression.
- The Rich terminal reporter and exit-code behavior used in CI.

## A note on invocation

The commands below use `agentprdiff` directly. If pip's user-script directory isn't on your PATH, substitute `python3 -m agentprdiff.cli` everywhere — both forms are equivalent.

## Setup (one-time)

```bash
cd examples/regression-tour
agentprdiff init                    # creates the .agentprdiff/ scaffolding
agentprdiff record suite.py         # captures the baseline trace
```

You should see baselines written under `.agentprdiff/baselines/`. In a real project these get committed to git — that's the whole point of agentprdiff.

## Happy path

```bash
agentprdiff check suite.py
echo "exit: $?"                     # 0
```

All three cases pass with no diff against the recorded baseline.

## Regression scenarios

Each command below injects one specific regression by setting `MODE`. Every one should fail with a clear diff and a non-zero exit code.

### 1. Output text drifted

```bash
MODE=output_changed agentprdiff check suite.py
echo "exit: $?"                     # non-zero
```

The agent's refund response changes from the baseline phrasing to "Refund initiated. Please allow 7–10 business days for processing." That trips:

- `contains("refund")` — still passes (the new text mentions refund)
- `contains_any(["business days", "card", "processed"])` — still passes
- `regex_match(r"\$\d+\.\d{2}")` — **fails** (no dollar amount in the new output)
- `output_length_lt(500)` — still passes
- `semantic(...)` — likely **fails** depending on judge backend

The terminal reporter prints a unified output diff so the reviewer can see exactly what changed.

### 2. Extra tool call

```bash
MODE=tool_added agentprdiff check suite.py
echo "exit: $?"                     # non-zero
```

The agent calls `check_inventory` after `lookup_order`. That trips:

- `tool_sequence(["lookup_order"])` — **fails** (sequence is now `["lookup_order", "check_inventory"]`)
- `no_tool_called("check_inventory")` — **fails**

### 3. Missing tool call

```bash
MODE=tool_removed agentprdiff check suite.py
echo "exit: $?"                     # non-zero
```

The agent never calls `lookup_order` and produces a fallback "trouble looking up your order" response. That trips:

- `tool_called("lookup_order")` — **fails**
- `tool_sequence(["lookup_order"])` — **fails**
- `contains("refund")` — **fails** (output text changed)
- `regex_match(r"\$\d+\.\d{2}")` — **fails**

### 4. Tool order swapped

```bash
MODE=tool_reordered agentprdiff check suite.py
echo "exit: $?"                     # non-zero
```

The agent calls `check_inventory` *before* `lookup_order`. Same tools, wrong order:

- `tool_sequence(["lookup_order"])` — **fails** (sequence is `["check_inventory", "lookup_order"]`)
- `no_tool_called("check_inventory")` — **fails**

### 5. Latency regression

```bash
MODE=latency_regressed agentprdiff check suite.py
echo "exit: $?"                     # non-zero
```

Planner LLM call jumps from 180 ms to 8 s, blowing past the 5 s cap:

- `latency_lt_ms(5_000)` — **fails**

The reporter shows the latency delta against baseline.

### 6. Cost regression

```bash
MODE=cost_regressed agentprdiff check suite.py
echo "exit: $?"                     # non-zero
```

Responder cost jumps from $0.0008 to $0.10 per call:

- `cost_lt_usd(0.01)` — **fails**

The reporter shows the cost delta against baseline.

## Run them all back to back

```bash
./tour.sh
```

Runs every scenario with banners, prints exit codes, and gives you a one-screen overview of what the tool detects.

## Resetting the baseline

If you want to make the new behavior the new baseline (e.g., you intentionally changed the agent), re-record:

```bash
MODE=output_changed agentprdiff record suite.py     # baseline now matches the changed output
agentprdiff check suite.py                          # passes against the new baseline
```

This is the workflow you'll use in real projects when a model upgrade or prompt change is intentional.

## Why this matters

The point of `agentprdiff` is that every one of these scenarios should be caught **before** the change reaches production. Run `agentprdiff check` in CI on every PR and the merge is blocked when behavior changes — same as `pytest` blocks merges when tests break.

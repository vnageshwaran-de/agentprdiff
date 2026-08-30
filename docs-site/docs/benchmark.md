---
id: benchmark
title: "The Benchmark: Sonnet → Haiku"
sidebar_position: 5
---

# We swapped Claude Sonnet for Haiku under two agents

**Every answer was right — and it still would have broken production.**

*By Vinoth Nageshwaran. Reproducible: every number below comes from
[`benchmark/`](https://github.com/vnageshwaran-de/agentprdiff/tree/main/benchmark)
in the agentprdiff repo — clone it, add an API key, and re-run it yourself.*

## The experiment

The most common "harmless" change teams make to an LLM agent is swapping the
model under it — usually downward, to save money. We wanted to measure what
that actually does to agent *behavior*, not benchmark scores.

So we built two small but realistic agents:

- a **customer-support agent** with two tools (`lookup_order`,
  `issue_refund`) and a behavioral contract: look orders up before answering,
  only refund refund-eligible orders, state the refund timeline, stay on
  topic;
- a **structured-extraction agent**: messy customer email in, strict JSON
  out — `{"customer_name", "order_id", "issue_type"}` — with an explicit
  instruction: *"Respond with ONLY a JSON object — no prose, no code
  fences."*

We wrote their behavioral contracts as [agentprdiff](https://agentprdiff.dev)
suites — 7 cases, 27 assertions, **all deterministic**: substring, regex,
tool-call, and length checks. No LLM-as-judge anywhere, so every regression
reported here is mechanically verifiable by reading the trace.

Then we recorded baselines on `claude-sonnet-4-6`, swapped in
`claude-haiku-4-5` (the natural cost downgrade), and ran `agentprdiff check`.

## What happened

**3 of 7 cases regressed. All three were the same failure — and it wasn't
intelligence.**

Haiku got every answer *right*. It looked up the correct orders. It held the
refund-eligibility guardrail — refused the out-of-window refund, cited the
30-day policy, and never called `issue_refund` on an ineligible order. On the
extraction task it even nailed the hardest case: an email mentioning two
orders where it had to pick the one the customer was acting on.

And then it wrapped every JSON response in markdown fences:

````text
```json
{
  "customer_name": "Dana Whitfield",
  "order_id": "2077",
  "issue_type": "refund"
}
```
````

— despite the prompt explicitly saying *no code fences*. Sonnet followed
that instruction; Haiku didn't, on all three extraction cases, across two
independent runs.

If your pipeline feeds that output to `json.loads`, every extraction request
now throws. The model got smarter answers per dollar and your service went
down anyway — not because the model is worse at the task, but because it's
worse at *obeying the format contract*. That distinction is invisible in
model benchmarks and instantly visible in a behavioral diff:

| Suite | Case | Assertion | Why it now fails |
|---|---|---|---|
| extraction | simple_refund_email | `bare-json-only` | no match for `\A\s*\{[\s\S]*\}\s*\Z` |
| extraction | two_orders_needle_pick | `bare-json-only` | no match for `\A\s*\{[\s\S]*\}\s*\Z` |
| extraction | status_email | `bare-json-only` | no match for `\A\s*\{[\s\S]*\}\s*\Z` |

## The false positive we hit (and why it's in this write-up)

Our first run reported a fourth regression: the support agent's
refusal-phrasing assertion failed on Haiku. Reading the trace showed Haiku
had behaved correctly — *"your order isn't eligible for a refund… our 30-day
refund window has passed"* — but our grader's word list expected "not
eligible" or "past" and matched neither phrasing.

That's an assertion bug, not a model regression. We fixed the grader,
re-recorded, and re-ran; the case went green. We're including this because
it's the honest shape of behavioral testing: some flips are real, some are
your assertions being too literal, and the diff-plus-trace is what lets you
tell them apart in about a minute. (It's also why agentprdiff's diffs cite
the exact failing reason, and why graders take a stable `id=` so fixing
their arguments doesn't cascade into more false diffs.)

## Why this matters

- **"Same task, cheaper model" is a behavioral change**, even when every
  answer stays correct. Format discipline, tool discipline, and
  instruction-following degrade independently of accuracy.
- **Model benchmarks won't warn you.** No leaderboard measures "wraps JSON
  in fences against instructions." Your agent's own behavioral contract is
  the only benchmark that predicts your production incidents.
- **The check is cheap.** These suites run in seconds, cost pennies, and are
  deterministic — they fit in per-PR CI, unlike judge-based eval pipelines.

## Reproduce it

```bash
git clone https://github.com/vnageshwaran-de/agentprdiff
cd agentprdiff && pip install -e ".[anthropic]"
export ANTHROPIC_API_KEY=...
python benchmark/run_benchmark.py --scenarios anthropic-downgrade
```

The repo's [`benchmark/README.md`](https://github.com/vnageshwaran-de/agentprdiff/tree/main/benchmark)
covers the full matrix (OpenAI downgrades, cross-vendor swaps, and a
"harmless prompt cleanup" scenario) plus methodology notes and caveats —
including the ones that cut against us.

agentprdiff is MIT-licensed: snapshot tests for LLM agent behavior,
committed to git, diffed in CI, posted on your PRs by the
[GitHub Action](https://agentprdiff.dev/scenarios/ci-cd/).
`pip install agentprdiff`.

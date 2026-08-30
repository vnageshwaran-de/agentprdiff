---
id: roadmap
title: Roadmap
sidebar_position: 10
---

# Roadmap

`agentprdiff` is at **0.5.x**. The core model, CLI, OpenAI / Anthropic
SDK adapters, multi-run flakiness handling, parallel execution, and the
GitHub Action are stable.

## Recently shipped (0.5.0)

- `check --strict-judge` — a silently-degraded semantic judge fails CI.
- Stable assertion identity (`id=` on every grader factory).
- Frozen baselines — grader verdicts persisted at `record` time; no more
  judge calls against baselines during `check`.
- Multi-run flakiness handling (`--runs N` + per-case `min_pass_rate`).
- Parallel case execution (`--concurrency N`).
- Native `async def` agent support — no `asyncio.run` bridge needed.
- The official [GitHub Action](./scenarios/ci-cd.md) with PR-comment
  behavioral diffs.
- The reproducible [model-swap benchmark](./benchmark.md).

## Next up

- **Latency tolerance relative to baseline.** `latency_lt_ms` is an
  absolute budget, but CI runners are slower than the laptop that
  recorded the baseline; a relative multiplier ("fail if > 2× baseline")
  removes an environment-sensitivity false-positive class.
- **Async Anthropic adapter.** Today the Anthropic adapter is sync only.
  Mirror the OpenAI sync/async detection so `AsyncAnthropic` clients
  work with the same `instrument_client` API.
- **LangChain / LangGraph adapter.** A `with instrument_runnable(chain)`
  context manager that records every chain / tool / LLM call as
  `LLMCall` / `ToolCall` entries.
- **Vercel AI SDK companion.** A small JS package that produces the same
  baseline JSON format from `ai/sdk` agents, so JS- and Python-shop
  users share a CI gate.
- **Tag-based filtering.** Today `--case` / `--skip` match case names;
  add `--tag smoke` / `--no-tag slow` for cases marked with `tags=[...]`.

## Under consideration

- **Streaming reporter.** Print case results as they finish instead of
  buffering until the end. Important for long suites where the user
  wants early signal.
- **GitHub annotations reporter.** Surface regressions as
  `::error file=...,line=...` annotations on the PR diff so the failing
  case shows up next to the line that changed.
- **JUnit XML reporter.** First-class CI integration with test-result
  aggregators that already understand JUnit.
- **Bedrock / Vertex AI adapters.** Both have their own response shapes;
  workable today via manual instrumentation, but a first-class adapter
  would be welcome.
- **Cost-budget *delta* graders.** Today `cost_lt_usd(0.02)` is an
  absolute ceiling. Adding `cost_increase_lt_pct(20)` would flag a 20%
  jump even if the absolute number is still under the ceiling.
- **Pluggable input fixtures.** A `case(input=fixture("orders.csv:row=4"))`
  shape so cases can refer to large structured inputs without inlining
  them.
- **Replay mode.** Re-run a recorded baseline through the differ without
  invoking the agent — useful for regression tests of `agentprdiff` itself
  and for benchmark drift detection.

## Out of scope

- A hosted SaaS. The point of committed baselines is that the diff lives
  next to the code; a hosted store undoes that.
- A new agent framework. `agentprdiff` deliberately does not care how
  your agent is built.
- Pairwise / ELO evaluation. Different problem.
- Auto-merging baseline updates. The PR diff in
  `.agentprdiff/baselines/` is the review surface — automating it away
  defeats the whole point.

## How to weigh in

- Open an issue with the `proposal` label.
- Reference real adoption pain — the more concrete the better.
- A working PR is the strongest argument.

The maintainer is one person; bandwidth is finite. Small, focused PRs
that fit the [Contributing scope](./contributing.md#scope) merge fastest.

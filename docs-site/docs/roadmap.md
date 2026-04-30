---
id: roadmap
title: Roadmap
sidebar_position: 10
---

# Roadmap

`agentprdiff` is **alpha** (0.2.x). The core model, CLI, and OpenAI /
Anthropic SDK adapters are stable. The OpenAI adapter covers both sync
`OpenAI` and async `AsyncOpenAI` clients via the same
`instrument_client` context manager.

## On the 0.3 roadmap

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

- **Parallel case execution.** Run cases within a suite concurrently
  with a thread or process pool. Useful for suites where each case is
  bottlenecked on network latency.
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

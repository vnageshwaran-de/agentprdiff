---
id: comparison
title: How It Compares
sidebar_position: 8
---

# How agentprdiff compares

The LLM-evaluation space is crowded, and several of the tools in it are
excellent. This page is an honest map: what agentprdiff does that they
don't, what they do that agentprdiff doesn't, and when you should use
them instead. Claims here reflect the tools as of this writing — check
their docs for current capabilities.

## The one-sentence difference

Most eval tools answer **"how good is my agent?"** with scores on a
dashboard. agentprdiff answers **"what did my agent stop doing since the
last known-good commit?"** with a diff in the pull request. Scores need
interpretation; diffs demand action.

## At a glance

| | agentprdiff | promptfoo | DeepEval | Braintrust |
|---|---|---|---|---|
| What it is | Snapshot tests for agent *behavior* | Prompt/output eval runner | pytest-style metric framework | Hosted eval platform |
| Open source | MIT | MIT | Apache 2.0 (cloud: Confident AI) | SaaS (free tier) |
| Where results live | **JSON baselines committed to your repo** | Config + web viewer / CI output | Test results / cloud dashboard | Their platform |
| Regression unit | Assertion flips vs a git baseline, per case | Score deltas between runs | Metric thresholds per test | Score thresholds vs experiment history |
| Agent-trace assertions (tool calls, sequences) | **Built-in graders** (`tool_called`, `tool_sequence`, …) | Partial (provider-dependent) | Agent metrics (judge-scored) | Via custom scorers |
| Judge dependence | **Optional** — 9 of 10 graders deterministic; `--strict-judge` refuses silent judge downgrade | Mixed (asserts + judges) | Judge-centric metrics | Judge/scorer-centric |
| CI gate | Exit 1 + [GitHub Action](./scenarios/ci-cd.md) posting the behavioral diff as a PR comment | CI-runnable | pytest exit codes | GitHub Action with merge blocking |
| Reviewer artifact | The baseline diff **in the PR itself** | Web UI / terminal | Test log / dashboard | Dashboard links |
| Account required | No | No (cloud optional) | No (cloud optional) | Yes |

## When to use them instead

**Use [promptfoo](https://promptfoo.dev)** when the thing under test is a
*prompt*, not an agent: comparing many prompt × model combinations side
by side, red-teaming, or jailbreak scanning. Its matrix-eval UX is more
mature than anything agentprdiff offers, and its red-team tooling has no
counterpart here at all. Where agentprdiff differs: baselines are
committed and diffed per-PR (promptfoo compares runs, but the
known-good reference isn't a git artifact your reviewers see in the
diff), and trace-level assertions — *which tools fired, in what order* —
are first-class.

**Use [DeepEval](https://deepeval.com)** when you want research-grade
*metrics* — hallucination, faithfulness, RAG relevancy — inside pytest.
Its metric library is far larger than agentprdiff's ten graders. Where
agentprdiff differs: DeepEval's metrics are predominantly LLM-judged
(with the cost and nondeterminism that implies per CI run), and its
open-source form doesn't keep a persistent known-good baseline across
runs — each run scores fresh. agentprdiff is the inverse: deterministic
first, judge optional, baseline permanent.

**Use [Braintrust](https://braintrust.dev)** (or LangSmith / Langfuse /
Arize Phoenix) when you need the full lifecycle: production
observability, dataset curation, experiment history, team dashboards.
These platforms do far more than agentprdiff ever will — that's their
job. Where agentprdiff differs: no account, no data leaving your repo,
free at any scale, and the review artifact is a git diff rather than a
dashboard link. Teams commonly run both: a platform for observability,
agentprdiff as the free per-PR behavioral gate.

**Use [RAGAS](https://docs.ragas.io)** for reference-free RAG-pipeline
scoring specifically; it's a metrics library, not a regression harness,
and pairs fine with agentprdiff.

## What agentprdiff deliberately doesn't do

No hosted dashboard, no dataset management, no production tracing, no
prompt playground, no red-teaming, no ELO/pairwise ranking. The
[roadmap](./roadmap.md) keeps it that way — the project's bet is that a
narrow, free, deterministic CI gate that reviewers see *inside the PR*
is worth more than a broader platform you have to adopt.

## Not to be confused with "Agent Diff"

There's a separate project called **Agent Diff** (`agent-diff` on PyPI,
agentdiff.dev) — sandboxed replicas of third-party APIs (Slack, Linear,
Box, Google Calendar) for evaluating and RL-training agents against
simulated services. Different tool, different job: Agent Diff gives you
*environments* to run agents in; agentprdiff gives you *regression
tests* for the agent you already run in your own environment. The names
are close; the products aren't. (If you're building suites for an agent
that talks to those APIs, they even combine: run your agent against an
Agent Diff sandbox inside an agentprdiff case.)

## The evidence

The claims above are testable. The
[benchmark](./benchmark.md) is a reproducible experiment — a model
downgrade caught breaking an agent's format contract while every answer
stayed correct, with all-deterministic assertions — that you can re-run
for a few cents.

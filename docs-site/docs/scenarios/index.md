---
id: scenarios-index
title: Scenarios Overview
sidebar_position: 0
---

# Scenarios

Runnable, end-to-end examples that map to real adoption questions.

| Scenario | When to read |
|---|---|
| [A simple end-to-end suite](./simple-suite.md) | First real example — the bundled quickstart, four cases. |
| [Large suites & multi-file agents](./large-suites.md) | Outgrowing one suite file; per-domain organization. |
| [Edge cases](./edge-cases.md) | Empty output, exceptions, missing baselines, exotic inputs. |
| [CI/CD integration](./ci-cd.md) | GitHub Actions, GitLab, CircleCI, Buildkite. |
| [OpenAI / Anthropic SDK adapters](./openai-adapter.md) | Skip manual instrumentation when on a supported SDK. |
| [Performance & cost budgets](./performance.md) | `cost_lt_usd`, `latency_lt_ms`, drift detection. |
| [Debugging workflow](./debugging.md) | A failing case → root cause in five minutes. |
| [Failure handling](./failure-handling.md) | Exception paths, judge unavailability, baseline corruption. |

Every scenario follows the same five-section shape: **problem → input
→ code → output → explanation**. Copy-paste any of them as a starting
point.

# Show HN draft — agentprdiff

*Post on a Tuesday or Wednesday morning ET. Lead with the finding, not the
feature list. The linked write-up (docs/benchmark-writeup.md, published on
agentprdiff.dev) carries the details.*

---

**Title:** Show HN: We downgraded Claude under two agents — right answers, broken prod (agentprdiff)

*(Fallback title if that reads too clickbaity for the day's front page:*
**Show HN: agentprdiff – snapshot tests that diff LLM agent behavior in CI**)*

---

Hi HN — I built agentprdiff (https://github.com/vnageshwaran-de/agentprdiff)
after watching the same quiet failure hit every team I know that ships LLM
agents: a model swap, a prompt tweak, or a vendor change silently alters
agent behavior, and nobody finds out until production.

To show the failure mode concretely, I ran a controlled experiment
[write-up: https://agentprdiff.dev/benchmark-writeup]: two realistic agents
(a tool-calling support agent with a refund guardrail, and a strict-JSON
extraction agent), behavioral baselines recorded on claude-sonnet, then the
natural cost downgrade to claude-haiku.

The result surprised me. Haiku got *every answer right* — held the refund
guardrail, refused the ineligible refund, even picked the correct order out
of an email mentioning two. And it still regressed 3 of 7 cases, because it
wraps its JSON output in markdown fences despite the prompt explicitly
forbidding them. Sonnet obeyed; Haiku didn't, consistently, across
independent runs. If your pipeline calls json.loads on that output, the
cheaper model takes your service down while scoring fine on every accuracy
benchmark you'd check.

That's the category of bug agentprdiff exists for. It's deliberately the
narrowest possible tool:

1. Write cases: (input, list of assertions) — assertions are deterministic
   graders (contains, regex, tool_called, tool_sequence, latency/cost
   budgets) plus an optional LLM-judge one you can refuse to depend on.
2. `agentprdiff record` on a known-good agent — full traces (LLM calls,
   tool calls, output, cost, latency) saved as JSON baselines, committed
   to git.
3. `agentprdiff check` on every PR — re-runs, diffs against baseline,
   exits 1 on regression. A GitHub Action posts the behavioral diff as a
   PR comment, so reviewers see "this assertion flipped, here's the output
   diff" the way they see code diffs.

Design choices that came from running this on real suites:

* Stochasticity is handled honestly: `--runs 3` with a per-case
  min_pass_rate tolerates a wobble without letting real regressions
  through; `--concurrency` keeps that affordable in wall-clock.
* Baselines are frozen: grader verdicts are persisted at record time, so
  checks never re-run judges against your baseline.
* Trust over green: `--strict-judge` fails CI if the semantic grader
  silently fell back to the keyword-matching fake judge because an API key
  went missing. A green build should mean what it says.
* No framework lock-in: your agent is any callable (sync or async);
  adapters auto-instrument the OpenAI/Anthropic SDKs and OpenAI-compatible
  providers.

The benchmark is in the repo and re-runnable for a few cents
(benchmark/README.md has the methodology, including the false positive we
hit and how we fixed it — that part cuts against us and stays in).

MIT licensed. `pip install agentprdiff`. Docs: https://agentprdiff.dev

I'd genuinely value HN's take on one open question: strict-judge mode
becomes the default at 1.0 — is failing CI on a silently-degraded judge the
right default, or too aggressive?

---

*Cross-post plan: r/LLMDevs (same body, less formal), LinkedIn (the
write-up's "why this matters" section), lobste.rs if someone can invite.
Pitch the write-up itself to TLDR AI / Latent Space / Python Weekly with a
two-line summary: model-swap regression data is scarce; this is
re-runnable.*

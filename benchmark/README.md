# The agentprdiff model-swap benchmark

**Question:** when you swap the model under an LLM agent — or "clean up" its
system prompt — which of its behaviors silently change?

**Method:** two realistic agents with committed behavioral baselines, a
matrix of controlled changes, and `agentprdiff check` to diff behavior
against baseline. Every grader is **deterministic** — substring, regex,
tool-call, and length assertions; no LLM-as-judge anywhere — so every
regression reported is mechanically verifiable by anyone who re-runs the
script.

## The agents under test

**`agents/support_agent.py`** — a tool-calling customer-support agent with
two tools (`lookup_order`, `issue_refund`) over a canned order database.
Its behavioral contract: look orders up before answering, only refund
refund-eligible orders (the guardrail), state the refund timeline, stay on
topic.

**`agents/extraction_agent.py`** — a structured-extraction agent: messy
customer email in, strict JSON out (`customer_name`, `order_id`,
`issue_type`). Its contract: bare JSON with no prose or fences, the *right*
order id when the email mentions several, correct issue classification.

Both read their provider, model, and prompt variant from environment
variables, so the driver swaps configurations without touching agent code —
which is precisely the kind of change this benchmark measures.

## The scenarios

| Scenario | What changes | Needs |
|---|---|---|
| `openai-downgrade` | `gpt-4o` → `gpt-4o-mini` (the "cheap path" downgrade) | `OPENAI_API_KEY` |
| `anthropic-downgrade` | `claude-sonnet-4-6` → `claude-haiku-4-5` | `ANTHROPIC_API_KEY` |
| `cross-vendor` | `gpt-4o` → `claude-haiku-4-5` (vendor swap) | both keys |
| `prompt-rewrite` | baseline system prompt → a terse "cleanup" (same model) | `OPENAI_API_KEY` |
| `stub-smoke` | nothing (harness sanity check, no API calls) | — |
| `stub-regression` | a deliberately degraded stub (report-format demo, no API calls) | — |

The terse prompt in `prompt-rewrite` is the change a well-meaning teammate
ships on a Friday: shorter and vaguer, silently dropping the refund
eligibility guardrail and the scope restriction.

## Reproduce it

```bash
git clone https://github.com/vnageshwaran-de/agentprdiff
cd agentprdiff
pip install -e ".[openai,anthropic]"

# sanity-check the harness first — free, no keys:
python benchmark/run_benchmark.py --scenarios stub-smoke,stub-regression

# then the real matrix (a few dollars of API spend):
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
python benchmark/run_benchmark.py
```

The driver records baselines, runs the checks, and writes
[`RESULTS.md`](RESULTS.md) with a summary table plus, per scenario, every
assertion that flipped and the mechanical reason it now fails.

## Methodology notes, honestly

- **Temperature 0 where the API allows it**, but providers don't guarantee
  determinism; a rerun can differ at the margin. Treat single flips as
  indicative and rerun before quoting them (`check --runs 3` exists for
  exactly this).
- **The suites encode a specific behavioral contract.** A model that fails
  `states-timeline` isn't a bad model — it broke *this agent's* contract.
  That's the point: the benchmark measures behavioral drift against a
  committed baseline, not model quality.
- **No LLM-as-judge** — deliberately. Judge-based scores add a second
  stochastic system to the measurement. Everything here is a substring,
  regex, tool-call, or length check you can verify by reading the trace.
- **The stub scenarios are demos**, clearly labeled, and excluded from the
  default matrix. They exist so the harness itself is testable in CI and so
  readers can see the report format before spending API dollars.

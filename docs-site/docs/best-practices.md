---
id: best-practices
title: Best Practices
sidebar_position: 11
---

# Best Practices

Patterns that survive contact with real codebases.

## Naming

| Object | Convention | Example |
|---|---|---|
| Suite name | `snake_case`, domain-scoped | `billing`, `customer_support`, `multilingual` |
| Case name | `snake_case`, behavior-scoped | `refund_happy_path`, `non_refundable_order`, `policy_question_no_tools` |
| Suite file | `<suite_name>.py` next to your agent | `suites/billing.py` |
| Tags | short lowercase words | `slow`, `smoke`, `flaky`, `manual` |

Filenames hit the filesystem (slugified), the terminal, and the JSON
report. Keep them ASCII, short, and grep-friendly.

## Cases pin behavior, not output

Bad:

```python
case(name="refund", input="…", expect=[contains("Of course! I have processed your refund of $89.00")])
```

This breaks every time the prompt rewrites a phrase.

Better:

```python
case(name="refund", input="…", expect=[
    contains("refund"),
    regex_match(r"\$\d+(\.\d{2})?"),
    tool_called("lookup_order"),
])
```

Pin the *behavior* (a refund happened, a money amount was quoted, the
right tool fired). The exact phrasing is volatile by design.

## Always include budget graders

Every case should have `latency_lt_ms` and `cost_lt_usd`, even if loose:

```python
expect=[
    contains("…"),
    latency_lt_ms(15_000),     # 3 × observed p50 to start
    cost_lt_usd(0.05),         # generous ceiling
]
```

Tighten over time as you accumulate observed data. Loose-but-present
budgets are infinitely more useful than no budgets — they catch
catastrophic regressions (`for ... in for ... in` runaway prompts) without
flaking on the ordinary noise.

## Encode the contract row by row

A "case" should map to exactly one row of your behavioral contract.
That means resisting the urge to write one giant `mega_case` with twenty
assertions:

```python
# AVOID
case(
    name="all_the_things",
    input="…",
    expect=[contains("a"), contains("b"), tool_called("c"), tool_called("d"), ...],
)

# PREFER
case(name="agent_says_a", input="…", expect=[contains("a")])
case(name="agent_says_b", input="…", expect=[contains("b")])
case(name="agent_calls_c", input="…", expect=[tool_called("c")])
case(name="agent_calls_d", input="…", expect=[tool_called("d")])
```

Smaller cases mean clearer regression signals — when one fails, the name
tells you what broke.

## Use `semantic` last

| First | Then | Last |
|---|---|---|
| `contains`, `regex_match`, `tool_called` | `tool_sequence`, `output_length_lt`, `cost_lt_usd`, `latency_lt_ms` | `semantic` |

`semantic` is slow, costs money on every CI run, and is non-deterministic
(even with `temperature=0`, judge models nudge over time). Reserve it
for the 20 % of behavior you can't encode mechanically.

When you do use `semantic`, write rubrics about *behavior*, not
*phrasing*:

```python
# AVOID
semantic("agent says 'I am sorry for the inconvenience'")

# PREFER
semantic("agent acknowledges the customer's frustration before answering")
```

## Stub side effects

Don't let your suite call PagerDuty, send emails, or charge credit cards.
Wrap side-effecting tools in stubs:

```python
# suites/_stubs.py
def lookup_order(order_id: str) -> dict:
    if order_id == "9999":
        return {"status": "shipped", "refundable": False}
    return {"status": "delivered", "refundable": True, "amount_usd": 89.0}

def send_email(*args, **kwargs) -> dict:
    return {"sent": True, "id": "email-stub-1"}

STUB_TOOL_MAP = {"lookup_order": lookup_order, "send_email": send_email}
```

Pass `STUB_TOOL_MAP` to `instrument_tools`. The agent under test
exercises the real *dispatch* (which tool, with what args) but not the
real *side effect*.

## Re-record one case at a time

When a behavior change is intentional, re-record only the case that
should have changed:

```bash
agentprdiff record suites/billing.py --case refund_happy_path
git add .agentprdiff/baselines/billing/refund_happy_path.json
```

The PR diff stays scoped — one JSON file, one assertion, one
explanation. Re-recording the whole suite hides the *meaning* of the
change in a wall of JSON.

## Make the judge mode explicit in CI

Don't rely on the judge's autodetect order in CI. Set
`AGENTGUARD_JUDGE` explicitly so future maintainers know which judge ran:

```yaml
env:
  AGENTGUARD_JUDGE: anthropic
  ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

If the suite has no `semantic()` graders, set
`AGENTGUARD_JUDGE=fake` to make the absence-of-judge intentional.

## Commit the workflow YAML alongside the suite

`agentprdiff scaffold <name>` produces a starter
`.github/workflows/agentprdiff.yml` because **a suite without CI is a
suite that bit-rots**. The workflow gates on
`secrets.OPENAI_API_KEY` and emits `::warning::` when missing — green
on fork PRs, fails on missing baseline regressions.

## Treat baselines as code

- Code review them — the JSON diff in `.agentprdiff/baselines/` is the
  review surface.
- Check them out at the same commit as the agent code that produced them.
- Don't hand-edit; re-record.
- Tag the agent SDK version and price-table revision in the PR
  description when you re-record.

## Document the case dossier

For every non-trivial suite, ship a `<name>_cases.md` next to the suite
file (the `agentprdiff scaffold` template produces one for you). Each
case gets:

- **What it tests** — one paragraph in plain English.
- **Input** — the exact input and *why this input was chosen*.
- **Assertions** — one bullet per grader, in plain English.
- **Code impacted** — file paths and approximate line numbers.
- **Application impact** — what breaks for users when this regresses.

When the case fails in CI six months later, a reviewer can read this in
ten seconds and decide whether to revert or re-record.

## Keep suites portable across machines

- Inline test data in the suite file or under `suites/` rather than
  reading from absolute paths.
- Pin the agent SDK version and any pricing overrides.
- Use the same `AGENTGUARD_JUDGE` mode locally and in CI.

A suite that "works on my machine" but fails in CI is a flaky suite,
which is worse than no suite — it teaches the team to ignore the gate.

## Treat zero-match as failure

`agentprdiff` exits 2 when `--case` / `--skip` matches no cases. *Don't*
suppress that with `|| true` in CI. A typo in a filter shouldn't sneak
through as a green build.

## Run `review` early

Before pushing a new case, run `agentprdiff review` on it. The verbose
panel shows the trace shape, the reasons each assertion fired, and the
diff. Catching "the assertion passes for the wrong reason" before you
record is much cheaper than catching it after.

## Tighten budgets over time

Start loose. Watch a few weeks of `Cost Δ` / `Latency Δ` columns.
Re-record the baseline (`agentprdiff record`) and *also* tighten the
graders:

```python
# Week 1
expect=[contains("…"), cost_lt_usd(0.10), latency_lt_ms(20_000)]

# Week 4 — tightened after observation
expect=[contains("…"), cost_lt_usd(0.02), latency_lt_ms(8_000)]
```

The whole point of `agentprdiff` is that the budgets get easier to set
once you have data.

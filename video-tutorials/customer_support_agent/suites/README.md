# ShopFast customer support agent — agentprdiff suite

Behavioral regression tests for the ShopFast LangGraph customer support agent.
The suite covers 12 cases across 5 suites pinning the agent's refund logic,
policy look-ups, order status, multi-step reasoning, and out-of-scope handling.

## Quick start

```bash
# Install dependencies (from the project root)
pip install -r requirements.txt

# Record baselines (first run, or after an intentional change)
agentprdiff record suites/customer_support.py

# Check for regressions (CI gate)
agentprdiff check suites/customer_support.py
```

## Running one case

```bash
# By substring
agentprdiff check  suites/customer_support.py --case refund_happy_path
agentprdiff review suites/customer_support.py --case refund_happy_path   # verbose, exit 0

# By glob
agentprdiff check  suites/customer_support.py --case "*refund*"

# List all case names
agentprdiff check  suites/customer_support.py --list
```

## Semantic Judge Keys

This suite uses `semantic(...)` graders in the following cases:
`refund_happy_path`, `refund_order_not_found`, `refund_in_transit_order`,
`policy_footwear`, `policy_unknown_category`, `full_refund_journey`,
`status_then_policy_no_refund`, `off_topic_weather`, `vague_refund_no_order_id`.

**CI judge mode: `fake_judge` (keyword matching, free).**

The CI workflow does NOT set `AGENTGUARD_JUDGE` or a judge-provider key, so
the semantic graders run in `fake_judge` mode — fast, free, but only keyword
matching. The rubric strings are written to pass under keyword matching for the
happy path; they add a human-readable description of intent for reviewers.

To switch to a real LLM judge locally or in CI:

```bash
# Anthropic judge (recommended — cheaper)
export AGENTGUARD_JUDGE=anthropic
export ANTHROPIC_API_KEY=sk-ant-...
agentprdiff check suites/customer_support.py

# OpenAI judge
export AGENTGUARD_JUDGE=openai
export OPENAI_API_KEY=sk-...
agentprdiff check suites/customer_support.py
```

Add the corresponding secret in GitHub Settings → Secrets and variables →
Actions, then add `AGENTGUARD_JUDGE: anthropic` and
`ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}` to the workflow YAML's
`env:` block.

## Stub strategy

The production tools (`lookup_order`, `process_refund`, `check_policy`) call
external APIs only when `LIVE_TOOLS=true`. The suite always runs with
`LIVE_TOOLS=false` (the default), which activates the in-module mock data.
See `_stubs.py` for the expected return shapes.

## Suite map

| Suite | Cases | What it pins |
|---|---|---|
| `refund_flow` | 3 | lookup-before-refund ordering; no refund on unknown/in-transit orders |
| `policy_queries` | 3 | correct tool called; right window returned; graceful unknown-category fallback |
| `order_status` | 2 | no spurious tool calls; correct status text |
| `multi_step_reasoning` | 2 | 3-tool chain; in-transit + policy without refund |
| `out_of_scope` | 2 | no tool called; agent asks for missing order ID |

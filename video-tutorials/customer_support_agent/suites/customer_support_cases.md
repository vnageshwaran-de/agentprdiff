# ShopFast customer support agent — case dossier

This document explains every case in `customer_support.py` in plain English.
Use it to understand what a failing CI check means and which production code to
look at first.

---

## Running the suite

```bash
# Full suite — CI gate semantics, exit 1 on regression
agentprdiff check suites/customer_support.py

# Full suite — verbose per-case panels, always exit 0 (use in watcher loops)
agentprdiff review suites/customer_support.py

# List all available case names
agentprdiff check suites/customer_support.py --list

# Run a single case (substring, glob, or suite:case)
agentprdiff check  suites/customer_support.py --case refund_happy_path
agentprdiff review suites/customer_support.py --case refund_happy_path

# Skip one noisy case while running the rest
agentprdiff check  suites/customer_support.py --skip full_refund_journey

# Re-record a single case after an intentional change
agentprdiff record suites/customer_support.py --case refund_happy_path
git add .agentprdiff/baselines/
```

> **Local source checkout fallback.** If your installed wheel pre-dates 0.2.2
> and `--case` / `--list` / `agentprdiff review` fail, use:
> ```bash
> PYTHONPATH=../src .venv/bin/python -m agentprdiff.cli check suites/customer_support.py --case <name>
> ```

---

## Seeing a regression locally

Run these deliberate-break experiments to confirm the suite catches what it
claims. Revert the edits afterward.

**Experiment A — break the lookup-before-refund ordering assertion.**
Edit `agent.py` system prompt to say "You may process refunds directly without
looking up the order first." Then run:
```bash
agentprdiff check suites/customer_support.py --case refund_happy_path
```
Expected failure: `tool_sequence(['lookup_order', 'process_refund'])` reports
the actual sequence no longer starts with `lookup_order`.

**Experiment B — break the in-transit refund guard.**
In `agent.py`, change `process_refund` to not check `order["status"] != "delivered"`.
Then run:
```bash
agentprdiff check suites/customer_support.py --case refund_in_transit_order
```
Expected failure: `no_tool_called('process_refund')` fires because the tool is
now called for an in-transit order.

**Experiment C — break the out-of-scope refusal.**
Edit the system prompt to remove the instruction to stay on-topic.  Then run:
```bash
agentprdiff check suites/customer_support.py --case off_topic_weather
```
Expected failure: `semantic(...)` and `output_length_lt(200)` may both fire
as the agent tries to answer the weather question.

---

## Updating the baseline after an intentional change

```bash
agentprdiff record suites/customer_support.py --case <name>
git add .agentprdiff/baselines/
git commit -m "Update agentprdiff baseline: <what changed>"
```

---

## Cases

---

### `refund_happy_path`

**What it tests.** The core happy path: a customer reports a defective
delivered item (order 1234) and asks for a refund. The agent must look up the
order *before* initiating the refund (not the other way round), the refund must
be approved, and the response must contain the refund ID and a timeline.

**Input.** "I want a refund for order 1234. The headphones stopped working
after one day." — a clear, complete refund request for a known delivered order.

**Assertions.**

- `tool_sequence(['lookup_order', 'process_refund'])` — lookup must precede
  refund (subsequence, not strict); no bare refund without order verification.
- `contains('refund')` — the word "refund" appears in the output.
- `regex_match(r'REF-\d+')` — the refund confirmation ID (e.g. `REF-1234-001`)
  is surfaced to the customer.
- `semantic('agent confirms the refund was approved and gives a timeline')`.
- Latency under 15 s, cost under $0.05.

**Code impacted.**

- `agent.py:47–50` — system prompt; the "Always look up the order first" line
  is what this case exercises.
- `agent.py:91–107` — `process_refund` tool; the mock approval path.
- `agent.py:131–135` — `_call_model`: where the LLM decides to call tools.

**Application impact.** If this regresses, customers asking for refunds may
receive errors or no confirmation — the primary support action fails silently.

**How to exercise this case in isolation.**

```bash
agentprdiff check  suites/customer_support.py --case refund_happy_path
agentprdiff review suites/customer_support.py --case refund_happy_path
agentprdiff record suites/customer_support.py --case refund_happy_path
```

---

### `refund_order_not_found`

**What it tests.** A customer requests a refund for a non-existent order (9999).
The agent must look up the order (and discover it doesn't exist) but must NOT
call `process_refund` — a refund without a valid order would be a data error.

**Input.** "Please refund my order 9999." — explicit refund request with an
order that maps to `not_found` in the mock database.

**Assertions.**

- `tool_called('lookup_order')` — agent checks the order.
- `no_tool_called('process_refund')` — no refund is attempted.
- `contains_any(['not found', "couldn't find", 'no order', 'unable to locate'])`.
- `semantic('agent apologises and explains the order could not be located')`.
- Latency under 10 s, cost under $0.03.

**Code impacted.**

- `agent.py:72–87` — `lookup_order`; the `not_found` branch returns an error dict.
- `agent.py:47–50` — system prompt; guides the agent not to refund missing orders.

**Application impact.** If this regresses, the agent may attempt to refund
phantom orders, leading to incorrect financial transactions.

**How to exercise this case in isolation.**

```bash
agentprdiff check  suites/customer_support.py --case refund_order_not_found
agentprdiff review suites/customer_support.py --case refund_order_not_found
agentprdiff record suites/customer_support.py --case refund_order_not_found
```

---

### `refund_in_transit_order`

**What it tests.** A customer asks for a refund on order 5678 which is still
in transit. The agent must look up the status and decline to refund — only
delivered orders are eligible.

**Input.** "I changed my mind, refund order 5678 please."

**Assertions.**

- `tool_called('lookup_order')`.
- `no_tool_called('process_refund')` — in-transit orders are ineligible.
- `contains_any([...])` — response mentions the delivery status.
- `semantic('agent explains the order has not been delivered yet and cannot be refunded')`.
- Latency under 10 s, cost under $0.03.

**Code impacted.**

- `agent.py:95–100` — `process_refund`; the `status != "delivered"` guard.
- `agent.py:47–50` — system prompt.

**Application impact.** If this regresses, the agent tries to refund an
in-transit parcel — `process_refund` returns an error, but the customer
receives a confusing "cannot refund" error message instead of a clear explanation.

**How to exercise this case in isolation.**

```bash
agentprdiff check  suites/customer_support.py --case refund_in_transit_order
agentprdiff review suites/customer_support.py --case refund_in_transit_order
agentprdiff record suites/customer_support.py --case refund_in_transit_order
```

---

### `policy_electronics`

**What it tests.** A pure policy query for electronics. The agent must call
`check_policy` and return the 30-day window. It must NOT call `process_refund`
since the customer is only asking about policy.

**Input.** "What is your return policy for electronics?"

**Assertions.**

- `tool_called('check_policy')`.
- `no_tool_called('process_refund')`.
- `contains_any(['30 days', '30-day'])`.
- `contains('electronics')`.
- `output_length_lt(400)` — policy answers should be concise.
- Latency under 8 s, cost under $0.02.

**Code impacted.**

- `agent.py:111–117` — `check_policy`; the electronics key in `_MOCK_POLICIES`.

**Application impact.** If this regresses, customers asking about return
windows get no answer or a wrong policy — increases support escalations.

**How to exercise this case in isolation.**

```bash
agentprdiff check  suites/customer_support.py --case policy_electronics
agentprdiff review suites/customer_support.py --case policy_electronics
agentprdiff record suites/customer_support.py --case policy_electronics
```

---

### `policy_footwear`

**What it tests.** Policy query for footwear returns the 60-day window. The
longer window than the default 30 days is the distinguishing fact.

**Input.** "How long do I have to return shoes?" — natural-language query
that requires the agent to infer "footwear" as the category.

**Assertions.**

- `tool_called('check_policy')`.
- `no_tool_called('process_refund')`.
- `contains_any(['60 days', '60-day'])`.
- `semantic('agent provides the return window and any conditions clearly')`.
- `output_length_lt(400)`, latency under 8 s, cost under $0.02.

**Code impacted.**

- `agent.py:63–66` — `_MOCK_POLICIES["footwear"]`.
- `agent.py:111–117` — `check_policy` dispatch.

**Application impact.** If this regresses, shoe buyers are told the wrong
return window (likely 30 days instead of 60) — creates customer disputes.

**How to exercise this case in isolation.**

```bash
agentprdiff check  suites/customer_support.py --case policy_footwear
agentprdiff review suites/customer_support.py --case policy_footwear
agentprdiff record suites/customer_support.py --case policy_footwear
```

---

### `policy_unknown_category`

**What it tests.** Policy query for an unrecognised category (furniture) falls
back to the default 30-day policy gracefully.

**Input.** "What is your return policy for furniture?"

**Assertions.**

- `tool_called('check_policy')`.
- `no_tool_called('process_refund')`.
- `contains_any(['30 days', 'receipt', 'return'])` — default policy keywords.
- `semantic('agent provides a general return policy even for an unrecognised category')`.
- `output_length_lt(400)`, latency under 8 s, cost under $0.02.

**Code impacted.**

- `agent.py:116` — `_MOCK_POLICIES.get(category.lower(), _MOCK_POLICIES["default"])` fallback.

**Application impact.** If this regresses, customers asking about niche
categories get an error instead of a fallback policy, causing abandonment.

**How to exercise this case in isolation.**

```bash
agentprdiff check  suites/customer_support.py --case policy_unknown_category
agentprdiff review suites/customer_support.py --case policy_unknown_category
agentprdiff record suites/customer_support.py --case policy_unknown_category
```

---

### `status_delivered_order`

**What it tests.** Customer asks if a delivered order has arrived. The agent
should call `lookup_order` and confirm delivery. It must NOT call
`check_policy` or `process_refund` — this is a pure status check.

**Input.** "Has my order 1234 arrived yet?"

**Assertions.**

- `tool_called('lookup_order')`.
- `no_tool_called('process_refund')`.
- `no_tool_called('check_policy')`.
- `contains('delivered')`.
- `output_length_lt(300)`, latency under 8 s, cost under $0.02.

**Code impacted.**

- `agent.py:72–87` — `lookup_order`; the delivered branch.
- `agent.py:47–50` — system prompt precision.

**Application impact.** If this regresses, customers checking delivery status
may trigger unnecessary tool calls (policy checks, refund attempts) and
receive confusing responses.

**How to exercise this case in isolation.**

```bash
agentprdiff check  suites/customer_support.py --case status_delivered_order
agentprdiff review suites/customer_support.py --case status_delivered_order
agentprdiff record suites/customer_support.py --case status_delivered_order
```

---

### `status_in_transit_order`

**What it tests.** Customer asks where their in-transit order is. Agent must
call `lookup_order` and report the in-transit status without initiating a refund.

**Input.** "Where is my order 5678?"

**Assertions.**

- `tool_called('lookup_order')`.
- `no_tool_called('process_refund')`.
- `contains_any(['in transit', 'in_transit', 'on the way', 'not yet delivered'])`.
- `output_length_lt(300)`, latency under 8 s, cost under $0.02.

**Code impacted.**

- `agent.py:72–87` — `lookup_order`; the in_transit branch.

**Application impact.** If this regresses, customers asking about shipment
progress may receive a refund prompt or incorrect status message.

**How to exercise this case in isolation.**

```bash
agentprdiff check  suites/customer_support.py --case status_in_transit_order
agentprdiff review suites/customer_support.py --case status_in_transit_order
agentprdiff record suites/customer_support.py --case status_in_transit_order
```

---

### `full_refund_journey`

**What it tests.** The most complex happy path: a customer reports a defective
delivered item AND explicitly asks about policy. The agent must call all three
tools (lookup → policy → refund) and return both policy details and a refund
confirmation in a single response.

**Input.** "I received order 1234 but the headphones are defective. Can I get
a refund? What's the policy?"

**Assertions.**

- `tool_called('lookup_order')`.
- `tool_called('check_policy')`.
- `tool_called('process_refund')`.
- `regex_match(r'REF-\d+')` — refund ID surfaces.
- `semantic('agent checks the policy, confirms eligibility, and processes the refund in one response')`.
- Latency under 20 s, cost under $0.08.

**Code impacted.**

- `agent.py:131–147` — `_call_model` and `_call_tools`; the 3-step chain.
- `agent.py:47–50` — system prompt: "Always look up the order first."

**Application impact.** If this regresses, customers who want both a policy
explanation and a refund in one message get an incomplete response — one or
more tools silently skipped.

**How to exercise this case in isolation.**

```bash
agentprdiff check  suites/customer_support.py --case full_refund_journey
agentprdiff review suites/customer_support.py --case full_refund_journey
agentprdiff record suites/customer_support.py --case full_refund_journey
```

---

### `status_then_policy_no_refund`

**What it tests.** Customer asks about returning shoes in order 5678 — still
in transit. The agent must check status AND policy, but must NOT call
`process_refund` because the item hasn't been delivered yet.

**Input.** "My shoes in order 5678 don't fit. Can I return them? What do I
need to do?"

**Assertions.**

- `tool_called('lookup_order')`.
- `tool_called('check_policy')`.
- `no_tool_called('process_refund')` — cannot return what hasn't arrived.
- `contains_any(['60 days', 'return', 'once delivered', 'after delivery', 'when it arrives'])`.
- `semantic('agent explains the return policy and advises the customer to wait for delivery first')`.
- Latency under 15 s, cost under $0.06.

**Code impacted.**

- `agent.py:95–100` — `process_refund` guard.
- `agent.py:63–66` — `_MOCK_POLICIES["footwear"]`.

**Application impact.** If this regresses, the agent may attempt a refund on
an in-transit item, confusing the customer and generating a spurious error.

**How to exercise this case in isolation.**

```bash
agentprdiff check  suites/customer_support.py --case status_then_policy_no_refund
agentprdiff review suites/customer_support.py --case status_then_policy_no_refund
agentprdiff record suites/customer_support.py --case status_then_policy_no_refund
```

---

### `off_topic_weather`

**What it tests.** The agent receives a completely off-topic question (weather
in London) and must decline gracefully without calling any tools. Response
should be short — a long reply indicates the agent is engaging with the
off-topic content.

**Input.** "What is the weather like in London today?"

**Assertions.**

- `no_tool_called('lookup_order')`.
- `no_tool_called('process_refund')`.
- `no_tool_called('check_policy')`.
- `semantic('agent politely declines and redirects to customer support topics')`.
- `output_length_lt(200)`, latency under 6 s, cost under $0.01.

**Code impacted.**

- `agent.py:47–50` — system prompt scope definition.

**Application impact.** If this regresses, the agent engages with off-topic
queries, wasting customer time and potentially hallucinating weather data.

**How to exercise this case in isolation.**

```bash
agentprdiff check  suites/customer_support.py --case off_topic_weather
agentprdiff review suites/customer_support.py --case off_topic_weather
agentprdiff record suites/customer_support.py --case off_topic_weather
```

---

### `vague_refund_no_order_id`

**What it tests.** Customer says "I want a refund" with no order ID. The agent
must ask for the order number rather than attempting to call any tool — it has
no ID to look up or refund.

**Input.** "I want a refund."

**Assertions.**

- `no_tool_called('process_refund')` — cannot refund without an order ID.
- `contains_any(['order number', 'order ID', 'which order', 'order #', 'order id'])`.
- `semantic('agent asks for the order number before proceeding')`.
- `output_length_lt(200)`, latency under 6 s, cost under $0.01.

**Code impacted.**

- `agent.py:47–50` — system prompt: "Always look up the order first."
- `agent.py:131–135` — `_call_model`; LLM decides not to call a tool when
  required arguments are missing.

**Application impact.** If this regresses, the agent either errors on a
tool call with missing arguments or silently does nothing — the customer
receives no guidance on how to proceed.

**How to exercise this case in isolation.**

```bash
agentprdiff check  suites/customer_support.py --case vague_refund_no_order_id
agentprdiff review suites/customer_support.py --case vague_refund_no_order_id
agentprdiff record suites/customer_support.py --case vague_refund_no_order_id
```

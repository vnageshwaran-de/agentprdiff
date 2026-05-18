# Adopting agentprdiff — instructions for AI coding agents

You are an AI coding agent (Claude Code, Cursor, Aider, Copilot, or similar) and the user has asked you to add `agentprdiff` to a repository, write a regression suite for an existing LLM agent, or fix an existing suite. This document is your brief.

It is dense by design. Each section is self-contained and grep-friendly. Read it linearly the first time; on subsequent runs jump to the section you need.

For the canonical file layout (what files to create, what's mandatory vs optional, what each contains), see [`docs/suite-layout.md`](./docs/suite-layout.md). Use it as the spec for your final diff before you tell the user you're done.

If you are a human reader, the same content is here but `docs/writing-suites.md` (forthcoming) is friendlier prose. This file is optimized for an agent that wants to copy templates and substitute values.

---

## What agentprdiff is, in one paragraph

`agentprdiff` is a regression-testing tool for non-deterministic LLM agents. The user writes a small Python file (a "suite") that lists `case(input, expect=[...])` entries. The first time `agentprdiff record` is run, it captures a JSON "baseline" trace of every model and tool call the agent made for each case. Baselines are committed to git. On every subsequent run (`agentprdiff check`, typically in CI), the new traces are diffed against the committed baselines; the build fails on regression. Think `git diff` for agent behavior, or Jest snapshot tests for `(model, prompt, tool_dispatch)` triples.

---

## Decision tree: should this project adopt agentprdiff?

Run through these in order. Stop at the first **NO** — agentprdiff is the wrong tool. Reach the end with all **YES** answers and proceed.

1. Does the project contain code that calls an LLM API (`openai`, `anthropic`, `langchain`, `google.genai`, a raw `requests.post` to an inference endpoint, …)? **YES** → continue. **NO** → stop. agentprdiff is for projects that ship LLM agents.
2. Does the agent's behavior depend on which model, prompt, or tool definitions are used? **YES** → continue. **NO** → stop. If the LLM call is incidental (one-off summarization, no tools, no system prompt the user controls), skip agentprdiff.
3. Is there at least one *contract* the agent must honor — "must call tool X first," "must not refund without verification," "must ask for missing inputs," "must stay under cost budget Y"? **YES** → continue. **NO** → stop. If you can't articulate a contract, you don't have a suite to write.
4. Is the agent already in production or about to be? Are model/prompt/SDK upgrades realistic in the project's lifetime? **YES** → continue. **NO** → consider deferring; agentprdiff's value compounds across changes.

If all four are YES, proceed.

---

## Canonical layout — what files you'll produce

Memorize this tree. Every adoption produces exactly this shape; deviating from it is a smell. Full per-file spec at [`docs/suite-layout.md`](./docs/suite-layout.md).

```
<project_root>/
├── suites/
│   ├── __init__.py                 ← optional (package marker)
│   ├── _eval_agent.py              ← MANDATORY (Step 3 produces this)
│   ├── _stubs.py                   ← MANDATORY iff side-effecting tools (Step 4)
│   ├── <project>.py                ← MANDATORY (Step 5 produces this)
│   ├── <project>_cases.md          ← MANDATORY (Step 5 produces this — case dossier)
│   └── README.md                   ← recommended
│
├── .agentprdiff/
│   ├── .gitignore                  ← auto-created by `agentprdiff init` (Step 6)
│   ├── baselines/<suite>/<case>.json   ← auto-created by `agentprdiff record` (Step 6)
│   └── runs/                       ← auto-created, NEVER committed
│
└── .github/workflows/
    └── agentprdiff.yml             ← strongly recommended (Step 7)
```

| File | Status | Created by |
|---|---|---|
| `suites/<project>.py` | MANDATORY | you |
| `suites/<project>_cases.md` | MANDATORY (the case dossier — see Step 5) | you |
| `suites/_eval_agent.py` | MANDATORY | you |
| `suites/_stubs.py` | MANDATORY iff side-effecting tools exist | you |
| `suites/__init__.py` | optional | you |
| `suites/README.md` | recommended | you |
| `.agentprdiff/.gitignore` | MANDATORY | `agentprdiff init` |
| `.agentprdiff/baselines/<suite>/<case>.json` | MANDATORY (one per case) | `agentprdiff record` |
| `.agentprdiff/runs/` | auto-created | `agentprdiff check` (NEVER commit) |
| `.github/workflows/agentprdiff.yml` | strongly recommended | you |

The Steps below produce these files in order. When you finish, your final `git status` should show *only* these paths added — no production-code modifications.

> **Shortcut: scaffold first, fill in TODOs.** Run `agentprdiff scaffold <project_name> --recipe <recipe>` to lay down the whole canonical layout in one shot, then edit the generated files. Recipes: `sync-openai` (default; `instrument_client` with a sync `OpenAI()` client), `async-openai` (the same `instrument_client` plus an `asyncio.run` bridge — works natively with `AsyncOpenAI`), `stubbed` (substitutes a single LLM helper — see [`docs/adapters.md`](./docs/adapters.md#stubbed-llm-boundary-pattern)). The scaffold also writes `suites/<project_name>_cases.md` — the case dossier (per-case "what it tests / input / assertions / code impacted / application impact"). Existing files are never overwritten, so this is safe to run on a partially-built suite.

---

## Step 1 — discover the agent in the codebase
*Produces: nothing on disk. Outputs a mental model you'll use in Steps 2–5.*

Before writing any test code, find the production agent. Use `Grep` / ripgrep to locate:

```
rg -l "chat\.completions\.create|messages\.create" --type py
rg -l "from openai import|from anthropic import|import openai|import anthropic" --type py
rg -l "tools\s*=\s*\[" --type py
```

For each candidate file, identify:

- The **agent entry function**. Usually named something like `run_agent`, `chat`, `respond`, or the only function in the module that takes a string and returns one.
- The **system prompt**. Usually a module-level constant called `SYSTEM_PROMPT` or assembled inline.
- The **tool dispatch dict**. Usually called `TOOL_MAP`, `TOOLS`, `FUNCTIONS`, `tool_handlers`, or similar — maps a string name to a callable. If tools are dispatched by `if/elif fn_name == "..."`, refactor to a dict first; the suite needs a single point to swap in stubs.
- The **client construction**. Look for `OpenAI(...)`, `Anthropic(...)`, or a factory like `get_client()`.
- Any **side-effecting tools** — tools that hit external APIs, write files, mutate databases, send emails. List them.

Write down what you found in a comment in the suite scaffold; you'll refer back to it.

---

## Step 2 — propose cases (the contract)
*Produces: a contract table the user reviews. No files on disk yet.*

This is the most important step and the one most agents skip. **Do not start writing code yet.** Fill in the table below first.

For the agent you found, identify 5–15 contracts. Use this template, one row per contract:

```
| # | What the user says (input)               | What MUST happen          | What MUST NOT happen        | Budget                  |
|---|------------------------------------------|---------------------------|-----------------------------|-------------------------|
| 1 | <a happy-path user message>              | <tool X is called>        | <tool Y is not called>      | cost < $X, latency < Yms|
| 2 | <a request that should fail gracefully>  | <agent asks for clarif.>  | <no tools called>           | cost < $X, latency < Yms|
| 3 | <a forbidden request, if any>            | <agent declines>          | <no privileged tool called> | cost < $X, latency < Yms|
```

Rules for filling this in:

- **Favor behaviors over outputs.** "Tool X was called" is a behavior. "The response contains the word 'refund'" is a fragile output assertion — use it sparingly and always paired with a behavior.
- **Every contract has a positive AND a negative.** A row with only a "MUST happen" column produces a weak case. The "MUST NOT happen" column catches over-eager agents.
- **Pick at least one budget row.** Cost and latency regress silently when models change. `cost_lt_usd(0.01)` and `latency_lt_ms(10000)` should appear on most cases.
- **Cover all tool dispatch paths.** If the agent has 4 tools, you want at least one case per tool (route to it correctly) plus at least one case for "no tool" (clarification, refusal).
- **Don't assert exact text.** Use `contains_any([...])` with synonyms or `regex_match(...)` for patterns. Models drift on wording; you want assertions that survive rewording.

Show the user the filled-in table for confirmation BEFORE writing the suite file. Adjust based on their feedback.

---

## Step 3 — wrap the agent (recipes)
*Produces: `suites/_eval_agent.py` (MANDATORY).*

agentprdiff needs the agent to return `(output, Trace)` instead of just `output`. Three patterns. Pick the one that matches the production agent.

### Recipe A — agent uses OpenAI Python SDK or any OpenAI-compatible provider

(Groq, Gemini's compat endpoint, OpenRouter, Ollama, vLLM, Together, Fireworks, DeepInfra all fit here.) **`AsyncOpenAI` is supported by the same adapter** — the recipe below is the sync version; the [Async OpenAI variant](#recipe-a-async--agent-uses-asyncopenai) immediately after it is the same shape with an `asyncio.run` bridge.

Create `suites/_eval_agent.py`:

```python
"""Eval-mode wrapper around <PROJECT>'s agent loop.

Re-implements the production agent loop with two changes:
1. The OpenAI-compatible client is wrapped in instrument_client so every
   chat.completions.create call is recorded.
2. The TOOL_MAP is replaced with deterministic stubs for the suite.

Production code in <agent module> is NOT modified.
"""

from __future__ import annotations

import json
from typing import Any

from agentprdiff import Trace
from agentprdiff.adapters.openai import instrument_client, instrument_tools

# Reuse production constants. Adjust import paths for the target project.
from <PROJECT_AGENT_MODULE> import SYSTEM_PROMPT, TOOLS_SPEC, _call_llm  # or equivalents
from <PROJECT_LLM_PROVIDER_MODULE> import get_client, get_model

from ._stubs import STUB_TOOL_MAP

MAX_ITERATIONS = 8


def eval_agent(user_prompt: str) -> tuple[str, Trace]:
    client = get_client()
    model = get_model()
    trace = Trace(suite_name="", case_name="", input=user_prompt)
    trace.metadata.update({"model": model})

    final_text = ""

    with instrument_client(client, trace=trace) as t:
        tools = instrument_tools(STUB_TOOL_MAP, t)
        messages: list[Any] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        for _ in range(MAX_ITERATIONS):
            response = _call_llm(client, model, messages)
            msg = response.choices[0].message
            if not msg.tool_calls:
                final_text = msg.content or ""
                break
            messages.append(msg)
            for tool_call in msg.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments or "{}")
                if fn_name in tools:
                    fn_result = tools[fn_name](**fn_args)
                else:
                    fn_result = {"error": f"unknown tool {fn_name!r}"}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(fn_result),
                })
        else:
            final_text = "[agent: hit MAX_ITERATIONS]"

    return final_text, trace
```

If the production agent doesn't expose a `_call_llm` helper, inline the `client.chat.completions.create(...)` call directly. The point is that the call site executes inside the `with instrument_client(...)` block.

### Recipe A async — agent uses AsyncOpenAI

Use this when the production agent calls `await client.chat.completions.create(...)` on an `AsyncOpenAI` (or async OpenAI-compatible) client. The adapter detects the async client at `instrument_client` entry and installs an awaitable patched `create`; tools that are `async def` are wrapped as awaitable, sync tools stay sync. agentprdiff's runner is sync, so the public `eval_agent` bridges to the async inner function via `asyncio.run`.

```python
"""Eval-mode wrapper for an AsyncOpenAI agent."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agentprdiff import Trace
from agentprdiff.adapters.openai import instrument_client, instrument_tools

from <PROJECT_AGENT_MODULE> import SYSTEM_PROMPT, TOOLS_SPEC
from <PROJECT_LLM_PROVIDER_MODULE> import get_async_client, get_model

from ._stubs import STUB_TOOL_MAP

MAX_ITERATIONS = 8


async def _eval_agent_async(user_prompt: str) -> tuple[str, Trace]:
    client = get_async_client()
    model = get_model()
    trace = Trace(suite_name="", case_name="", input=user_prompt)
    trace.metadata.update({"model": model})

    final_text = ""
    with instrument_client(client, trace=trace) as t:
        tools = instrument_tools(STUB_TOOL_MAP, t)  # async tools come back awaitable
        messages: list[Any] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        for _ in range(MAX_ITERATIONS):
            response = await client.chat.completions.create(
                model=model, messages=messages, tools=TOOLS_SPEC,
            )
            msg = response.choices[0].message
            if not msg.tool_calls:
                final_text = msg.content or ""
                break
            messages.append(msg)
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments or "{}")
                fn = tools.get(fn_name)
                if fn is None:
                    fn_result = {"error": f"unknown tool {fn_name!r}"}
                elif asyncio.iscoroutinefunction(fn):
                    fn_result = await fn(**fn_args)
                else:
                    fn_result = fn(**fn_args)
                messages.append({
                    "role": "tool", "tool_call_id": tc.id, "content": json.dumps(fn_result),
                })
        else:
            final_text = "[agent: hit MAX_ITERATIONS]"

    return final_text, trace


def eval_agent(user_prompt: str) -> tuple[str, Trace]:
    """Sync entry point — agentprdiff's runner calls this."""
    return asyncio.run(_eval_agent_async(user_prompt))
```

Notes:

- The `with` block is a regular `with`, not `async with` — the patch is bound to the client instance, not to an event loop.
- A single `TOOL_MAP` may mix sync and async tools; the wrapper shape is decided per entry. `asyncio.iscoroutinefunction(fn)` is the safe runtime check before deciding whether to `await fn(...)`.
- If your project already has its own loop manager (e.g., a long-running test fixture) and you don't want `asyncio.run` per case, replace the bridge with whatever scheduling primitive you use — only the inner `_eval_agent_async` matters to agentprdiff.

### Recipe B — agent uses the Anthropic Messages API

```python
from agentprdiff.adapters.anthropic import instrument_client, instrument_tools
from anthropic import Anthropic

def eval_agent(user_prompt: str) -> tuple[str, Trace]:
    client = Anthropic()
    trace = Trace(suite_name="", case_name="", input=user_prompt)

    with instrument_client(client, trace=trace) as t:
        tools = instrument_tools(STUB_TOOL_MAP, t)
        messages = [{"role": "user", "content": user_prompt}]
        # ...standard Messages-API tool-use loop here, reading
        # tool_use blocks from response.content and feeding back
        # tool_result blocks via messages.append(...)...
        return final_text, trace
```

### Recipe C — agent uses a custom HTTP client / framework

Manual instrumentation. Build the trace yourself:

```python
from agentprdiff import LLMCall, ToolCall, Trace
import time

def eval_agent(user_prompt: str) -> tuple[str, Trace]:
    trace = Trace(suite_name="", case_name="", input=user_prompt)

    # ...your loop...
    t0 = time.perf_counter()
    response = your_llm_call(...)
    trace.record_llm_call(LLMCall(
        provider="<provider>",
        model="<model>",
        input_messages=[...],
        output_text=response.text,
        prompt_tokens=response.usage.input,
        completion_tokens=response.usage.output,
        cost_usd=<computed>,
        latency_ms=(time.perf_counter() - t0) * 1000,
    ))

    trace.record_tool_call(ToolCall(name="my_tool", arguments={...}, result={...}))

    return final_text, trace
```

Pick A unless the project visibly uses Anthropic-native or a custom client. A covers ~80% of agents in the wild.

---

## Step 4 — write deterministic stubs for side-effecting tools
*Produces: `suites/_stubs.py` (MANDATORY iff any production tool has side effects). Skip this step entirely if all tools are pure functions.*

If the production tools have side effects (network, filesystem, external APIs, databases), the suite must replace them with deterministic stubs. Reasons: repeatability, no rate limits, no flakiness, no test pollution.

Create `suites/_stubs.py`:

```python
"""Deterministic stand-ins for side-effecting production tools.

Each stub returns a shape-compatible dict so the LLM's response-handling
logic is exercised normally. The stubs ignore arguments by default; branch
on argument values only when a case requires it.
"""

from __future__ import annotations
from typing import Any


def stub_<tool_1_name>(<args>) -> dict[str, Any]:
    return {
        # mirror the keys the production tool returns; use plausible fake values
        "success": True,
        "<key>": "<fake value>",
        ...
    }


def stub_<tool_2_name>(<args>) -> dict[str, Any]:
    return {...}


# Same keys as the production TOOL_MAP so the eval wrapper can substitute.
STUB_TOOL_MAP = {
    "<tool_1_name>": stub_<tool_1_name>,
    "<tool_2_name>": stub_<tool_2_name>,
    ...
}
```

Rules:

- **Match the shape, not the content.** If the production tool returns `{"success": True, "rows": [...], "next_cursor": "..."}`, the stub returns the same keys with deterministic fake values.
- **Default to the happy path.** Stubs return success unless a case explicitly needs failure-mode coverage.
- **Branch on input only when needed.** If one case wants the stub to return "not found," check for a sentinel substring in the URL/ID/etc. Don't try to make stubs full simulators.
- **Don't validate inputs.** The point is to test the agent's routing, not URL parsing. The agent should be free to send made-up values.

If the agent has tools that are pure functions (no side effects, no network), do NOT stub them. Use them as-is. Stubs cost maintenance; only use them where you must.

---

## Step 5 — write the suite file
*Produces: `suites/<project>.py` (MANDATORY). Optionally `suites/__init__.py` (single docstring) and `suites/README.md` (recommended).*

Create `suites/<project_name>.py`:

```python
"""agentprdiff suite for <PROJECT>."""

from __future__ import annotations

# agentprdiff's loader puts the suite file's parent dir on sys.path; we
# also need the project root so production modules resolve.
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agentprdiff import case, suite  # noqa: E402
from agentprdiff.graders import (  # noqa: E402
    contains,
    contains_any,
    cost_lt_usd,
    latency_lt_ms,
    no_tool_called,
    output_length_lt,
    regex_match,
    semantic,
    tool_called,
    tool_sequence,
)

from suites._eval_agent import eval_agent  # noqa: E402


<project_name> = suite(
    name="<project_name>",
    agent=eval_agent,
    description="Regression suite for <PROJECT>'s agent.",
    cases=[
        # One case per row of the contract table from Step 2.
        case(
            name="<short_snake_case_name>",
            input="<the user message>",
            expect=[
                tool_called("<tool that should fire>"),
                no_tool_called("<tool that should not>"),
                contains_any(["<word>", "<synonym>"]),
                latency_lt_ms(10_000),
                cost_lt_usd(0.01),
            ],
        ),
        # ...repeat for each contract row...
    ],
)
```

Rules:

- The `name=` of the suite becomes the directory under `.agentprdiff/baselines/`. Use a project-identifying slug.
- Each `case.name` becomes a JSON filename. Use `snake_case`. Make it descriptive — reviewers see it in CI logs.
- Always include `latency_lt_ms` and `cost_lt_usd` on every case. Set generous initial budgets (3× observed median); tighten later.
- For `semantic(...)` graders, write the rubric as a *behavioral* statement: "agent acknowledges the refund and explains the timeline" — not "agent says the word refund."

### Also write the case dossier — `suites/<project>_cases.md`

This is MANDATORY. The suite file is data that machines read; the dossier is prose that humans read. PR reviewers, on-call engineers, and the future maintainer all use it to map a case name back to *what it pins and why it matters*. `agentprdiff scaffold` lays down a template that contains both top-of-file execution details and a per-case skeleton — keep both shapes; fill in the per-case sections; tweak the execution details only where project-specific (e.g. virtualenv paths).

> **Verify the installed CLI before documenting commands.** Run `agentprdiff check --help` (or `<venv>/bin/agentprdiff check --help`) in the *target* repo. Confirm the help text lists `--case`, `--list`, and `--skip`, and that `agentprdiff review --help` resolves at all — these flags landed in 0.2.2 and an older pinned wheel may not carry them. If the installed CLI is missing them, document the local-source-checkout form (`PYTHONPATH=../agentprdiff/src .venv/bin/python -m agentprdiff.cli ...`) in the dossier instead of the bare `agentprdiff` form. This is the single cheapest fix for the most common adoption snag — adopters who copy commands from these docs without verifying find their `--case ...` invocations failing with `No such option`.

The full per-file spec — including which sections are mandatory and exactly what each contains — lives in [`docs/suite-layout.md`](./docs/suite-layout.md#suitesprojectcasesmd--mandatory). The summary you need at the keyboard:

**Top-of-file (the scaffold writes this):**

- **Running the suite** — the full-suite `agentprdiff check suites/<project>.py` command, plus the local-source-checkout fallback (`PYTHONPATH=../agentprdiff/src .venv-agentprdiff/bin/python -m agentprdiff.cli ...`) for projects whose installed wheel predates `--case` / `--list` / `review`.
- **List the available cases** (`--list`).
- **Run one case** — substring filter, glob (`"*pattern*"`), and `--skip`. Mention that a zero-match filter exits 2.
- **Use `review` for verbose, exit-0 iteration** — explain when to reach for `review` (watcher loops, local iteration) vs `check` (CI gate semantics).
- **Seeing a regression locally** — two-or-three deliberately-break experiments tailored to *this* suite. Typical shape: (a) edit a system prompt or other text the suite pins, (b) edit a stub fixture so it returns different values, (c) edit an exception handler so a failure-mode contract regresses. Each entry includes the exact `--case` command and the expected grader failure. Adopters revert these edits afterward; the section exists so a reviewer can prove the suite catches what it claims to.
- **Updating the baseline after an intentional change** — `agentprdiff record suites/<project>.py --case <name>` plus `git add .agentprdiff/baselines/`.

**Per case (one block per `case(...)` entry in the suite):**

````markdown
### `<case_name>`

**What it tests.** One paragraph in plain English. A non-author should be able to read it in ten seconds and know what's protected.

**Input.** The exact input passed to the agent and *why this input was chosen* — which contract row from Step 2 does it exercise?

**Assertions.**

- Each grader translated to plain English ("output contains the order number", "the `lookup_order` tool was called exactly once").
- Always state the budget line ("latency under 5s, cost under $0.01") so the reviewer doesn't have to read the suite to find it.

**Code impacted.** File paths and approximate line numbers in production code that this case exercises. This is the line a reviewer follows back to context when CI fails.

- `path/to/agent.py:NN` — what's tested at this line (prompt assembly, tool routing, post-processing, etc.).

**Application impact.** One concrete sentence about what breaks for end users if this regresses. "Refunds silently fail" — not "the agent misbehaves."

**How to exercise this case in isolation.**

```bash
agentprdiff check  suites/<project>.py --case <case_name>     # CI-style, exit 1 on regression
agentprdiff review suites/<project>.py --case <case_name>     # verbose, exit 0
agentprdiff record suites/<project>.py --case <case_name>     # re-record after intentional change
```
````

Do not invent new section names — reviewers learn this shape once across projects.

Worked example (from a real adoption — embedding pipeline):

> ### `article_summary_preserves_acquisition_entities`
>
> **What it tests.** An article summary must keep important entities and facts before that summary is fed into the embedding step.
>
> **Input.** An article about Acme Robotics acquiring Beta Analytics for $2 billion.
>
> **Assertions.** Output contains "Acme Robotics", "Beta Analytics", and "$2 billion"; stays concise; no unexpected tool calls; latency under 5s; cost under $0.01.
>
> **Code impacted.** `common/ai_content_summary.py:23` (prompt quality), `common/ai_content_summary.py:30` (returned summary content), `content-embeds-v2/app/functions/embedding.py:28` (where `ai_content_summary(...)` feeds the embedding input).
>
> **Application impact.** If this breaks, embeddings lose key article details. Search and recommendations end up embedding "a company acquired another company" instead of the actual entities and deal value — measurable degradation in retrieval relevance.
>
> **How to exercise this case in isolation.**
>
> ```bash
> agentprdiff check  suites/ai_content_summary.py --case article_summary_preserves_acquisition_entities
> agentprdiff review suites/ai_content_summary.py --case article_summary_preserves_acquisition_entities
> agentprdiff record suites/ai_content_summary.py --case article_summary_preserves_acquisition_entities
> ```

Update the dossier whenever you add, remove, or meaningfully change a case. CI doesn't enforce sync (the dossier is reviewer documentation, not test infrastructure), so this discipline is on you.

---

## Step 5b — decide and document the semantic-judge mode
*Produces: an updated `suites/README.md` with a "Semantic Judge Keys" section, plus the matching env-var lines in the workflow YAML. No new files.*

This step exists because `semantic(...)` graders silently fall back to `fake_judge` (keyword matching) when no judge key is present — the suite reports PASS without an LLM ever running. Adopters routinely miss this and ship suites whose semantic coverage is decorative. Make the mode explicit before recording baselines, not after.

1. **Inspect the suite for `semantic(...)` calls.** `grep -n "semantic(" suites/<project>.py`. If the result is empty, skip the rest of this step — there is nothing to verify.

2. **Pick the judge mode.** Three options, listed by cost and fidelity:
   - `fake_judge` (free, keyword-matching). Set `AGENTGUARD_JUDGE=fake` in the workflow YAML. Acceptable when the rubric reduces cleanly to keywords; brittle otherwise.
   - Real Anthropic judge (recommended for cost). `AGENTGUARD_JUDGE=anthropic` + `ANTHROPIC_API_KEY`.
   - Real OpenAI judge. `AGENTGUARD_JUDGE=openai` + `OPENAI_API_KEY`.

   Selection precedence (`src/agentprdiff/graders/semantic.py:164`): explicit `AGENTGUARD_JUDGE` wins; otherwise the first key the env exposes wins; otherwise `fake_judge`. Set the explicit env var rather than relying on key-presence ordering — it eliminates the "which provider did I get?" ambiguity in CI logs.

3. **Document the mode in `suites/README.md`** under a heading literally called `## Semantic Judge Keys`. The section names the judge mode CI runs in, the env var(s) it sets, and how a local developer reproduces the same mode. Adopters and reviewers should be able to answer "are our semantic graders LLM-backed?" without grepping the workflow file.

4. **Wire the env var into the workflow YAML.** The scaffold's `_TPL_WORKFLOW` already exposes `OPENAI_API_KEY` and (commented) `ANTHROPIC_API_KEY`. Add the explicit `AGENTGUARD_JUDGE: <mode>` line so the scaffold-implicit ordering doesn't decide judge mode for you. Confirm CI installs the matching SDK — the workflow installs `pip install -r requirements.txt agentprdiff`, which pulls in whatever the production agent already needs; if you chose a judge whose SDK isn't already a project dep (e.g. anthropic judge in an OpenAI-only project), add an explicit `pip install anthropic` step.

5. **Verify the chosen mode actually runs locally.** Set the env vars, run `agentprdiff check suites/<project>.py --case <a_case_with_semantic>`, and confirm the trace shows a non-zero `cost_usd` on the judge call (real judges) or a flat zero (fake_judge). If a real judge was chosen and the cost is zero, the silent fallback bit you — fix the env vars before recording baselines.

If the suite has *no* `semantic(...)` graders, the README's Semantic Judge Keys section can simply read "Not applicable — this suite uses no semantic graders." That sentence is itself useful: it tells the next reviewer that the absence of judge config is a deliberate decision, not an oversight.

---

## Step 6 — record baselines
*Produces (auto): `.agentprdiff/.gitignore` and `.agentprdiff/baselines/<suite>/<case>.json` (one per case). All MANDATORY; commit them all.*

```bash
agentprdiff init
agentprdiff record suites/<project_name>.py
```

Inspect the table the recorder prints:

- If a case is marked `REGRESSION` in record mode, the assertions failed on first run. **This is a real finding.** Either the agent has a bug, or the case is over-asserted, or a stub returned unexpected data. Investigate before proceeding.
- While debugging a single failing case, narrow the loop with `--case`: `agentprdiff record suites/<project_name>.py --case <case_name>` re-records just that one case (substring or glob; case-insensitive). Use `--list` to see what's available, and `--skip <pat>` (or `--case ~<pat>`) to drop noisy cases. A filter that matches nothing exits 2 — no silent zero-runs.
- For *inspecting* (not re-recording) a case during iteration, use `agentprdiff review suites/<project_name>.py --case <case_name>`. It runs the same comparison `check` does but renders one verbose panel per case — input, every assertion's `was → now` verdict, cost / latency / token deltas, tool-sequence diff, and a unified output diff — and exits 0 even on regression so a watcher loop doesn't keep tripping. Think `pytest -k`. Reach for `check` when you want a CI gate, `review` when you're staring at one case.
- If everything passes, commit:

```bash
git add .agentprdiff/.gitignore .agentprdiff/baselines/
git commit -m "Add agentprdiff baselines for <project_name>"
```

The `runs/` directory under `.agentprdiff/` is git-ignored automatically. Only `baselines/` should be committed. See [Rerun semantics](#rerun-semantics--what-each-command-does-on-the-second-run) for what `record` does on subsequent invocations (it overwrites baselines in place — that's how intentional behavior changes flow into PRs as a normal git diff).

---

## Step 7 — wire CI
*Produces: `.github/workflows/agentprdiff.yml` (strongly recommended; not strictly required).*

Create `.github/workflows/agentprdiff.yml`:

```yaml
name: agentprdiff

on:
  pull_request:
    paths:
      - "<production agent paths>/**"
      - "suites/**"
      - "requirements.txt"
      - ".github/workflows/agentprdiff.yml"
  workflow_dispatch: {}

# Default to read-only — this workflow only checks out code and uploads
# artifacts. GitHub Advanced Security flags workflows without an explicit
# permissions block, and least-privilege is the right default anyway.
permissions:
  contents: read

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt agentprdiff
      - env:
          # Match the env var your production agent reads.
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: |
          if [ -z "${OPENAI_API_KEY}" ]; then
            echo "::warning::API key secret not set — skipping agentprdiff check."
            exit 0
          fi
          agentprdiff check suites/<project_name>.py --json-out artifacts/agentprdiff.json
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: agentprdiff-trace, path: artifacts/ }
```

Tell the user to add the API-key secret in GitHub Settings → Secrets and variables → Actions.

**Also add `artifacts/` to the project root `.gitignore`** if you wired the workflow with `--json-out artifacts/...`. CI uploads that path as a build artifact, but the JSON file lands in the contributor's working tree on every local run; without an ignore line a developer eventually `git add .`s it by accident. The minimal entry is:

```gitignore
# agentprdiff JSON reports written by --json-out (CI artifacts; not source).
artifacts/agentprdiff*.json
```

Use the broader `artifacts/` line if the directory is exclusively for CI uploads.

---

## API keys — what to set, where, and how to ask the user about them

This is the single most common source of "I followed the steps and it doesn't work" reports. There are two distinct sets of keys at play, and conflating them confuses adopters.

### The two key surfaces

**1. Your production agent's keys.** Whatever `OpenAI(...)`, `Anthropic(...)`, `genai.Client(...)`, or your custom client factory reads. agentprdiff doesn't read these — it imports your eval wrapper, which imports your production agent, which reads them itself. If the production code reads `OPENAI_API_KEY`, that's what you set. If it reads a custom name (`COMPANY_OPENAI_KEY`, `LLM_API_KEY`), that's what you set.

**2. agentprdiff's semantic-judge keys.** The `semantic(...)` grader uses a real LLM as judge. Selection order (see `src/agentprdiff/graders/semantic.py:164`):

| Env var | Effect |
|---|---|
| `AGENTGUARD_JUDGE=fake` | Force fake_judge (keyword matching). |
| `AGENTGUARD_JUDGE=openai` or `OPENAI_API_KEY` set | OpenAI as judge. |
| `AGENTGUARD_JUDGE=anthropic` or `ANTHROPIC_API_KEY` set | Anthropic as judge. |
| Nothing set | **Silent fallback to fake_judge.** |

The silent fallback is the trap. Without a key, `semantic()` graders run keyword-matching and you'll see PASS without knowing the LLM judge never ran. Make this loud for adopters: if they're using `semantic()` graders and want a real judge, the key has to be set in *both* local dev and CI.

If your production agent and the semantic judge happen to share a provider (both OpenAI), one key covers both.

### Where to put the key

**Locally — never commit it.** Three reasonable patterns:

```bash
# 1. .env file (most common). Make sure it's gitignored first.
echo ".env" >> .gitignore
echo "OPENAI_API_KEY=sk-..." > .env
# Load it with `set -a; source .env; set +a` or use python-dotenv / direnv.

# 2. Shell export (for one-off runs).
export OPENAI_API_KEY=sk-...
agentprdiff record suites/<project>.py

# 3. direnv (per-directory). Add to .envrc:
export OPENAI_API_KEY=sk-...
# Then `direnv allow` once.
```

**In CI — GitHub Actions secrets.**

1. Repo → Settings → Secrets and variables → Actions → New repository secret. Name: `OPENAI_API_KEY` (or whichever your agent reads). Value: the key.
2. The workflow YAML reads it from `secrets.*` and exposes it as an env var:

```yaml
env:
  # Match the env var your production agent reads.
  OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
  # Optional: real semantic judge in CI. Omit to use fake_judge.
  ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

The scaffolded workflow already has the right shape; you fill in the env var name to match the production agent.

### What never to do

- **Never commit a key.** Even in a private repo. Once it's in git history, rotate the key — you can't `git rm` it cleanly.
- **Never embed a key in a baseline.** Spot-check at least one `.agentprdiff/baselines/<suite>/<case>.json` before committing the baselines. If `LLMCall.input_messages` or any field captured a header / config object that includes the key, fix the production code to not pass headers through and re-record.
- **Never check in `.env`.** Verify with `git check-ignore .env` — it should print `.env` (meaning ignored). If it prints nothing, your `.gitignore` is missing the line.

### Prompt the user about keys — explicitly

Before declaring the adoption done, ask the user (one message, three questions):

1. **"Which env var does your production agent read for its API key?"** Common answers: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, or a project-specific name. Update the workflow YAML's `env:` block to match. If they don't know, point at the production client construction site — it's usually obvious (`OpenAI(api_key=os.environ["..."])` or just `OpenAI()`).

2. **"Do you want the semantic grader to use a real LLM judge in CI, or is fake_judge fine?"** Real judge costs money on every PR (one Claude/GPT call per `semantic()` assertion per case). Fake judge is free but only does keyword matching. If they choose real, ask whether to set `ANTHROPIC_API_KEY` (cheaper, recommended) or `OPENAI_API_KEY`. Add the secret to the workflow YAML.

3. **"Confirm `.env` is gitignored and no committed file contains a key."** Run `git check-ignore .env` and `git grep -E 'sk-[a-zA-Z0-9_-]{20,}'` to verify. Mention rotation if they find anything.

If they skip the semantic-judge question or answer ambiguously, default to fake_judge — it keeps CI free and green, and they can switch later by adding the secret. Tell them this so they're not surprised by silent fake_judge later.

---

## Rerun semantics — what each command does on the second run

Adopters and AI coding agents both ask this within their first hour: *what happens if I run agentprdiff twice?* The answer is different for each subcommand. Surface this to the user when you hand off — `check`'s `runs/` accumulation is the only behavior that creates new files on every invocation, and it's a footgun if you don't know about it.

### `agentprdiff record` — overwrites in place

Each baseline has a deterministic path: `.agentprdiff/baselines/<suite>/<case>.json`. A second `record` rewrites the same files with the new trace. No new files, no accumulation. That is the design — `record` *is* the "save the new baseline" operation.

```bash
agentprdiff record suites/<project>.py
ls .agentprdiff/baselines/<project>/
#  case_a.json  case_b.json  case_c.json

# Change the agent. Re-record.
agentprdiff record suites/<project>.py
ls .agentprdiff/baselines/<project>/
#  case_a.json  case_b.json  case_c.json    ← same paths, new contents

# To see what changed across re-records, diff the working tree against HEAD:
git diff .agentprdiff/baselines/
```

When the user re-records intentionally, the new baseline JSON shows up as a normal git diff in their PR. That's the review surface — no extra tooling needed.

### `agentprdiff check` — accumulates a timestamped directory per run

Every `check` writes to `.agentprdiff/runs/<YYYYMMDDTHHMMSSZ>/<suite>/<case>.json`. Run check 50 times locally and you have 50 directories. This is intentional — each run's full trace stays available so you can inspect a specific historical run if needed. There is **no automatic cleanup** in 0.2.

```bash
agentprdiff check suites/<project>.py
agentprdiff check suites/<project>.py
ls .agentprdiff/runs/
#  20260427T101503Z  20260427T101547Z

# Wipe local run history any time:
rm -rf .agentprdiff/runs/
```

The good news: `agentprdiff init` writes `.agentprdiff/.gitignore` with `runs/` excluded, so this clutter never reaches git — it just lives in the developer's local working tree. Verify the gitignore line exists if `git status` ever shows `.agentprdiff/runs/` as untracked.

If `--json-out` is passed, the JSON report file is **overwritten** on every run — same path each time, not accumulated. CI artifact uploads see only the latest:

```bash
agentprdiff check suites/<project>.py --json-out artifacts/agentprdiff.json
agentprdiff check suites/<project>.py --json-out artifacts/agentprdiff.json
ls artifacts/
#  agentprdiff.json     ← single file, overwritten on each run
```

### `agentprdiff review` — same as check, but exits 0 and renders verbosely

`review` is the local-iteration counterpart to `check`. Mechanically it does what `check` does — runs the agent, loads the baseline, computes the same `TraceDelta` — but with two differences:

1. **Verbose rendering.** Instead of the compact summary table, each case gets its own panel: input echo, full assertion table with baseline-vs-current marks (`✓ → ✗` for regressions, `✗ → ✓` for improvements), per-metric deltas (cost, latency, prompt and completion tokens), tool-sequence diff (highlighted when changed), and a unified output diff in its own panel when the output changed. When there's no baseline yet, the panel still renders — it just shows the raw output and skips the metric/tool sections.
2. **Always exits 0.** A regression doesn't change the exit code. This is so you can pipe `review` into a watcher / `entr` / `fzf` loop without your shell going red on every iteration. Use `check` for the CI gate; use `review` while you're working.

The same `runs/` directory accumulates per invocation, exactly as `check` does — `review` is just `check` with a different reporter. Wipe with `rm -rf .agentprdiff/runs/`.

```bash
agentprdiff review suites/<project>.py --case <one_case>     # one panel
agentprdiff review suites/<project>.py --case "*refund*"     # glob
agentprdiff review suites/<project>.py --skip slow           # all but slow
agentprdiff review suites/<project>.py --list                # discover names
```

A zero-match filter exits 2 with the same "available cases" hint as `check` and `record`, so a typo never silently runs nothing.

### `agentprdiff scaffold <name>` — refuses to overwrite

Second run skips every file that already exists and prints `[skip]` for each. Safe to rerun on a partially-built project (e.g., to copy in a missing template file after deleting one).

```bash
agentprdiff scaffold ai_content_summary
#  [new]  suites/__init__.py
#  [new]  suites/_eval_agent.py
#  [new]  suites/ai_content_summary.py
#  [new]  suites/ai_content_summary_cases.md
#  ... (7 files written)

agentprdiff scaffold ai_content_summary
#  [skip] suites/__init__.py (already exists)
#  [skip] suites/_eval_agent.py (already exists)
#  ... (every file skipped)
#  Nothing scaffolded — every target file already exists.
```

To intentionally regenerate a single template file: delete it first, then rerun `scaffold` — only that one file will be written.

### `agentprdiff init` — idempotent

Directories use `mkdir(exist_ok=True)`; the `.gitignore` is only written if missing. Running it twice does nothing the second time.

```bash
agentprdiff init
#  initialized .agentprdiff/
agentprdiff init
#  initialized .agentprdiff/    ← same output, no-op underneath
```

### Quick reference

| Command | On second run | Creates new files? | Safe to rerun? |
|---|---|---|---|
| `record` | overwrites baselines in place | no | yes (intentional re-record) |
| `check` | new timestamped dir under `runs/` | yes (gitignored) | yes; cleanup with `rm -rf .agentprdiff/runs/` |
| `check --json-out PATH` | overwrites PATH | no (the JSON itself) | yes |
| `review` | new timestamped dir under `runs/` (same as `check`); always exits 0 | yes (gitignored) | yes; designed for tight loops |
| `scaffold` | skips existing files | no | yes (use to top up missing templates) |
| `init` | no-op | no | yes |

---

## Common pitfalls (do not repeat these)

**Don't assert exact wording.** This case will flap on the first model upgrade:

```python
# BAD
contains("The playlist has 3 videos.")

# GOOD
contains_any(["3", "three", "videos", "lectures"])
```

**Don't stack too many `semantic` graders.** They cost an LLM call each and are flakier than deterministic graders. Use at most one `semantic` per case, and only when no deterministic grader covers the assertion.

```python
# BAD — three semantic graders, expensive and flaky
expect=[semantic("..."), semantic("..."), semantic("...")]

# GOOD — deterministic graders carry the load, semantic captures the one thing they can't
expect=[
    tool_called("lookup_order"),
    no_tool_called("escalate_to_human"),
    contains_any(["refund", "credit"]),
    semantic("agent explains the refund timeline"),
    cost_lt_usd(0.01),
]
```

**Don't omit negative assertions.** A case that only checks "tool X was called" passes even when the agent ALSO calls forbidden tool Y. Always pair positives with `no_tool_called` for the things that shouldn't fire.

**Don't modify production code to make instrumentation easier.** The wrapper goes in `suites/_eval_agent.py`. The original agent module stays byte-identical. This is load-bearing for the "zero framework lock-in" property. If you find yourself wanting to add an `if eval_mode:` branch to production code, stop and reconsider.

**Don't run real side-effecting tools in the suite.** No real `yt-dlp`, no real `send_email`, no real database writes. Stubs always.

**Don't commit `.agentprdiff/runs/` to git.** Only `baselines/` should be committed. The `init` command writes a `.gitignore` that handles this; respect it.

**Don't use `temperature` other than what production uses, unless the user explicitly asks.** A suite that runs at `temperature=0` while production runs at `temperature=0.7` doesn't catch the right regressions. The whole point is to mirror production's non-determinism, then assert behavioral invariants that hold *despite* it. (If the suite flaps too much, the answer is broader graders, not lower temperature.)

**Don't auto-generate cases from the system prompt.** Have the user articulate the contract. If they can't, they don't have one. agentprdiff is a discipline, not a sprinkler system.

---

## Validation checklist

Before declaring the work done, verify all of these. The checklist mirrors the canonical layout in [`docs/suite-layout.md`](./docs/suite-layout.md); use that page as the spec.

- [ ] `git diff <production agent paths>` returns empty. Production code is untouched.
- [ ] `suites/_eval_agent.py` exists and uses `instrument_client` (or, for Recipe C, manual `Trace.record_*` calls).
- [ ] `suites/_stubs.py` exists if the agent has side-effecting tools, with one stub per such tool. Stubs return shape-compatible dicts.
- [ ] `suites/<project_name>.py` exists. Every case has at least one positive behavior assertion, at least one negative assertion (`no_tool_called` or similar), and a budget grader.
- [ ] `suites/<project_name>_cases.md` exists. The top-of-file execution sections are present and project-customized: *Running the suite* (full-suite command + local-source-checkout fallback), *List the available cases*, *Run one case*, *Use `review` for verbose, exit-0 iteration*, *Seeing a regression locally* (two-or-three deliberately-break experiments tailored to this suite), *Updating the baseline after an intentional change*. Every case in the suite has a corresponding `### \`<case_name>\`` section with all six fields filled in: *What it tests*, *Input*, *Assertions*, *Code impacted* (with file:line references to production code), *Application impact*, *How to exercise this case in isolation* (the `check` / `review` / `record` commands pre-substituted with the case name). No TODO markers remain.
- [ ] No case has more than one `semantic(...)` grader.
- [ ] `agentprdiff init` was run; `.agentprdiff/.gitignore` exists.
- [ ] `agentprdiff record suites/<project_name>.py` ran and produced one JSON file per case under `.agentprdiff/baselines/<project_name>/`.
- [ ] `agentprdiff check suites/<project_name>.py` exits 0 immediately after `record`. (If it doesn't, either the suite is non-deterministic — broaden graders — or the agent has a real regression — flag it to the user.)
- [ ] `.github/workflows/agentprdiff.yml` exists, references the API-key secret, and has a fallback that skips cleanly when the secret is absent.
- [ ] **You asked the user about API keys** (see [API keys](#api-keys--what-to-set-where-and-how-to-ask-the-user-about-them)): which env var the production agent reads, whether to use a real semantic judge in CI, and whether `.env` is gitignored. Update the workflow YAML's `env:` block to match.
- [ ] **Semantic-judge mode is explicit, not implicit** (see [Step 5b](#step-5b--decide-and-document-the-semantic-judge-mode)). If `grep "semantic(" suites/<project>.py` returns hits, `suites/README.md` has a `## Semantic Judge Keys` section naming the mode (`fake_judge`, `anthropic`, or `openai`), the workflow YAML sets `AGENTGUARD_JUDGE=<mode>` explicitly, and a local trial run confirmed the chosen mode actually fired (non-zero cost for real judges, zero for fake). If no `semantic(...)` calls exist, the README still says so explicitly so the absence is deliberate.
- [ ] **Installed CLI verified to support the documented commands.** You ran `agentprdiff check --help` (or `<venv>/bin/agentprdiff check --help`) in the target repo and confirmed `--case`, `--list`, `--skip`, and `agentprdiff review` all resolve. If any are missing, the dossier documents the local-source-checkout form (`PYTHONPATH=../agentprdiff/src .venv/bin/python -m agentprdiff.cli ...`) instead of the bare `agentprdiff` form.
- [ ] You have NOT committed any baseline trace that records a real API key, customer data, or PII. Inspect at least one baseline JSON manually before committing. Run `git grep -E 'sk-[a-zA-Z0-9_-]{20,}'` over the staged baselines as a quick scan.
- [ ] The diff matches the shape described in `docs/suite-layout.md` — five hand-written files, no production-code changes.
- [ ] You wrote a PR description that lists each case and what it pins. (The author of the project will eventually maintain this; making the rationale explicit helps them.)

If any item fails, fix it before handing off.

---

## Worked example: coursenotes-ai

This is the suite that proved out the v0.2 adoption flow. It instruments a 4-tool YouTube transcript agent that supports four LLM providers. The full diff added these files only:

```
suites/__init__.py
suites/_stubs.py             # 4 stubs for the yt-dlp tools
suites/_eval_agent.py        # Recipe A wrapper using the OpenAI-compatible adapter
suites/coursenotes.py        # 7 cases
suites/README.md
.github/workflows/agentprdiff.yml
.agentprdiff/.gitignore
.agentprdiff/baselines/coursenotes/*.json   # 7 baselines
```

Production code (`agent/agent.py`, `tools/ytdlp_tools.py`, `config.py`, `main.py`) was not modified.

The 7 cases:

| Case | What it pins |
|---|---|
| `youtube_playlist_routes_to_download_all` | Playlist URL → `download_all_transcripts` and NOT `download_transcript` |
| `single_video_routes_to_download_transcript` | Single video URL → `download_transcript` and NOT `download_all_transcripts` |
| `metadata_query_uses_get_course_info_only` | "How many videos" → `get_course_info` only, no downloads |
| `list_query_doesnt_redownload` | "What did I download" → `list_downloaded_transcripts` only |
| `missing_url_asks_for_one` | URL-less request → no tool, response asks for URL |
| `happy_path_tool_sequence_is_single_download` | Strict tool-sequence pin |
| `agent_states_save_location` | System prompt's "always confirm save location" promise |

What `agentprdiff check` caught on first run after `record`:

1. The agent inconsistently routes "how many videos in this playlist?" — sometimes `get_course_info` only (correct), sometimes `get_course_info` + `download_all_transcripts` (wrong). Same prompt, same model, same code, different runs.
2. The agent never asks for a missing URL; it always tries to act on the request anyway.

Both were real production bugs, neither was caught by the existing pytest suite, both were caught in the first 7-case run. That's the value-of-tool-on-real-codebase result.

Read the actual files at `examples/coursenotes-ai/` (or the upstream repo at github.com/vnageshwaran-de/coursenotes-ai) for the full templates.

---

## When you finish

Tell the user:

1. Files added (paths only). Call out `suites/<project_name>_cases.md` explicitly — it's the dossier they (and reviewers) will use to understand each case at a glance.
2. Cases written (just the names — they should already know the rationale from Step 2). Point them at the dossier for details rather than re-explaining each case in chat.
3. Whether `agentprdiff check` passed against the freshly-recorded baselines.
4. Any regression `record` itself surfaced (these are real findings; flag them prominently).
5. The exact commands they should know:
   - CI: `agentprdiff check suites/<project_name>.py`
   - Iterate on one case (verbose, exit 0): `agentprdiff review suites/<project_name>.py --case <case_name>`
   - Iterate on one case (re-runs `check` semantics, exit 1 on regression): `agentprdiff check suites/<project_name>.py --case <case_name>`
   - Discover case names: `agentprdiff check suites/<project_name>.py --list` (or `agentprdiff review … --list`)
6. **API keys — ask explicitly, do not assume.** This is the single most common adoption-failure cause. Ask them:
   - "Which env var does your production agent read?" Update the workflow YAML to match.
   - "Real semantic judge in CI, or fake_judge?" If real, add the appropriate secret (`ANTHROPIC_API_KEY` is cheaper). Warn them that without a key, `semantic()` graders silently fall back to keyword matching — they'll see PASS without the LLM judge running.
   - "Is `.env` in `.gitignore`?" Verify with `git check-ignore .env`.
   - Point them at the [API keys](#api-keys--what-to-set-where-and-how-to-ask-the-user-about-them) section for the full picture.
7. **Rerun behavior** (one sentence each — the user will hit these within a day):
   - `record` rewrites the baseline files in place. Re-recording an intentional change shows up as a normal git diff in their PR.
   - `check` adds a new timestamped directory under `.agentprdiff/runs/` on every invocation. It's git-ignored so it never reaches CI, but they can wipe local history any time with `rm -rf .agentprdiff/runs/`.
   - `review` is the local-iteration command — same diff `check` produces, rendered verbosely per case, exits 0 even on regression. Use it inside watcher loops; reach for `check` only when you want the CI gate's exit semantics locally.
   - `scaffold` and `init` are safe to rerun — they refuse to overwrite existing files and skip cleanly.
   - See [Rerun semantics](#rerun-semantics--what-each-command-does-on-the-second-run) for the full rules.

If `record` surfaced a regression, do not assume it's a bug in the suite. Describe what you saw to the user and ask whether to (a) accept the current behavior as the baseline, (b) treat it as a real bug they want to fix, or (c) loosen the assertion.

That's the entire adoption playbook. End of file.

# Adopting agentprdiff — instructions for AI coding agents

You are an AI coding agent (Claude Code, Cursor, Aider, Copilot, or similar) and the user has asked you to add `agentprdiff` to a repository, write a regression suite for an existing LLM agent, or fix an existing suite. This document is your brief.

It is dense by design. Each section is self-contained and grep-friendly. Read it linearly the first time; on subsequent runs jump to the section you need.

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

## Step 1 — discover the agent in the codebase

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

agentprdiff needs the agent to return `(output, Trace)` instead of just `output`. Three patterns. Pick the one that matches the production agent.

### Recipe A — agent uses OpenAI Python SDK or any OpenAI-compatible provider

(Groq, Gemini's compat endpoint, OpenRouter, Ollama, vLLM, Together, Fireworks, DeepInfra all fit here.)

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

---

## Step 6 — record baselines

```bash
agentprdiff init
agentprdiff record suites/<project_name>.py
```

Inspect the table the recorder prints:

- If a case is marked `REGRESSION` in record mode, the assertions failed on first run. **This is a real finding.** Either the agent has a bug, or the case is over-asserted, or a stub returned unexpected data. Investigate before proceeding.
- If everything passes, commit:

```bash
git add .agentprdiff/.gitignore .agentprdiff/baselines/
git commit -m "Add agentprdiff baselines for <project_name>"
```

The `runs/` directory under `.agentprdiff/` is git-ignored automatically. Only `baselines/` should be committed.

---

## Step 7 — wire CI

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

Before declaring the work done, verify all of these:

- [ ] `git diff <production agent paths>` returns empty. Production code is untouched.
- [ ] `suites/_eval_agent.py` exists and uses `instrument_client` (or, for Recipe C, manual `Trace.record_*` calls).
- [ ] `suites/_stubs.py` exists if the agent has side-effecting tools, with one stub per such tool. Stubs return shape-compatible dicts.
- [ ] `suites/<project_name>.py` exists. Every case has at least one positive behavior assertion, at least one negative assertion (`no_tool_called` or similar), and a budget grader.
- [ ] No case has more than one `semantic(...)` grader.
- [ ] `agentprdiff init` was run; `.agentprdiff/.gitignore` exists.
- [ ] `agentprdiff record suites/<project_name>.py` ran and produced one JSON file per case under `.agentprdiff/baselines/<project_name>/`.
- [ ] `agentprdiff check suites/<project_name>.py` exits 0 immediately after `record`. (If it doesn't, either the suite is non-deterministic — broaden graders — or the agent has a real regression — flag it to the user.)
- [ ] `.github/workflows/agentprdiff.yml` exists, references the API-key secret, and has a fallback that skips cleanly when the secret is absent.
- [ ] You have NOT committed any baseline trace that records a real API key, customer data, or PII. Inspect at least one baseline JSON manually before committing.
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

1. Files added (paths only).
2. Cases written (just the names — they should already know the rationale from Step 2).
3. Whether `agentprdiff check` passed against the freshly-recorded baselines.
4. Any regression `record` itself surfaced (these are real findings; flag them prominently).
5. The exact command they should run in CI: `agentprdiff check suites/<project_name>.py`.

If `record` surfaced a regression, do not assume it's a bug in the suite. Describe what you saw to the user and ask whether to (a) accept the current behavior as the baseline, (b) treat it as a real bug they want to fix, or (c) loosen the assertion.

That's the entire adoption playbook. End of file.

"""Template-driven scaffolding for new agentprdiff adoptions.

`agentprdiff scaffold <name>` stamps out the canonical layout described in
``docs/suite-layout.md`` so adopters don't have to copy/paste from AGENTS.md
by hand. The generated files contain ``TODO:`` markers that an AI coding
agent (or human) fills in for their specific production agent.

Design choices worth knowing:

* **Don't overwrite.** Every file write is gated on ``Path.exists()``. We
  never clobber a file the user already wrote. The CLI prints ``[skip]`` for
  pre-existing paths and ``[new]`` for fresh writes so the user sees what
  changed.
* **Recipe-driven `_eval_agent.py`.** The wrapper template differs by recipe
  (sync OpenAI client, async OpenAI, stubbed helper). The other files are
  recipe-agnostic.
* **Templates are inline.** Shipping a separate `templates/` directory is
  more code paths to thread through wheel/sdist packaging for marginal
  readability gain. If the templates outgrow this file, split later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
"""Allowed shape for the suite name. Matches Python identifier rules and
the slug shape used in `.agentprdiff/baselines/<suite>/`."""

VALID_RECIPES = ("sync-openai", "async-openai", "stubbed")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class ScaffoldResult:
    """Outcome of a single scaffold run, suitable for CLI rendering."""

    written: list[Path]
    skipped: list[Path]


def scaffold(name: str, *, recipe: str, root: Path) -> ScaffoldResult:
    """Lay down the canonical suite layout under ``root``.

    Raises ``ValueError`` if ``name`` or ``recipe`` is invalid. Never
    overwrites an existing file — those paths are returned in
    ``ScaffoldResult.skipped`` so the caller can report on them.
    """
    if not NAME_RE.match(name):
        raise ValueError(
            f"invalid name {name!r}: must be lowercase snake_case "
            "(letters, digits, underscores; starting with a letter)."
        )
    if recipe not in VALID_RECIPES:
        raise ValueError(
            f"unknown recipe {recipe!r}: choose one of {', '.join(VALID_RECIPES)}."
        )

    files: list[tuple[Path, str]] = [
        (root / "suites" / "__init__.py", _TPL_SUITES_INIT.format(name=name)),
        (root / "suites" / "_eval_agent.py", _eval_agent_template(recipe).format(name=name)),
        (root / "suites" / "_stubs.py", _TPL_STUBS.format(name=name)),
        (root / "suites" / f"{name}.py", _TPL_SUITE.format(name=name)),
        (root / "suites" / f"{name}_cases.md", _TPL_CASES_DOSSIER.format(name=name)),
        (root / "suites" / "README.md", _TPL_SUITES_README.format(name=name)),
        (
            root / ".github" / "workflows" / "agentprdiff.yml",
            _TPL_WORKFLOW.format(name=name),
        ),
    ]

    written: list[Path] = []
    skipped: list[Path] = []
    for path, content in files:
        if path.exists():
            skipped.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)

    return ScaffoldResult(written=written, skipped=skipped)


def _eval_agent_template(recipe: str) -> str:
    """Pick the recipe-specific `_eval_agent.py` template."""
    return {
        "sync-openai": _TPL_EVAL_SYNC,
        "async-openai": _TPL_EVAL_ASYNC,
        "stubbed": _TPL_EVAL_STUBBED,
    }[recipe]


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
# `.format(name=...)` is the only substitution. Curly braces inside templates
# that should NOT be substituted are doubled.

_TPL_SUITES_INIT = '"""agentprdiff suites for {name}."""\n'


_TPL_STUBS = '''"""Stubs for side-effecting tools used by the {name} agent.

Each stub returns deterministic, shape-compatible data so the agent's tool
dispatch is exercised without hitting external systems. Stubs should be
dumb: branch on a substring of the input to vary canned data, do not build
full simulators.
"""

from __future__ import annotations


# TODO: write one stub per side-effecting production tool. Example:
#
# def lookup_order(order_id: str) -> dict:
#     if order_id == "9999":
#         return {{"status": "shipped", "refundable": False}}
#     return {{"status": "delivered", "refundable": True}}


# Map of tool name -> stub function. Imported by _eval_agent.py and passed
# to instrument_tools(...) (or used directly if you dispatch tools yourself).
STUB_TOOL_MAP: dict[str, object] = {{
    # "lookup_order": lookup_order,
}}
'''


_TPL_EVAL_SYNC = '''"""Eval-mode wrapper for the {name} agent (sync OpenAI recipe).

Uses :func:`agentprdiff.adapters.openai.instrument_client` to capture every
model call and tool dispatch automatically. Production code is *not*
modified.

See ``docs/adapters.md`` for the full adapter reference.
"""

from __future__ import annotations

from agentprdiff import Trace
from agentprdiff.adapters.openai import instrument_client, instrument_tools

# TODO: import your production agent's entry point and client factory.
# Replace these stub names with the real ones. Example:
#
# from agent import run_agent, get_client

from suites._stubs import STUB_TOOL_MAP


def eval_agent(query: str) -> tuple[str, Trace]:
    """Eval-mode entry point. agentprdiff calls this with each case input."""
    client = get_client()  # noqa: F821 — TODO: replace with your factory
    with instrument_client(client) as trace:
        # `tools` is the dict your production loop already takes. The adapter
        # wraps each callable so its arguments + result land on the trace.
        tools = instrument_tools(STUB_TOOL_MAP, trace)
        output = run_agent(query, client=client, tools=tools)  # noqa: F821
        return output, trace
'''


_TPL_EVAL_ASYNC = '''"""Eval-mode wrapper for the {name} agent (async OpenAI recipe).

For agents built on ``AsyncOpenAI`` (or any other async OpenAI-compatible
client). ``instrument_client`` detects the async client automatically and
installs an awaitable patched ``create``; ``instrument_tools`` returns
``async def`` wrappers for tools that are themselves coroutine functions, so
``await tools[name](**args)`` works just like the underlying tool.

The ``with`` block is a regular ``with`` (not ``async with``) — the patch is
bound to the client *instance*, not the running event loop, so context
management is event-loop-agnostic.

agentprdiff's runner is sync, so the public ``eval_agent`` bridges with
``asyncio.run``. If your tests already manage their own loop, replace that
bridge — the inner async function is what matters.
"""

from __future__ import annotations

import asyncio

# TODO: import your production async agent.
# from agent import run_agent_async, get_async_client

from agentprdiff import Trace
from agentprdiff.adapters.openai import instrument_client, instrument_tools

from suites._stubs import STUB_TOOL_MAP


async def _eval_agent_async(query: str) -> tuple[str, Trace]:
    client = get_async_client()  # noqa: F821 — TODO: replace with your factory
    trace = Trace(suite_name="", case_name="", input=query)

    with instrument_client(client, trace=trace) as t:
        # `tools` is the dict your production loop already takes. Each
        # wrapper matches the underlying tool's shape — async tools come
        # back as `async def`, sync tools as plain functions.
        tools = instrument_tools(STUB_TOOL_MAP, t)
        output = await run_agent_async(query, client=client, tools=tools)  # noqa: F821
        return output, trace


def eval_agent(query: str) -> tuple[str, Trace]:
    """Sync entry point — agentprdiff calls this."""
    return asyncio.run(_eval_agent_async(query))
'''


_TPL_EVAL_STUBBED = '''"""Eval-mode wrapper for the {name} agent (stubbed-helper recipe).

Production code wraps a single LLM call in a helper (e.g. ``summarize(text)``,
``classify(query)``, ``extract_entities(doc)``). Tests substitute that helper
with a deterministic stub and exercise the surrounding orchestration —
chunking, dedup, formatting, post-processing. See ``docs/adapters.md``
"stubbed LLM-boundary pattern" for the full rationale.

This recipe does NOT test the prompt itself. It tests everything the agent
does *with* the LLM output. If you also want prompt-quality regression
coverage, pair this with a small live-API suite gated behind a flag.
"""

from __future__ import annotations

from agentprdiff import LLMCall, Trace

# TODO: import the production module that contains the LLM helper and the
# orchestration entry point.
# from agent import summarize as agent_mod
# from agent import run_agent


def _fake_helper(text: str) -> str:
    """Deterministic stand-in for the LLM helper.

    Branch on a substring of the input to vary canned outputs across cases.
    Keep it dumb.
    """
    # TODO: tailor canned outputs to the case shapes you'll write.
    if "TODO_marker" in text.lower():
        return "TODO: canned output for this case shape"
    return "TODO: default canned output"


# Replace HELPER_NAME with the actual attribute name on your production
# module (e.g. `summarize_article`, `classify_intent`).
HELPER_NAME = "TODO_helper_name"


def eval_agent(query: str) -> tuple[str, Trace]:
    trace = Trace(suite_name="", case_name="", input=query)

    original = getattr(agent_mod, HELPER_NAME)  # noqa: F821
    def wrapped(text: str) -> str:
        out = _fake_helper(text)
        # Record what the helper "did" so cost_lt_usd / latency_lt_ms graders
        # still get values. Numbers should be plausible, not real.
        trace.record_llm_call(LLMCall(
            provider="stub",
            model=f"stub-{{HELPER_NAME}}-v1",
            input_messages=[{{"role": "user", "content": text[:200]}}],
            output_text=out,
            prompt_tokens=len(text) // 4,
            completion_tokens=len(out) // 4,
            cost_usd=0.0001,
            latency_ms=120.0,
        ))
        return out
    setattr(agent_mod, HELPER_NAME, wrapped)  # noqa: F821
    try:
        output = run_agent(query)  # noqa: F821
    finally:
        setattr(agent_mod, HELPER_NAME, original)  # noqa: F821

    return output, trace
'''


_TPL_SUITE = '''"""Regression suite for {name}.

Run with::

    agentprdiff record suites/{name}.py     # save baselines after intentional changes
    agentprdiff check  suites/{name}.py     # in CI; exit 1 on regression
    agentprdiff check  suites/{name}.py --case happy_path   # iterate on one case

See AGENTS.md for the full adoption playbook.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the project root importable so the suite resolves `from agent import
# ...` regardless of how the loader was invoked. The 0.2 loader does this
# defensively too — keep the block as belt-and-suspenders.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentprdiff import case, suite  # noqa: E402
from agentprdiff.graders import (  # noqa: E402
    contains,
    cost_lt_usd,
    latency_lt_ms,
    tool_called,
)

from suites._eval_agent import eval_agent  # noqa: E402

{name}_suite = suite(
    name="{name}",
    agent=eval_agent,
    description="Regression tests for the {name} agent.",
    cases=[
        case(
            name="happy_path",
            input="TODO: a representative input for this case",
            expect=[
                # Pin a behavioral fact (substring or output shape).
                contains("TODO"),
                # Pin a tool-routing decision if the agent uses tools.
                # tool_called("TODO_tool_name"),
                # Optional semantic check. Write the rubric as a *behavior*,
                # not a specific phrase — see docs/adapters.md on the
                # fake_judge fallback before relying on this in CI.
                # from agentprdiff.graders import semantic
                # semantic("agent does the right thing for this input"),
                # Always include budget graders. Set generous initial values
                # (3x observed median) and tighten later.
                latency_lt_ms(5_000),
                cost_lt_usd(0.01),
            ],
        ),
        # TODO: add one case per row of your behavioral contract. At minimum
        # cover: each tool routing decision, the no-tool clarification path,
        # and any cost/latency-sensitive case.
    ],
)
'''


_TPL_SUITES_README = '''# Regression suites

This directory holds agentprdiff regression suites — JSON snapshots of
agent behavior that CI diffs against on every PR.

- `{name}.py` — the suite definition (cases + assertions).
- `{name}_cases.md` — case dossier (reviewer-facing prose for each case).
- `_eval_agent.py` — eval-mode wrapper around the production agent.
- `_stubs.py` — deterministic stand-ins for side-effecting tools.

## Setup (first time)

Set the API key your production agent reads. Local options:

```bash
# Option 1: .env file (make sure .env is in .gitignore!)
echo ".env" >> .gitignore
echo "OPENAI_API_KEY=sk-..." > .env       # match your agent's env var
set -a; source .env; set +a               # or use python-dotenv / direnv

# Option 2: shell export (one-off)
export OPENAI_API_KEY=sk-...
```

If your suite uses `semantic()` graders and you want a real LLM judge
locally (rather than the keyword-matching fake fallback), also set
`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`. See
[AGENTS.md → API keys](https://github.com/vnageshwaran-de/agentprdiff/blob/main/AGENTS.md#api-keys--what-to-set-where-and-how-to-ask-the-user-about-them)
for the full picture, including CI secret setup.

## Semantic Judge Keys

`semantic(...)` graders need an LLM judge to render verdicts. Without
one, agentprdiff silently falls back to `fake_judge` (keyword matching) —
the suite reports PASS even when no LLM ever ran. Make the judge mode
explicit so the absence-or-presence of LLM scoring is never ambiguous in
CI logs.

**This suite's mode:** TODO — fill in one of:

- `fake_judge` — free, keyword matching only. Acceptable when the rubric
  reduces cleanly to keywords; brittle otherwise. Set
  `AGENTGUARD_JUDGE=fake`.
- Real Anthropic judge — recommended for cost.
  `AGENTGUARD_JUDGE=anthropic` plus `ANTHROPIC_API_KEY=...`.
- Real OpenAI judge — `AGENTGUARD_JUDGE=openai` plus `OPENAI_API_KEY=...`.
- `Not applicable` — this suite has no `semantic(...)` graders. Verify with
  `grep -n "semantic(" suites/{name}.py`. State this explicitly anyway so
  the next reviewer knows the absence of judge config is deliberate.

**Reproduce CI's mode locally:** export the same env vars CI sets and run:

```bash
agentprdiff check suites/{name}.py --case <a_case_with_semantic>
```

Confirm the trace shows non-zero `cost_usd` on the judge call (real
judges) or a flat zero (fake_judge). A real-judge config that traces zero
cost means the silent fallback bit you — re-check the env vars.

## Run locally

```bash
agentprdiff check suites/{name}.py
agentprdiff check suites/{name}.py --case happy_path   # iterate on one case
agentprdiff check suites/{name}.py --list              # discover case names
```

Re-record after intentional behavior changes:

```bash
agentprdiff record suites/{name}.py
git add .agentprdiff/baselines/
```

See [AGENTS.md](https://github.com/vnageshwaran-de/agentprdiff/blob/main/AGENTS.md)
for the full adoption guide and
[suites/{name}_cases.md](./{name}_cases.md) for what each case pins.
'''


_TPL_CASES_DOSSIER = '''# `{name}` regression suite — case dossier

A reviewer-friendly explanation of every case in [`{name}.py`](./{name}.py).
This file is for humans (PR reviewers, on-call engineers) — CI does not
enforce sync between it and the suite. Update it whenever you add, remove,
or meaningfully change a case.

If a case fails in CI and you don't recognize the name, this file is the
fastest path back to *what the case is pinning and why it matters*.

## Running the suite

Run every case from the repository root:

```bash
agentprdiff check suites/{name}.py
```

If the installed wheel in your virtualenv predates the `--case`, `--list`,
and `review` commands, use a local source checkout of agentprdiff next to
this repository. Substitute `.venv-agentprdiff/` with whatever virtualenv
your project actually uses:

```bash
.venv-agentprdiff/bin/agentprdiff check suites/{name}.py
```

### List the available cases

```bash
agentprdiff check suites/{name}.py --list
# or, with a local source checkout:
PYTHONPATH=../agentprdiff/src .venv-agentprdiff/bin/python -m agentprdiff.cli \\
    check suites/{name}.py --list
```

### Run one case

Filter by substring or glob (case-insensitive). A filter that matches
nothing exits 2 — no silent zero-runs:

```bash
agentprdiff check suites/{name}.py --case happy_path
agentprdiff check suites/{name}.py --case "*refund*"
agentprdiff check suites/{name}.py --skip slow         # everything except a pattern
```

### Use `review` for verbose, exit-0 iteration

`review` runs the same comparison `check` does but renders one verbose
panel per case (input echo, every assertion's `was → now` verdict, cost /
latency / token deltas, tool-sequence diff, unified output diff) and
exits 0 even on regression — safe to wire into a watcher / `entr` / `fzf`
loop without your shell going red on every iteration:

```bash
agentprdiff review suites/{name}.py --case happy_path
```

Reach for `check` when you want the CI gate's exit semantics; reach for
`review` while you're working.

## Seeing a regression locally

These are safe local experiments to confirm the suite would catch a real
behavior drift. Revert the edit after observing the failure — none of
these changes should be committed.

1. **Change the system prompt** in your production agent module to a
   different sentence, then run:

   ```bash
   agentprdiff check suites/{name}.py --case happy_path
   ```

   Expected result: the prompt-text grader (e.g. `system_prompt_is(...)`)
   fails because the recorded LLM call no longer matches the baseline
   contract.

2. **Change a stub fixture** in `suites/_stubs.py` so it returns different
   values from the case expects, then run:

   ```bash
   agentprdiff check suites/{name}.py --case happy_path
   ```

   Expected result: the entity / value graders fail and the trace diff
   shows the output text changed.

3. **Change an exception handler** in production code (e.g. so a failure
   path returns a string where callers expect `None`), then run:

   ```bash
   agentprdiff check suites/{name}.py --case happy_path
   ```

   Expected result: the contract grader pinning the failure-mode return
   value fails, proving callers would no longer receive the fallback
   signal they depend on.

## Updating the baseline after an intentional change

When a behavior change is intentional, re-record the baseline for only
the affected case and commit the resulting JSON diff so reviewers see the
before/after:

```bash
agentprdiff record suites/{name}.py --case happy_path
git add .agentprdiff/baselines/
```

Drop `--case` to re-record every case at once. Either form is safe to
re-run — `record` overwrites baselines in place, no accumulation.

## Cases

### `happy_path`

**What it tests.** TODO: one-paragraph English description of the contract
this case pins. Write it as a sentence a non-author reviewer can read in
ten seconds and understand what's being protected.

**Input.** TODO: the exact input passed to the agent and *why this input
was chosen* (which contract row from the requirements does it exercise?).

**Assertions.**

- TODO: assertion 1 in plain English (e.g. "output contains the order number").
- TODO: assertion 2 (e.g. "the `lookup_order` tool was called exactly once").
- Budget: latency under 5s, cost under $0.01. (Tighten after a few PRs of observation.)

**Code impacted.** TODO: file paths and approximate line numbers in
production code that this case exercises. Helps reviewers locate the
affected lines without re-reading the whole agent.

- `path/to/agent.py:NN` — what's tested at this line (prompt assembly, tool routing, post-processing, etc.).

**Application impact.** TODO: what breaks for end users if this regresses.
One sentence; concrete and specific. ("Refunds silently fail" not "the
agent misbehaves.")

**How to exercise this case in isolation.**

```bash
agentprdiff check  suites/{name}.py --case happy_path     # CI-style, exit 1 on regression
agentprdiff review suites/{name}.py --case happy_path     # verbose, exit 0
agentprdiff record suites/{name}.py --case happy_path     # re-record after intentional change
```

---

<!--
Template for additional cases. Copy-paste this block, change the heading,
fill in the five sections plus the run-commands block.

### `<case_name>`

**What it tests.**

**Input.**

**Assertions.**

-

**Code impacted.**

-

**Application impact.**

**How to exercise this case in isolation.**

```bash
agentprdiff check  suites/{name}.py --case <case_name>
agentprdiff review suites/{name}.py --case <case_name>
agentprdiff record suites/{name}.py --case <case_name>
```

-->
'''


_TPL_WORKFLOW = '''name: agentprdiff
on:
  pull_request:
    paths:
      - "**/*.py"
      - "suites/**"
      - "requirements.txt"
      - ".github/workflows/agentprdiff.yml"
  workflow_dispatch: {{}}

# Default to read-only — this workflow only checks out code and uploads
# artifacts. GHAS flags workflows without an explicit permissions block.
permissions:
  contents: read

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {{ python-version: "3.11" }}
      - run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt agentprdiff
          # Semantic-judge SDKs. The agentprdiff wheel imports these lazily,
          # so installing only the one matching AGENTGUARD_JUDGE below keeps
          # the CI environment lean. If your suite has no semantic() graders
          # (see suites/README.md "Semantic Judge Keys"), drop both lines.
          # Uncomment exactly one to match the chosen judge mode:
          #   pip install anthropic     # if AGENTGUARD_JUDGE=anthropic
          #   pip install openai        # if AGENTGUARD_JUDGE=openai (already installed if your agent uses OpenAI)
          # Without the matching SDK, semantic() raises ImportError at first
          # use and the case fails — louder than the silent fake_judge trap.
      - env:
          # TODO: match the env var your production agent reads.
          # Common names: OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY,
          # or a project-specific name. Add the secret in:
          # Settings -> Secrets and variables -> Actions -> New repository secret.
          OPENAI_API_KEY: ${{{{ secrets.OPENAI_API_KEY }}}}
          # Semantic-judge mode (see AGENTS.md "Step 5b — decide and document
          # the semantic-judge mode"). Pick one and uncomment; do NOT rely on
          # the implicit selection order — it makes "which provider did I
          # get?" ambiguous in CI logs. If your suite has no semantic()
          # graders, leave all three commented and CI runs free.
          #
          #   AGENTGUARD_JUDGE: fake          # keyword matching, free
          #   AGENTGUARD_JUDGE: anthropic     # real judge, cheaper; pair with ANTHROPIC_API_KEY below
          #   AGENTGUARD_JUDGE: openai        # real judge; pair with OPENAI_API_KEY above
          #
          # Pair the chosen judge with its key. Without a key, semantic()
          # falls back to fake_judge silently and you'll see PASS without
          # the LLM judge ever running.
          ANTHROPIC_API_KEY: ${{{{ secrets.ANTHROPIC_API_KEY }}}}
        run: |
          if [ -z "${{OPENAI_API_KEY}}" ]; then
            echo "::warning::API key secret not set — skipping agentprdiff check."
            exit 0
          fi
          agentprdiff check suites/{name}.py --json-out artifacts/agentprdiff.json
      - uses: actions/upload-artifact@v4
        if: always()
        with: {{ name: agentprdiff-trace, path: artifacts/ }}
'''

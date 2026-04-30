---
id: troubleshooting
title: Troubleshooting
sidebar_position: 7
---

# Troubleshooting

The fastest way to fix it.

## "no module named agentprdiff"

```bash
$ agentprdiff check suite.py
ModuleNotFoundError: No module named 'agentprdiff'
```

You're running from a shell that doesn't see the install. Common causes:

- Wrong virtualenv. Activate the one that has `pip install agentprdiff`.
- Installed with `pipx` but the `pipx` shim isn't on `PATH`.
- Editable install in a different repo. `pip install -e .` again here.

```bash
which agentprdiff
agentprdiff --version
```

## "no such file: suite.py"

```bash
$ agentprdiff check suite.py
FileNotFoundError: no such file: /abs/path/suite.py
```

- Path is relative to the current working directory, not to
  `.agentprdiff/`. `cd` into the suite's parent or pass the absolute path.
- Glob expansion didn't run — use quotes only when you mean a glob:
  `agentprdiff check 'suites/*.py'` ← wrong.
  `agentprdiff check suites/*.py`   ← right (shell expands).

## "<file> defines no module-level Suite objects"

```python
# suite.py
def my_suite():
    return suite(name="…", agent=…, cases=[…])
```

Only **module-level** `Suite` instances are picked up. Bind the result of
`suite(...)` to a variable at the top level:

```python
# suite.py
my_suite = suite(name="…", agent=…, cases=[…])
```

## "no cases matched --case/--skip filters."

Exit code 2. The filter matched zero cases. Common causes:

- Typo in the case name (filtering is case-insensitive but exact about
  spelling).
- You qualified by suite (`billing:refund*`) but the suite name is
  different in your file.
- You added a `~negate` pattern that excluded everything.

```bash
agentprdiff check suite.py --list   # see what's actually there
```

## "Trace pydantic validation error" when loading a baseline

Someone hand-edited a baseline JSON, or a major schema change happened.

```bash
git checkout HEAD -- .agentprdiff/baselines/<suite>/<case>.json
# or
rm .agentprdiff/baselines/<suite>/<case>.json
agentprdiff record suite.py --case <case>
```

Never edit baselines by hand — they exist to be regenerated.

## "REGRESSION" but the diff looks identical

A grader's `passed` flipped even though the trace shape looks the same.
Usually one of:

- A grader was *added* to the case since the baseline. Its
  `baseline_passed` is `None`, treated as previously-passing-or-absent;
  if it now fails, that's a regression. Re-record the baseline if the
  current behavior is correct.
- The grader's `name` changed (e.g. you switched from
  `contains("refund")` to `contains("Refund", case_sensitive=True)`).
  The differ keys per-assertion regression detection by `grader_name`,
  so a renamed grader looks new. Same fix: re-record.

## Adapter says "instrument_client expected an OpenAI-style client"

```python
TypeError: instrument_client expected an OpenAI-style client with
client.chat.completions.create; got LegacyOpenAI.
```

You're on the legacy `openai` SDK (≤ 0.x) or a wrapper that hides the
`chat.completions.create` attribute. Either upgrade the SDK
(`pip install -U openai`) or switch to manual instrumentation:

```python
trace.record_llm_call(LLMCall(...))
```

## "no pricing entry for model 'foo'; cost_usd will be recorded as 0.0"

The adapter doesn't know the price of the model you used. Cost-budget
graders will trivially pass.

```python
from agentprdiff.adapters import register_prices
register_prices({"foo": (0.0009, 0.0018)})  # ($/1k input, $/1k output)
```

Or pass `prices=` to the specific `instrument_client` call.

## `semantic()` quietly returns PASS even though the model is wrong

You probably hit the silent `fake_judge` fallback. Check the banner at
the top of the run output:

```
semantic judge: fake_judge (no AGENTGUARD_JUDGE, no OPENAI_API_KEY/ANTHROPIC_API_KEY — silent fallback)
```

`fake_judge` matches keywords ≥ 4 chars. To get a real judge:

```bash
export OPENAI_API_KEY=sk-...
# or
export ANTHROPIC_API_KEY=sk-ant-...
# or pin explicitly
export AGENTGUARD_JUDGE=anthropic
```

## CI passes locally but fails in CI

Almost always one of:

- **Different `AGENTGUARD_JUDGE`.** Local has `OPENAI_API_KEY` set, CI
  uses `fake_judge`. Set both explicitly:
  `AGENTGUARD_JUDGE=fake` (or `=anthropic`/`=openai` with the matching
  secret) on both sides so the judge is identical.
- **Different `pricing` table.** Local has `register_prices(...)` in a
  helper that CI doesn't import. Move the call into the suite file.
- **Different agent SDK version.** Pin in `requirements.txt` or
  `constraints.txt`.
- **Different stub data.** Stubs that read from a JSON file outside the
  repo don't ship to CI. Inline them.

## "the agent does the right thing but a `tool_called` grader fails"

Your agent called the tool, but the trace doesn't have the call recorded.
Two usual causes:

- The agent *does* the call internally but doesn't return a `Trace`. The
  runner can only see what's in the returned trace. Wire up
  `trace.record_tool_call(ToolCall(...))` (or use an SDK adapter).
- You're using `instrument_tools` but dispatching the original `TOOL_MAP`
  instead of the wrapped dict. Make sure your tool-loop uses
  `tools[name](**args)`, not `TOOL_MAP[name](**args)`.

## Asyncio: "RuntimeError: This event loop is already running"

You called `asyncio.run` from inside an already-running event loop
(common in Jupyter or some test runners). For agentprdiff:

```python
def my_agent(query):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(my_agent_async(query))
    # We're inside a loop — use loop.run_until_complete or restructure.
    return loop.run_until_complete(my_agent_async(query))
```

Long-term, push the asyncio bridge into your eval wrapper, not into
production code.

## "test_runner.py::test_record_overwrites_baselines fails"

You're hacking on agentprdiff itself and a baseline-test broke. The most
common cause: a stray `.agentprdiff/` directory left over from a previous
run. The tests use `tmp_path` fixtures, but if your `cwd` has a top-level
`.agentprdiff/` it can leak in. `rm -rf .agentprdiff/` and try again.

## Where to file a bug

[github.com/vnageshwaran-de/agentprdiff/issues](https://github.com/vnageshwaran-de/agentprdiff/issues)

Include:

- Output of `agentprdiff --version`.
- Minimal reproducer (suite + agent stub).
- Full terminal output and the relevant baseline JSON if applicable.

---
id: contributing
title: Contributing
sidebar_position: 9
---

# Contributing

Thanks for your interest. `agentprdiff` is a small, opinionated project;
PRs that fit the scope below are merged quickly.

## Scope

In scope:

- New deterministic graders (keep them dependency-free).
- New semantic-grader backends (pluggable `Judge` callables).
- SDK-specific instrumentation helpers under `agentprdiff/adapters/`.
- CI reporters (JUnit XML, GitHub annotations, etc.).
- Bug fixes, test coverage, docs.

Out of scope for the 0.x line:

- A hosted service / SaaS.
- A new agent framework — `agentprdiff` deliberately does not care how
  your agent is built.
- Non-trace-based evaluation (pairwise preference, ELO). Different tool.

## Development setup

```bash
git clone https://github.com/vnageshwaran-de/agentprdiff
cd agentprdiff
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

The `dev` extra brings in `pytest`, `pytest-cov`, `ruff`, and `mypy`.

## Running tests

```bash
pytest -q                            # unit tests
ruff check src tests                 # lint
mypy src                             # types
```

The bundled quickstart is also a CI smoke test:

```bash
cd examples/quickstart
agentprdiff init
agentprdiff record suite.py
agentprdiff check  suite.py          # exit 0
```

## Project layout

```
src/agentprdiff/
├── __init__.py        # public re-exports
├── core.py            # Suite, Case, Trace, …
├── runner.py          # Runner, RunReport
├── differ.py          # TraceDelta
├── store.py           # BaselineStore
├── loader.py          # load_suites
├── filtering.py       # --case / --skip parser
├── reporters.py       # Terminal / JSON / Review
├── scaffold.py        # `agentprdiff scaffold` templates
├── cli.py             # Click app
├── graders/
│   ├── deterministic.py
│   └── semantic.py
└── adapters/
    ├── pricing.py
    ├── openai.py
    └── anthropic.py
tests/                 # pytest, mirrors the package layout
examples/              # quickstart + regression-tour demos
```

`tests/` mirrors the package — `tests/test_runner.py` exercises
`runner.py`, `tests/test_adapter_openai_async.py` covers the
`AsyncOpenAI` path, and so on.

## Adding a new deterministic grader

1. Add the grader function to `src/agentprdiff/graders/deterministic.py`
   following the convention of existing graders:
   - Take config args at the outer level.
   - Return a closure `(trace) -> GradeResult`.
   - Set `grader_name` and `reason` such that they're useful in CI logs.
   - No new dependencies.
2. Re-export it from `src/agentprdiff/graders/__init__.py`.
3. Add it to the README's "batteries-included graders" list.
4. Add at least one passing-test and one failing-test case in
   `tests/test_graders_deterministic.py`.

```python
# src/agentprdiff/graders/deterministic.py
def starts_with(prefix: str) -> Grader:
    """Pass iff the agent's final output starts with `prefix`."""
    def _grader(trace: Trace) -> GradeResult:
        haystack = _output_str(trace)
        passed = haystack.startswith(prefix)
        return GradeResult(
            passed=passed,
            grader_name=f"starts_with({prefix!r})",
            reason=(
                f"output starts with {prefix!r}"
                if passed
                else f"output starts with {haystack[: len(prefix)]!r}"
            ),
        )
    return _grader
```

## Adding a new semantic-grader judge

1. Add the judge to `src/agentprdiff/graders/semantic.py`.
2. Lazy-import any SDK so the base wheel doesn't pull it in.
3. Update `_default_judge` if it should be a fallback option.
4. Add `describe_default_judge` coverage so the banner stays accurate.
5. Add tests in `tests/test_graders_semantic.py` using a fake transport.

## Adding a new SDK adapter

1. New file under `src/agentprdiff/adapters/`.
2. Pattern: `@contextmanager def instrument_client(client, *, trace=None,
   prices=None, provider=None)`.
3. Patch the bound method on the client *instance*, not module state.
4. Restore on `__exit__` even if the agent raised.
5. Mirror the OpenAI adapter's `_make_*` helper split so sync + async
   share record-building logic.
6. Add an `instrument_tools` re-export (the data model is SDK-agnostic;
   the existing helpers are reusable).
7. Tests under `tests/test_adapter_<provider>.py`. Use a fake response
   object — don't depend on the real SDK at test time.

## Adding a new reporter

Reporters take a `RunReport` and render it. Add to
`src/agentprdiff/reporters.py` if it's general; ship it under
`src/agentprdiff/contrib/` otherwise.

If the reporter wants a CLI flag (like `--junit-out`), add the option in
`src/agentprdiff/cli.py` and route through the existing pattern (see
`--json-out`).

## PR checklist

- Tests pass locally (`pytest`, `ruff check`, `mypy`).
- Public API changes are reflected in
  `src/agentprdiff/__init__.py` and the README.
- User-facing changes are noted in `CHANGELOG.md` under the next version.
- New graders include at least one passing and one failing test case.
- New CLI flags include `--help` text that survives a non-Click reading.

## Code style

- Black-compatible formatting; `ruff` is the linter.
- Type hints on all public APIs (`mypy --strict` is *not* enforced; we
  use `strict = false` because pydantic + Click).
- Prefer small, pure callables over classes.
- Keep imports lazy at the module boundary for optional dependencies.
- Docstrings on every public symbol; one-line summary plus a usage
  example when nontrivial.

## Releasing (maintainers)

```bash
# bump version in pyproject.toml + src/agentprdiff/__init__.py
# add CHANGELOG entry
git tag v0.x.y && git push --tags
# GitHub Action publishes to PyPI on tag push
```

## Code of conduct

Be kind. Disagreement is welcome; rudeness is not. PR feedback is about
the code, not the person.

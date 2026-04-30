---
id: installation
title: Installation
sidebar_position: 2
---

# Installation

`agentprdiff` is published on PyPI and installs cleanly on any Python
3.10+ environment with no compiled dependencies.

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10, 3.11, or 3.12 | Tested on each in CI. |
| `pip` | ≥ 23.0 | For modern resolver behavior. |
| Git | any | Baselines live in your repo, so you'll commit them. |
| `OpenAI` SDK | optional, ≥ 1.0 | Only if using the OpenAI adapter or `openai_judge`. |
| `Anthropic` SDK | optional, ≥ 0.30 | Only if using the Anthropic adapter or `anthropic_judge`. |

The package itself depends on `click`, `rich`, `pydantic` (v2), and
`pyyaml` — all pure-Python.

## Install from PyPI

```bash
pip install agentprdiff
```

Verify:

```bash
agentprdiff --version
# agentprdiff, version 0.2.3
```

### Optional extras

```bash
# OpenAI / OpenAI-compatible providers (Groq, Gemini, OpenRouter, Ollama, ...)
pip install "agentprdiff[openai]"

# Anthropic Messages API
pip install "agentprdiff[anthropic]"

# Both (for a polyglot agent or to use both judge backends)
pip install "agentprdiff[openai,anthropic]"
```

The base wheel imports `openai` / `anthropic` lazily, so you only pay the
import cost when you actually call an SDK adapter or judge.

## Install from source (contributors)

```bash
git clone https://github.com/vnageshwaran-de/agentprdiff
cd agentprdiff
pip install -e ".[dev]"
```

The `dev` extra brings in `pytest`, `pytest-cov`, `ruff`, and `mypy`.

## Run the bundled smoke test

```bash
cd examples/quickstart
agentprdiff init
agentprdiff record suite.py
agentprdiff check  suite.py
```

If the last command exits `0`, your install is healthy. The example agent
is fully self-contained — no API keys required.

## Environment variables

`agentprdiff` itself reads only one env var. Your *agent* and the
*semantic judge* read a few others.

| Variable | Read by | Purpose |
|---|---|---|
| `AGENTGUARD_JUDGE` | `agentprdiff` (semantic grader) | `fake`, `openai`, or `anthropic`. Forces the default judge backend, ignoring autodetection. |
| `OPENAI_API_KEY` | `openai_judge` (when selected); your agent | Real-LLM judge calls and any agent that uses the OpenAI SDK. |
| `ANTHROPIC_API_KEY` | `anthropic_judge` (when selected); your agent | Real-LLM judge calls and any agent that uses the Anthropic SDK. |
| Whatever your agent reads | your agent | `agentprdiff` does not touch your agent's keys; it just invokes the callable. |

### Default judge selection rules

When `semantic(...)` runs without an explicit `judge=` argument, the backend
is chosen in this order:

1. `AGENTGUARD_JUDGE=fake` → `fake_judge`
2. `AGENTGUARD_JUDGE=openai` *or* `OPENAI_API_KEY` set → `openai_judge()`
3. `AGENTGUARD_JUDGE=anthropic` *or* `ANTHROPIC_API_KEY` set → `anthropic_judge()`
4. Otherwise → `fake_judge` (deterministic, free, used in CI without keys)

Run `agentprdiff check` with at least one `semantic()` grader to see a
banner that prints which judge was actually selected — useful for
catching the silent fake-judge fallback.

## Uninstall

```bash
pip uninstall agentprdiff
```

Your `.agentprdiff/` directory and any baselines committed to git remain
untouched.

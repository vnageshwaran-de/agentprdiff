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

## Install Python 3.10+ first (if you don't have it)

Check what you've got:

```bash
python --version
python3 --version
python3.12 --version    # works only if 3.12 specifically is installed
```

If none of those print **3.10 or higher**, install one of the supported
versions below.

### macOS

**Homebrew (recommended):**

```bash
# Install Homebrew if you don't have it (one-time, ~3 min)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python 3.12
brew install python@3.12

# Verify
python3.12 --version
# Python 3.12.x
```

After install, the binary is at `/opt/homebrew/bin/python3.12` (Apple
Silicon) or `/usr/local/bin/python3.12` (Intel). Both are normally on
`$PATH` already; if not, add to your `~/.zshrc`:

```bash
echo 'export PATH="/opt/homebrew/bin:$PATH"' >> ~/.zshrc   # Apple Silicon
echo 'export PATH="/usr/local/bin:$PATH"' >> ~/.zshrc      # Intel
source ~/.zshrc
```

**python.org installer (alternative):**

If you'd rather not use Homebrew, download the Mac installer from
[python.org/downloads/macos](https://www.python.org/downloads/macos/),
double-click the `.pkg`, run through the prompts. Adds `python3.12` to
`/Library/Frameworks/Python.framework/Versions/3.12/bin/` — same `-m pip`
pattern as Homebrew.

### Windows

**python.org installer (recommended):**

1. Download from [python.org/downloads/windows](https://www.python.org/downloads/windows/)
   — pick "Windows installer (64-bit)" for Python 3.12.
2. Run the `.exe`. **Important:** check **"Add python.exe to PATH"** at
   the bottom of the first installer screen. Without that box ticked,
   `python` won't be findable from PowerShell or Command Prompt.
3. Click "Install Now."
4. Verify in a fresh terminal:

   ```powershell
   python --version
   # Python 3.12.x
   python -m pip --version
   ```

**winget (alternative, scriptable):**

```powershell
winget install --id Python.Python.3.12 --source winget
```

**Microsoft Store (alternative, sandboxed):**

Search "Python 3.12" in the Microsoft Store and click Install. Note: the
Store version installs into a sandboxed location and *can't* write
outside the user profile, which sometimes confuses tools that expect
filesystem access. The python.org installer is more flexible.

### Linux (briefly)

**Ubuntu / Debian:**

```bash
sudo apt update
sudo apt install software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt install python3.12 python3.12-venv python3.12-pip
python3.12 --version
```

**Fedora / RHEL:**

```bash
sudo dnf install python3.12 python3.12-pip
python3.12 --version
```

**Cross-platform: pyenv** (manages multiple Python versions cleanly,
works on macOS and Linux):

```bash
curl https://pyenv.run | bash
# Add the suggested lines to ~/.zshrc or ~/.bashrc, then:
pyenv install 3.12
pyenv global 3.12
python --version
# Python 3.12.x
```

Once Python 3.10+ is installed, continue to the install step below.

## Install from PyPI

```bash
pip install agentprdiff
```

Verify:

```bash
agentprdiff --version
# agentprdiff, version 0.2.4
```

!!! tip "Multiple Python versions on macOS / Linux?"

    If `pip install agentprdiff` reports `Could not find a version that
    satisfies the requirement` or `No matching distribution found` even
    after installing Python 3.10+, your shell's `pip` is likely still
    wired to an older Python. Use the `-m pip` form to invoke pip
    *through* a specific Python binary:

    ```bash
    python3.12 -m pip install --upgrade pip
    python3.12 -m pip install agentprdiff
    python3.12 -c "import agentprdiff; print(agentprdiff.__version__)"
    ```

    Substitute whichever Python ≥ 3.10 you have installed (e.g.
    `python3.11`, `python3.13`). The `-m pip` form sidesteps `$PATH`
    confusion when multiple Pythons coexist — common on macOS where
    Homebrew installs `python3.12` alongside the system's `python3.9`.

    For a permanent fix, create a virtualenv and install into it once:

    ```bash
    python3.12 -m venv ~/.venvs/agentprdiff
    source ~/.venvs/agentprdiff/bin/activate
    pip install agentprdiff
    agentprdiff --version    # works without typing python3.12 every time
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

---
id: cli
title: CLI Reference
sidebar_position: 2
---

# CLI Reference

`agentprdiff` is a Click app with six subcommands. Run
`agentprdiff --help` or `agentprdiff <cmd> --help` for the canonical
text.

## Top-level options

```
agentprdiff [--root PATH] [--version] <command> [args]
```

| Option | Default | Description |
|---|---|---|
| `--root` | `.agentprdiff` | Directory where baselines and runs live. |
| `--version` | — | Print the installed version (`agentprdiff, version 0.2.3`) and exit. |
| `--help` | — | Show help and exit. |

## `agentprdiff init`

Create the `.agentprdiff/` directory and a starter `.gitignore`.

```bash
agentprdiff init
# initialized .agentprdiff/
#   baselines: .agentprdiff/baselines/   (commit this)
#   runs:      .agentprdiff/runs/        (gitignored)
```

Idempotent. Running it twice does nothing the second time.

## `agentprdiff record SUITE_FILE`

Run every suite in `SUITE_FILE` and save each trace as the canonical
baseline.

| Option | Description |
|---|---|
| `--json-out PATH` | Also write a JSON report to `PATH`. Overwrites every run. |
| `--case PATTERN` | Only record cases matching `PATTERN`. Repeatable; comma-split. |
| `--skip PATTERN` | Skip cases matching `PATTERN`. Same syntax. |
| `--list` | Print suite/case names and exit without running. |

```bash
agentprdiff record suites/billing.py
agentprdiff record suites/*.py --json-out artifacts/agentprdiff.json
agentprdiff record suites/billing.py --case refund_happy_path
```

**Exit codes.** `0` on success. `1` when any case raised an exception
(grader failures alone don't fail `record`). `2` when `--case` /
`--skip` matched zero cases.

**Side effects.** Overwrites `.agentprdiff/baselines/<suite>/<case>.json`
in place. Re-running `record` with the same suite shows up as a regular
git diff in the next PR.

## `agentprdiff check SUITE_FILE`

Run every suite and diff against saved baselines. The CI command.

| Option | Description |
|---|---|
| `--json-out PATH` | Write a JSON report to `PATH`. Overwrites every run. |
| `--case PATTERN` | Only check matching cases. |
| `--skip PATTERN` | Skip matching cases. |
| `--list` | Print case names and exit. |
| `--fail-on/--no-fail-on` | When `--no-fail-on`, regressions are reported but the exit code stays 0. Default `--fail-on`. |

```bash
agentprdiff check suites/*.py
agentprdiff check suites/billing.py --case "*refund*" --json-out art/check.json
```

**Exit codes.** `0` on no regression. `1` on any regression (with
`--fail-on`). `2` on filter-matched-zero-cases.

**Side effects.** Writes `runs/<timestamp>/<suite>/<case>.json` per case
(gitignored). Each `check` invocation creates a fresh timestamped
directory; `rm -rf .agentprdiff/runs/` is safe any time.

## `agentprdiff review SUITE_FILE`

Verbose per-case panels. **Always exits 0.** The local-iteration
counterpart to `check` — `pytest -k` for agents.

| Option | Description |
|---|---|
| `--case PATTERN` | Only render matching cases. |
| `--skip PATTERN` | Skip matching cases. |
| `--list` | Print case names and exit. |

```bash
agentprdiff review suites/billing.py --case refund_happy_path
```

`review` runs the same comparison as `check` (and writes to the same
`runs/` dir) but renders one verbose panel per case — input echo, every
assertion's *was → now* verdict, cost / latency / token deltas,
tool-sequence diff, unified output diff. Always exits `0` so it slots
into watcher loops without going red between every keystroke.

```bash
ls suites/*.py agent.py | entr -c agentprdiff review suites/billing.py --case refund
```

## `agentprdiff diff SUITE_NAME CASE_NAME`

Print the saved baseline trace for a single case as pretty JSON.

```bash
agentprdiff diff billing refund_happy_path | jq '.tool_calls'
```

**Exit codes.** `0` on success. `2` when no baseline exists.

## `agentprdiff scaffold NAME`

Stamp out the canonical suite layout for a new adoption.

| Option | Default | Description |
|---|---|---|
| `--recipe` | `sync-openai` | One of `sync-openai`, `async-openai`, `stubbed`. |
| `--dir PATH` | `.` | Project root to scaffold into. |

```bash
agentprdiff scaffold ai_content_summary --recipe sync-openai
```

Writes:

```
suites/__init__.py
suites/_eval_agent.py        # recipe-specific
suites/_stubs.py
suites/<NAME>.py
suites/<NAME>_cases.md       # reviewer-facing dossier
suites/README.md
.github/workflows/agentprdiff.yml
```

**Never overwrites.** Existing files are reported as `[skip]`. Files
that get written are reported as `[new]`. Exit code `2` on bad name or
unknown recipe.

### Recipe selection

| Recipe | When to use |
|---|---|
| `sync-openai` | Agent uses `OpenAI()` (or any OpenAI-compatible client) synchronously. |
| `async-openai` | Agent uses `AsyncOpenAI` and you want the asyncio.run bridge. |
| `stubbed` | Agent's LLM call lives behind a single helper (e.g. `summarize(text)`) — substitute the helper rather than instrument the SDK. |

## Filter syntax

`--case` and `--skip` share the same parser.

| Syntax | Meaning |
|---|---|
| `refund_happy_path` | Case-insensitive substring. Matches `refund_happy_path`, `Refund_happy_path`, etc. |
| `*refund*` | Glob (`fnmatch`). Case-insensitive. |
| `refund?` | Glob with single-char wildcard. |
| `~slow` | Negate — same as `--skip slow`. |
| `!slow` | Same as `~slow` (alternate syntax). |
| `billing:refund*` | Qualify by suite name. |
| `--case a,b` | Comma-split — equivalent to `--case a --case b`. |
| `--case a --case b` | Repeated flag. |

Negative patterns inside `--case` are merged with `--skip` and treated as
unconditional drops. A case is **kept** iff:

1. It matches at least one positive pattern (or no positive patterns
   were given).
2. It does not match any negative or `--skip` pattern.

A filter that matches zero cases exits `2` with a hint:

```
error: no cases matched --case/--skip filters.
available cases:
  customer_support/refund_happy_path
  customer_support/non_refundable_order
  ...
(tip: run with --list to see suite/case names; patterns are case-insensitive substrings or globs.)
```

## Examples cookbook

```bash
# Discover what's in a suite
agentprdiff check suites/billing.py --list

# Run one case
agentprdiff check suites/billing.py --case refund_happy_path

# Run everything except slow cases
agentprdiff check suites/billing.py --skip slow

# Re-record one case after a deliberate behavior change
agentprdiff record suites/billing.py --case refund_happy_path
git add .agentprdiff/baselines/billing/refund_happy_path.json

# Quick local iteration without breaking your shell prompt
agentprdiff review suites/billing.py --case refund_happy_path

# Run all suites, write a JSON artifact for CI archiving
agentprdiff check suites/*.py --json-out artifacts/agentprdiff.json

# Inspect a saved baseline
agentprdiff diff billing refund_happy_path | jq '.llm_calls[].cost_usd'

# Bootstrap a new adoption
agentprdiff scaffold billing --recipe sync-openai
```

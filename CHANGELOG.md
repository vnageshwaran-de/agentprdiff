# Changelog

All notable changes to `agentprdiff` are documented in this file. Originally
prototyped under the name `tracediff`; renamed before first public release.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **SDK adapters** for the two dominant agent toolchains, eliminating the need
  for manual `Trace` instrumentation:
  - `agentprdiff.adapters.openai.instrument_client` — context manager that
    monkey-patches `client.chat.completions.create` for the duration of one
    agent call. Records each invocation as an `LLMCall` (provider, model,
    input messages, output text, tool calls, tokens, cost, latency) and
    restores the original on exit. Works with **OpenAI, Groq, Gemini's
    OpenAI-compatible endpoint, OpenRouter, Ollama, vLLM, Together,
    Fireworks, DeepInfra**, and any other SDK that follows the OpenAI client
    shape.
  - `agentprdiff.adapters.anthropic.instrument_client` — equivalent for the
    Anthropic Messages API (`client.messages.create`). Handles the
    content-block response shape (text + `tool_use` blocks) and the
    Messages-API token field names.
  - `instrument_tools(tool_map, trace)` — wraps a dict of callables so each
    invocation records a `ToolCall` with name, arguments, result, latency,
    and any raised exception. Shared between both adapters.
  - `agentprdiff.adapters.pricing` — curated model→price table for cost
    estimation, with `register_prices()` and per-call `prices=` overrides.
    Unknown models record `cost_usd=0.0` and emit a single `RuntimeWarning`
    per process so missing pricing is loud rather than silent.
- Documentation: `docs/adapters.md` (full reference) and
  `docs/adapters-vercel.md` (manual integration recipe for the Vercel AI
  SDK, which is JS-only and lives in a future companion package).

### Notes

- The base `pip install agentprdiff` does **not** require the `openai` or
  `anthropic` packages. The adapters operate on a client object's shape,
  not on imported SDK modules — so installing only the SDKs you actually
  use keeps the dependency footprint small. Optional extras are still
  declared (`agentprdiff[openai]`, `agentprdiff[anthropic]`) for adopters
  who prefer to pin the SDK version alongside agentprdiff itself.

## [0.1.0] — 2026-04-22

Initial public release.

### Added

- Core `Suite` / `Case` / `Trace` model for defining agent regression tests.
- Deterministic graders: `contains`, `contains_any`, `regex_match`, `tool_called`,
  `tool_sequence`, `output_length_lt`, `latency_lt_ms`, `cost_lt_usd`,
  `no_tool_called`.
- Semantic grader (`semantic`) with a pluggable `judge` callable and built-in
  fake judge for CI environments without API keys.
- Baseline store (JSON files under `.agentprdiff/baselines/`) designed to be
  committed to version control.
- Trace diff engine producing a structured `TraceDelta` (assertion pass/fail
  changes, cost delta, latency delta, tool-call sequence changes, output
  change).
- CLI: `agentprdiff init`, `agentprdiff record`, `agentprdiff check`, `agentprdiff diff`.
- Rich-formatted terminal reporter and machine-readable JSON reporter for CI.
- Quickstart example with a mock agent that runs without any API keys.
- Pytest test suite covering graders, runner, differ, store, and CLI smoke.
- GitHub Actions CI workflow.

### Known limitations

- Only a manual instrumentation API for provider SDKs is shipped in 0.1.0.
  Drop-in wrappers for OpenAI / Anthropic / Vercel AI SDK are planned for 0.2.
- The semantic grader's built-in judge supports OpenAI and Anthropic via user-
  supplied API keys; hosted judge endpoints are not yet offered.

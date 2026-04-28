"""Tests for ``agentprdiff review`` and the underlying ``ReviewReporter``.

The reporter is rendered through a Rich Console redirected to an
``io.StringIO``; we don't assert exact glyph positioning (that would tie
the tests to Rich internals), but we do assert that the expected
human-readable signals appear in the output: status word, grader names
and reasons, baseline-vs-current markers, metric deltas, tool-sequence
deltas, and the unified output diff.
"""

from __future__ import annotations

import io
from pathlib import Path

from click.testing import CliRunner
from rich.console import Console

from agentprdiff import LLMCall, Trace, case, suite
from agentprdiff.cli import main as cli_main
from agentprdiff.graders import contains, semantic, tool_called
from agentprdiff.reporters import ReviewReporter, TerminalReporter
from agentprdiff.runner import Runner
from agentprdiff.store import BaselineStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _captured_console() -> tuple[Console, io.StringIO]:
    """Return a Rich Console wired to a StringIO for assertions.

    ``force_terminal=False`` keeps colour codes out so we can match plain
    substrings; ``width=200`` stops Rich from line-wrapping our markers
    away from the keywords they belong with.
    """
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=200, record=False)
    return console, buf


def _agent_factory(output: str, *, tools: list[str] | None = None, latency_ms: float = 5.0):
    """Build a deterministic agent that returns a fixed output and tool list."""
    tools = tools or []

    def _agent(query: str) -> tuple[str, Trace]:
        t = Trace(suite_name="", case_name="", input=query)
        t.record_llm_call(
            LLMCall(provider="m", model="m", cost_usd=0.001, latency_ms=latency_ms)
        )
        for name in tools:
            from agentprdiff import ToolCall

            t.record_tool_call(ToolCall(name=name))
        return output, t

    return _agent


# ---------------------------------------------------------------------------
# ReviewReporter unit tests
# ---------------------------------------------------------------------------


def test_review_reporter_pass_case_with_baseline(tmp_path):
    """A clean PASS case shows status PASS, all checkmarks, no diff panel."""
    store = BaselineStore(root=tmp_path / ".agentprdiff")
    runner = Runner(store)
    s = suite(
        name="toy",
        agent=_agent_factory("refund processed", tools=["lookup_order"]),
        cases=[
            case(
                name="refund",
                input="please refund",
                expect=[contains("refund"), tool_called("lookup_order")],
            )
        ],
    )
    runner.record(s)
    chk = runner.check(s)

    console, buf = _captured_console()
    ReviewReporter(console=console).render(chk)
    out = buf.getvalue()

    assert "agentprdiff review" in out
    assert "case: refund" in out
    assert "PASS" in out
    assert "REGRESSION" not in out
    assert "baseline: present" in out
    assert "contains('refund')" in out
    assert "(unchanged)" in out  # tools and output both unchanged
    assert "no change" in out  # cost/latency deltas


def test_review_reporter_regression_shows_diff_and_marker_change(tmp_path):
    """When the agent regresses, the assertion table shows ✓→✗ and the
    output diff appears in the output."""
    store = BaselineStore(root=tmp_path / ".agentprdiff")
    runner = Runner(store)

    good = suite(
        name="toy",
        agent=_agent_factory("refund processed", tools=["lookup_order"]),
        cases=[
            case(
                name="refund",
                input="please refund",
                expect=[contains("refund"), tool_called("lookup_order")],
            )
        ],
    )
    runner.record(good)

    bad = suite(
        name="toy",
        agent=_agent_factory("nope", tools=[]),  # different output, no tool
        cases=good.cases,
    )
    chk = runner.check(bad)

    console, buf = _captured_console()
    ReviewReporter(console=console).render(chk)
    out = buf.getvalue()

    assert "REGRESSION" in out
    assert "regressed" in out  # footer summary
    # Both assertions flipped: the assertion table renders both old and new marks.
    assert "✓" in out and "✗" in out
    # Tool sequence change is announced.
    assert "baseline:" in out
    assert "current:" in out
    # Unified output diff appears (file labels come from difflib).
    assert "--- baseline" in out
    assert "+++ current" in out


def test_review_reporter_no_baseline_yet(tmp_path):
    """First-run-no-baseline: assertions render, but no metric deltas
    or output diff section, since there's nothing to compare against."""
    store = BaselineStore(root=tmp_path / ".agentprdiff")
    runner = Runner(store)
    s = suite(
        name="toy",
        agent=_agent_factory("refund processed"),
        cases=[case(name="refund", input="please refund", expect=[contains("refund")])],
    )
    chk = runner.check(s)  # no record() first

    console, buf = _captured_console()
    ReviewReporter(console=console).render(chk)
    out = buf.getvalue()

    assert "baseline: not yet recorded" in out
    assert "contains('refund')" in out
    # The "metrics" section should not render — there are no deltas.
    assert "metrics:" not in out
    # The raw output should appear in place of a diff.
    assert "refund processed" in out


def test_review_reporter_agent_error(tmp_path):
    """Agent exceptions are surfaced as their own block."""
    store = BaselineStore(root=tmp_path / ".agentprdiff")
    runner = Runner(store)

    def raising_agent(query):
        raise ValueError("kaboom")

    s = suite(
        name="toy",
        agent=raising_agent,
        cases=[case(name="boom", input="x", expect=[])],
    )
    chk = runner.check(s)

    console, buf = _captured_console()
    ReviewReporter(console=console).render(chk)
    out = buf.getvalue()

    assert "error:" in out
    assert "ValueError" in out
    assert "kaboom" in out
    # No assertions defined → that branch should render its placeholder.
    assert "(no assertions defined)" in out


# ---------------------------------------------------------------------------
# Semantic-judge banner — visible in both reporters when (and only when)
# the suite uses semantic() graders.
# ---------------------------------------------------------------------------


def _stub_judge_pass(rubric: str, trace):
    """Always-pass judge so semantic() runs without an LLM SDK on the path."""
    return True, "stub PASS"


def _suite_with_semantic():
    return suite(
        name="toy",
        agent=_agent_factory("refund processed"),
        cases=[
            case(
                name="refund",
                input="please refund",
                expect=[
                    contains("refund"),
                    semantic("agent confirmed refund", judge=_stub_judge_pass),
                ],
            )
        ],
    )


def _suite_without_semantic():
    return suite(
        name="toy",
        agent=_agent_factory("refund processed"),
        cases=[case(name="refund", input="please refund", expect=[contains("refund")])],
    )


def test_terminal_reporter_prints_judge_banner_when_semantic_used(tmp_path, monkeypatch):
    """``check`` output names the judge mode so silent fake_judge can't hide."""
    monkeypatch.delenv("AGENTGUARD_JUDGE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    store = BaselineStore(root=tmp_path / ".agentprdiff")
    runner = Runner(store)
    s = _suite_with_semantic()
    chk = runner.check(s)

    console, buf = _captured_console()
    TerminalReporter(console=console).render(chk)
    out = buf.getvalue()

    assert "semantic judge:" in out
    # Default with no env vars — silent fallback warning is what we want loud.
    assert "fake_judge" in out
    assert "silent fallback" in out


def test_review_reporter_prints_judge_banner_when_semantic_used(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_JUDGE", "fake")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    store = BaselineStore(root=tmp_path / ".agentprdiff")
    runner = Runner(store)
    s = _suite_with_semantic()
    chk = runner.check(s)

    console, buf = _captured_console()
    ReviewReporter(console=console).render(chk)
    out = buf.getvalue()

    assert "semantic judge:" in out
    assert "AGENTGUARD_JUDGE=fake" in out


def test_no_judge_banner_when_suite_has_no_semantic(tmp_path):
    """Suites without semantic() should not be polluted with a banner."""
    store = BaselineStore(root=tmp_path / ".agentprdiff")
    runner = Runner(store)
    s = _suite_without_semantic()
    chk = runner.check(s)

    console, buf = _captured_console()
    TerminalReporter(console=console).render(chk)
    out = buf.getvalue()
    assert "semantic judge:" not in out

    console2, buf2 = _captured_console()
    ReviewReporter(console=console2).render(chk)
    assert "semantic judge:" not in buf2.getvalue()


def test_terminal_reporter_judge_banner_reflects_explicit_anthropic(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_JUDGE", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    store = BaselineStore(root=tmp_path / ".agentprdiff")
    runner = Runner(store)
    chk = runner.check(_suite_with_semantic())

    console, buf = _captured_console()
    TerminalReporter(console=console).render(chk)
    out = buf.getvalue()

    assert "semantic judge:" in out
    assert "anthropic" in out
    assert "fake_judge" not in out


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


_QUICKSTART_SUITE = '''
"""Tiny suite file used by the review CLI test."""
from agentprdiff import LLMCall, ToolCall, Trace, case, suite
from agentprdiff.graders import contains, tool_called


def _agent(query: str):
    t = Trace(suite_name="", case_name="", input=query)
    t.record_llm_call(LLMCall(provider="m", model="m", cost_usd=0.001, latency_ms=2))
    if "refund" in query:
        t.record_tool_call(ToolCall(name="lookup_order"))
        return "refund processed", t
    return "please share your order number", t


toy = suite(
    name="toy",
    agent=_agent,
    cases=[
        case(name="refund", input="please refund", expect=[contains("refund"), tool_called("lookup_order")]),
        case(name="hello",  input="hello",          expect=[contains("order")]),
    ],
)
'''


def _write_suite_file(tmp_path: Path) -> Path:
    p = tmp_path / "suite.py"
    p.write_text(_QUICKSTART_SUITE)
    return p


def test_cli_review_exit_code_and_filtering(tmp_path, monkeypatch):
    """`agentprdiff review` honours --case and exits 0 even on regression.

    We invoke the CLI inside an isolated cwd so the default
    ``.agentprdiff/`` root lands in tmp_path. This is the same pattern
    quickstart users hit on day one.
    """
    monkeypatch.chdir(tmp_path)
    suite_path = _write_suite_file(tmp_path)
    runner = CliRunner()

    # First, record baselines so we have something to diff against.
    rec = runner.invoke(cli_main, ["record", str(suite_path)])
    assert rec.exit_code == 0, rec.output

    # Now narrow to one case via --case (substring match) and review.
    rev = runner.invoke(cli_main, ["review", str(suite_path), "--case", "refund"])
    assert rev.exit_code == 0, rev.output
    assert "case: refund" in rev.output
    # The 'hello' case must be filtered out — it should not appear as its own
    # detail panel. (We check the panel title specifically; the substring
    # 'hello' could otherwise leak in via input echoes.)
    assert "case: hello" not in rev.output


def test_cli_review_exits_zero_on_regression(tmp_path, monkeypatch):
    """Even when a case regresses, review's exit code stays 0."""
    monkeypatch.chdir(tmp_path)
    suite_path = _write_suite_file(tmp_path)
    cli = CliRunner()

    cli.invoke(cli_main, ["record", str(suite_path)])

    # Mutate the suite to simulate a regression (output no longer contains 'refund').
    suite_path.write_text(
        _QUICKSTART_SUITE.replace('"refund processed"', '"nope, sorry"')
    )

    rev = cli.invoke(cli_main, ["review", str(suite_path)])
    assert rev.exit_code == 0, rev.output
    assert "REGRESSION" in rev.output
    assert "regressed" in rev.output


def test_cli_review_zero_match_exits_two(tmp_path, monkeypatch):
    """A filter that matches nothing exits 2 — same contract as check/record."""
    monkeypatch.chdir(tmp_path)
    suite_path = _write_suite_file(tmp_path)
    cli = CliRunner()
    cli.invoke(cli_main, ["record", str(suite_path)])

    rev = cli.invoke(cli_main, ["review", str(suite_path), "--case", "does-not-exist"])
    assert rev.exit_code == 2
    assert "no cases matched" in rev.output

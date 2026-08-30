"""Tests for the GitHub Action's PR-comment builder (scripts/gha_pr_comment.py).

The markdown builder is a pure function of the ``--json-out`` envelopes, so
we drive it end-to-end: run record + check through the real CLI, feed the
resulting JSON to the builder, and assert on the comment.
"""

from __future__ import annotations

import importlib.util
import json
import textwrap
from pathlib import Path

from click.testing import CliRunner

from agentprdiff.cli import main

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gha_pr_comment.py"
_spec = importlib.util.spec_from_file_location("gha_pr_comment", _SCRIPT)
gha = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gha)


_TWO_SUITE_FILE = textwrap.dedent(
    """
    from agentprdiff import case, suite
    from agentprdiff.graders import contains

    def good(inp):
        return "refund processed"

    def flaky(inp):
        return "no help here"

    s1 = suite(
        name="billing",
        agent=good,
        cases=[case(name="refund", input="x", expect=[contains("refund")])],
    )
    s2 = suite(
        name="support",
        agent=good,
        cases=[case(name="greeting", input="y", expect=[contains("refund")])],
    )
    """
)


def _run_record_and_check(tmp_path: Path, mutate: str | None = None) -> Path:
    """Record baselines, optionally rewrite the suite, check with --json-out.
    Returns the report directory."""
    suite_file = tmp_path / "suite.py"
    suite_file.write_text(_TWO_SUITE_FILE, encoding="utf-8")
    root = str(tmp_path / ".agentprdiff")
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    runner = CliRunner()

    rec = runner.invoke(main, ["--root", root, "record", str(suite_file)])
    assert rec.exit_code == 0, rec.output

    if mutate:
        suite_file.write_text(mutate, encoding="utf-8")

    runner.invoke(
        main,
        [
            "--root", root, "check", str(suite_file),
            "--json-out", str(report_dir / "report-1.json"),
        ],
    )
    return report_dir


class TestJsonEnvelope:
    def test_multi_suite_json_keeps_every_suite(self, tmp_path):
        report_dir = _run_record_and_check(tmp_path)
        payload = json.loads((report_dir / "report-1.json").read_text())
        suites = [r["suite"] for r in payload["reports"]]
        assert suites == ["billing", "support"]  # previously only the last survived


class TestCommentBuilder:
    def test_green_comment(self, tmp_path):
        report_dir = _run_record_and_check(tmp_path)
        body = gha.build_comment(gha.load_reports(report_dir))
        assert gha.MARKER in body
        assert "No behavioral regressions" in body
        assert "| billing | refund | 🟢 pass" in body
        assert "| support | greeting | 🟢 pass" in body

    def test_regression_comment_names_the_flipped_assertion(self, tmp_path):
        regressed = _TWO_SUITE_FILE.replace(
            'return "refund processed"', 'return "no idea"'
        )
        report_dir = _run_record_and_check(tmp_path, mutate=regressed)
        body = gha.build_comment(gha.load_reports(report_dir))
        assert "regressed" in body
        assert "🔴 regression" in body
        assert "`contains('refund')` now fails" in body
        assert "output diff — billing/refund" in body
        assert "```diff" in body

    def test_empty_report_dir_warns(self, tmp_path):
        empty = tmp_path / "none"
        empty.mkdir()
        body = gha.build_comment(gha.load_reports(empty))
        assert "No agentprdiff reports were produced" in body

    def test_legacy_single_envelope_still_parses(self, tmp_path):
        report_dir = _run_record_and_check(tmp_path)
        payload = json.loads((report_dir / "report-1.json").read_text())
        legacy = payload["reports"][0]  # old single-suite shape
        (report_dir / "report-1.json").write_text(json.dumps(legacy))
        envelopes = gha.load_reports(report_dir)
        assert len(envelopes) == 1
        assert envelopes[0]["suite"] == "billing"

    def test_multi_run_tally_shown(self, tmp_path):
        report_dir = _run_record_and_check(tmp_path)
        payload = json.loads((report_dir / "report-1.json").read_text())
        payload["reports"][0]["cases"][0]["runs_total"] = 3
        payload["reports"][0]["cases"][0]["runs_passed"] = 2
        payload["reports"][0]["cases"][0]["min_pass_rate"] = 0.6
        (report_dir / "report-1.json").write_text(json.dumps(payload))
        body = gha.build_comment(gha.load_reports(report_dir))
        assert "2/3 (≥60%)" in body

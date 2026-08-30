"""End-to-end smoke test for the benchmark harness (stub mode, no API keys).

Verifies the two properties the benchmark's credibility rests on: an
unchanged configuration produces zero regressions, and a degraded one is
caught with named, mechanically-explained assertion flips.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DRIVER = REPO / "benchmark" / "run_benchmark.py"


def test_stub_scenarios_end_to_end(tmp_path):
    results_path = tmp_path / "RESULTS.md"
    env = {
        **os.environ,
        "BENCH_WORK_DIR": str(tmp_path / "bench-work"),
        "BENCH_RESULTS_PATH": str(results_path),
    }
    proc = subprocess.run(
        [sys.executable, str(DRIVER), "--scenarios", "stub-smoke,stub-regression"],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    results = results_path.read_text(encoding="utf-8")
    # Unchanged config: no false positives.
    assert "| stub-smoke | stub/stub-model → stub/stub-model | 0/7 | 0 |" in results
    # Degraded config: the guardrail and format regressions are caught by name.
    assert "`guardrail-no-refund`" in results
    assert "issue_refund" in results
    assert "`bare-json-only`" in results
    assert "`picks-acted-on-order`" in results

#!/usr/bin/env python3
"""Run the agentprdiff model-swap benchmark and generate RESULTS.md.

For each scenario, the driver:

1. records baselines for both suites with the *baseline* configuration
2. re-runs `agentprdiff check` with the *changed* configuration
3. collects the JSON reports and aggregates which behaviors regressed

Usage (from the repo root, with agentprdiff installed):

    # keyless smoke run of the harness itself — no API calls:
    python benchmark/run_benchmark.py --scenarios stub-smoke

    # the real matrix (needs OPENAI_API_KEY and ANTHROPIC_API_KEY):
    python benchmark/run_benchmark.py

    # a subset:
    python benchmark/run_benchmark.py --scenarios openai-downgrade,prompt-rewrite

Every grader in both suites is deterministic — no LLM-as-judge — so every
regression in the results is mechanically verifiable by re-running this
script.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
SUITES = [
    str(BENCH_DIR / "suites" / "support_suite.py"),
    str(BENCH_DIR / "suites" / "extraction_suite.py"),
]
WORK_DIR = Path(os.environ.get("BENCH_WORK_DIR", BENCH_DIR / ".bench"))
RESULTS_PATH = Path(os.environ.get("BENCH_RESULTS_PATH", BENCH_DIR / "RESULTS.md"))


@dataclass
class Config:
    provider: str
    model: str
    prompt_variant: str = "baseline"
    stub_quality: str = "good"

    def env(self) -> dict[str, str]:
        return {
            "BENCH_PROVIDER": self.provider,
            "BENCH_MODEL": self.model,
            "BENCH_PROMPT_VARIANT": self.prompt_variant,
            "BENCH_STUB_QUALITY": self.stub_quality,
        }

    def label(self) -> str:
        suffix = "" if self.prompt_variant == "baseline" else f" ({self.prompt_variant} prompt)"
        if self.provider == "stub" and self.stub_quality != "good":
            suffix += f" ({self.stub_quality})"
        return f"{self.provider}/{self.model}{suffix}"


@dataclass
class Scenario:
    name: str
    title: str
    baseline: Config
    changed: Config
    needs_keys: list[str] = field(default_factory=list)


SCENARIOS: list[Scenario] = [
    Scenario(
        name="stub-smoke",
        title="Harness smoke test (stub model, no API calls)",
        baseline=Config("stub", "stub-model"),
        changed=Config("stub", "stub-model"),
    ),
    Scenario(
        name="stub-regression",
        title="Harness regression demo (degraded stub — shows what a real regression report looks like, no API calls)",
        baseline=Config("stub", "stub-model"),
        changed=Config("stub", "stub-model", stub_quality="degraded"),
    ),
    Scenario(
        name="openai-downgrade",
        title="OpenAI cost downgrade: gpt-4o → gpt-4o-mini",
        baseline=Config("openai", "gpt-4o"),
        changed=Config("openai", "gpt-4o-mini"),
        needs_keys=["OPENAI_API_KEY"],
    ),
    Scenario(
        name="anthropic-downgrade",
        title="Anthropic cost downgrade: claude-sonnet-4-6 → claude-haiku-4-5",
        baseline=Config("anthropic", "claude-sonnet-4-6"),
        changed=Config("anthropic", "claude-haiku-4-5-20251001"),
        needs_keys=["ANTHROPIC_API_KEY"],
    ),
    Scenario(
        name="cross-vendor",
        title="Cross-vendor swap: gpt-4o → claude-haiku-4-5",
        baseline=Config("openai", "gpt-4o"),
        changed=Config("anthropic", "claude-haiku-4-5-20251001"),
        needs_keys=["OPENAI_API_KEY", "ANTHROPIC_API_KEY"],
    ),
    Scenario(
        name="prompt-rewrite",
        title='Prompt "cleanup": baseline system prompt → terse rewrite (same model)',
        baseline=Config("openai", "gpt-4o"),
        changed=Config("openai", "gpt-4o", prompt_variant="terse"),
        needs_keys=["OPENAI_API_KEY"],
    ),
]


def _run_cli(args: list[str], extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    env = {**os.environ, **extra_env}
    return subprocess.run(
        [sys.executable, "-m", "agentprdiff.cli", *args],
        env=env,
        capture_output=True,
        text=True,
    )


def run_scenario(sc: Scenario) -> dict:
    root = WORK_DIR / sc.name
    report_path = root / "report.json"
    print(f"\n=== {sc.name}: {sc.title}")

    print(f"  recording baseline on {sc.baseline.label()} ...")
    rec = _run_cli(
        ["--root", str(root), "record", *SUITES], sc.baseline.env()
    )
    if rec.returncode != 0:
        print(rec.stdout[-2000:], rec.stderr[-2000:], sep="\n")
        raise RuntimeError(f"record failed for scenario {sc.name}")

    print(f"  checking against {sc.changed.label()} ...")
    chk = _run_cli(
        [
            "--root", str(root),
            "check", *SUITES,
            "--json-out", str(report_path),
            "--no-fail-on",  # the driver aggregates; don't stop the matrix
        ],
        sc.changed.env(),
    )
    if not report_path.exists():
        print(chk.stdout[-2000:], chk.stderr[-2000:], sep="\n")
        raise RuntimeError(f"check produced no report for scenario {sc.name}")

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    return {"scenario": sc, "reports": payload["reports"]}


def summarize(results: list[dict]) -> str:
    lines: list[str] = [
        "# agentprdiff model-swap benchmark — results",
        "",
        "Generated by `benchmark/run_benchmark.py`. Every grader is",
        "deterministic (no LLM-as-judge), so every regression below is",
        "mechanically verifiable by re-running the script.",
        "",
        "## Summary",
        "",
        "| Scenario | Change | Cases regressed | Assertions flipped |",
        "|---|---|---|---|",
    ]
    details: list[str] = []

    for r in results:
        sc: Scenario = r["scenario"]
        flips: list[tuple[str, str, str, str]] = []
        cases_regressed = 0
        cases_total = 0
        for env in r["reports"]:
            cases_total += env["summary"]["cases_total"]
            cases_regressed += env["summary"]["cases_regressed"]
            for case in env["cases"]:
                delta = case.get("delta") or {}
                for ch in delta.get("assertion_changes", []):
                    was, now = ch.get("baseline_passed"), ch.get("current_passed")
                    if (was is None or was) and now is False:
                        flips.append(
                            (
                                env["suite"],
                                case["case_name"],
                                ch.get("grader_id") or ch.get("grader_name", "?"),
                                ch.get("current_reason", ""),
                            )
                        )

        lines.append(
            f"| {sc.name} | {sc.baseline.label()} → {sc.changed.label()} "
            f"| {cases_regressed}/{cases_total} | {len(flips)} |"
        )

        details.append(f"\n## {sc.title}\n")
        details.append(
            f"Baseline `{sc.baseline.label()}` → changed `{sc.changed.label()}`.\n"
        )
        if not flips:
            details.append("No assertions regressed in this scenario.\n")
        else:
            details.append("| Suite | Case | Assertion | Why it now fails |")
            details.append("|---|---|---|---|")
            for suite_name, case_name, grader, reason in flips:
                reason = reason.replace("|", "\\|")
                details.append(
                    f"| {suite_name} | {case_name} | `{grader}` | {reason} |"
                )
            details.append("")

    return "\n".join(lines + details) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios",
        default=None,
        help=(
            "Comma-separated scenario names to run "
            f"(available: {', '.join(s.name for s in SCENARIOS)}). "
            "Default: every scenario whose API keys are present, "
            "excluding stub-smoke."
        ),
    )
    args = parser.parse_args()

    if args.scenarios:
        wanted = {s.strip() for s in args.scenarios.split(",")}
        unknown = wanted - {s.name for s in SCENARIOS}
        if unknown:
            parser.error(f"unknown scenario(s): {', '.join(sorted(unknown))}")
        selected = [s for s in SCENARIOS if s.name in wanted]
    else:
        selected = [
            s
            for s in SCENARIOS
            if not s.name.startswith("stub-")
            and all(os.environ.get(k) for k in s.needs_keys)
        ]
        if not selected:
            print(
                "No API keys found (OPENAI_API_KEY / ANTHROPIC_API_KEY). "
                "Run the keyless harness check with: "
                "python benchmark/run_benchmark.py --scenarios stub-smoke"
            )
            return 2

    missing = [
        (s.name, k)
        for s in selected
        for k in s.needs_keys
        if not os.environ.get(k)
    ]
    if missing:
        for name, key in missing:
            print(f"scenario {name} needs {key} — set it or drop the scenario")
        return 2

    results = [run_scenario(s) for s in selected]
    RESULTS_PATH.write_text(summarize(results), encoding="utf-8")
    print(f"\nWrote {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

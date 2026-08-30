#!/usr/bin/env python3
"""Post (or update) the agentprdiff behavioral-diff comment on a pull request.

Used by the composite GitHub Action in action.yml. Standard library only, so
it runs on any actions/setup-python interpreter without extra installs.

Usage:
    python gha_pr_comment.py <report_dir>

<report_dir> holds one or more ``--json-out`` files produced by
``agentprdiff check`` (either the multi-suite ``{"reports": [...]}`` envelope
from v0.5.0+ or the older single-suite envelope). Environment supplies the
GitHub context: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_API_URL (optional),
GITHUB_EVENT_PATH (to find the PR number).

The comment is upserted: a hidden marker identifies the previous comment and
it is edited in place, so a PR gets one living behavioral-diff comment, not a
trail of stale ones.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

MARKER = "<!-- agentprdiff-behavioral-diff -->"
MAX_COMMENT_BYTES = 60_000  # GitHub caps comments at 65536 chars; stay under.
MAX_DIFF_CHARS = 3_000


# ---------------------------------------------------------------------------
# Report loading.
# ---------------------------------------------------------------------------


def load_reports(report_dir: Path) -> list[dict]:
    """Read every JSON file in `report_dir` and normalize to a flat list of
    per-suite envelopes ({suite, mode, summary, cases})."""
    envelopes: list[dict] = []
    for path in sorted(report_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"::warning::could not read {path.name}: {exc}")
            continue
        if isinstance(payload, dict) and isinstance(payload.get("reports"), list):
            envelopes.extend(e for e in payload["reports"] if isinstance(e, dict))
        elif isinstance(payload, dict) and "suite" in payload:
            envelopes.append(payload)  # legacy single-suite envelope
    return envelopes


# ---------------------------------------------------------------------------
# Markdown rendering. Pure function of the envelopes — unit-testable.
# ---------------------------------------------------------------------------


def build_comment(envelopes: list[dict]) -> str:
    cases_total = sum(e.get("summary", {}).get("cases_total", 0) for e in envelopes)
    cases_regressed = sum(
        e.get("summary", {}).get("cases_regressed", 0) for e in envelopes
    )
    any_regression = any(
        e.get("summary", {}).get("has_regression") for e in envelopes
    )
    n_suites = len(envelopes)

    lines: list[str] = [MARKER, "## agentprdiff — behavioral diff", ""]
    if not envelopes:
        lines.append(
            "No agentprdiff reports were produced — check the job logs for "
            "errors before trusting this run."
        )
        return "\n".join(lines)

    if any_regression:
        lines.append(
            f"**✗ {cases_regressed} of {cases_total} case(s) regressed** "
            f"across {n_suites} suite(s)."
        )
    else:
        lines.append(
            f"**✓ No behavioral regressions** — {cases_total} case(s) "
            f"across {n_suites} suite(s) match their baselines."
        )
    lines.append("")

    lines.append("| Suite | Case | Result | Runs | Cost Δ | Latency Δ | What changed |")
    lines.append("|---|---|---|---|---|---|---|")
    diff_sections: list[str] = []

    for env in envelopes:
        suite_name = env.get("suite", "?")
        for case in env.get("cases", []):
            name = case.get("case_name", "?")
            delta = case.get("delta") or {}
            runs_total = case.get("runs_total", 1)
            runs_passed = case.get("runs_passed", 1)
            min_rate = case.get("min_pass_rate", 1.0)

            regressed, notes = _case_status(case, delta)
            result = "🔴 regression" if regressed else "🟢 pass"
            if not regressed and not _case_passed(case):
                result = "🔴 fail"

            runs_cell = "—"
            if runs_total > 1:
                runs_cell = f"{runs_passed}/{runs_total} (≥{min_rate:.0%})"

            cost_cell = _fmt_delta(delta.get("cost_delta_usd"), "${:+.4f}")
            lat_cell = _fmt_delta(delta.get("latency_delta_ms"), "{:+.0f} ms")

            lines.append(
                f"| {suite_name} | {name} | {result} | {runs_cell} "
                f"| {cost_cell} | {lat_cell} | {'; '.join(notes) or '—'} |"
            )

            output_diff = delta.get("output_diff") or ""
            if regressed and output_diff:
                clipped = output_diff[:MAX_DIFF_CHARS]
                if len(output_diff) > MAX_DIFF_CHARS:
                    clipped += "\n… (truncated; full diff in the job log)"
                diff_sections.append(
                    f"<details><summary>output diff — {suite_name}/{name}"
                    f"</summary>\n\n```diff\n{clipped}\n```\n\n</details>"
                )

    if diff_sections:
        lines.append("")
        lines.extend(diff_sections)

    lines.append("")
    lines.append(
        "<sub>Posted by [agentprdiff](https://agentprdiff.dev/) — snapshot "
        "tests for LLM agent behavior. Baselines live in your repo; "
        "re-record and commit to accept intentional changes.</sub>"
    )

    body = "\n".join(lines)
    if len(body.encode("utf-8")) > MAX_COMMENT_BYTES:
        # Drop diff sections first; the table is the essential part.
        body = "\n".join(
            line for line in lines if not line.startswith("<details>")
        )
    return body


def _case_passed(case: dict) -> bool:
    runs_total = case.get("runs_total", 1)
    runs_passed = case.get("runs_passed")
    if runs_passed is not None and runs_total:
        return (runs_passed / runs_total) >= case.get("min_pass_rate", 1.0)
    results = case.get("grader_results", [])
    return all(r.get("passed") for r in results) and not (case.get("trace") or {}).get(
        "error"
    )


def _case_status(case: dict, delta: dict) -> tuple[bool, list[str]]:
    """Return (regressed, notes) for one case envelope."""
    notes: list[str] = []
    regressed = not _case_passed(case)

    for change in delta.get("assertion_changes", []):
        baseline_passed = change.get("baseline_passed")
        current_passed = change.get("current_passed")
        if (baseline_passed is None or baseline_passed) and current_passed is False:
            regressed = True
            reason = change.get("current_reason") or ""
            notes.append(f"`{change.get('grader_name', '?')}` now fails — {reason}")
        elif baseline_passed is False and current_passed:
            notes.append(f"`{change.get('grader_name', '?')}` now passes")

    trace_error = (case.get("trace") or {}).get("error")
    baseline_error = delta.get("baseline_error")
    if trace_error and not baseline_error:
        regressed = True
        notes.append(f"agent raised: {trace_error}")

    if delta.get("tool_sequence_changed"):
        notes.append(
            f"tools: {delta.get('baseline_tool_sequence')} → "
            f"{delta.get('current_tool_sequence')}"
        )
    if delta.get("output_changed") and not notes:
        notes.append("output changed (assertions still pass)")
    return regressed, notes


def _fmt_delta(value, fmt: str) -> str:
    if not value:
        return "—"
    try:
        return fmt.format(value)
    except (TypeError, ValueError):
        return "—"


# ---------------------------------------------------------------------------
# GitHub API — upsert the comment.
# ---------------------------------------------------------------------------


def _api(method: str, url: str, token: str, payload: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "agentprdiff-action")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:  # noqa: S310 — https API URL
        return json.loads(resp.read().decode("utf-8") or "null")


def find_pr_number() -> int | None:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).exists():
        try:
            event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        pr = event.get("pull_request") or {}
        if isinstance(pr.get("number"), int):
            return pr["number"]
        issue = event.get("issue") or {}
        if isinstance(issue.get("number"), int):
            return issue["number"]
    return None


def upsert_comment(body: str) -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    api_base = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    pr_number = find_pr_number()

    if not (token and repo and pr_number):
        print(
            "::warning::not a pull_request context or GITHUB_TOKEN/"
            "GITHUB_REPOSITORY missing — skipping the PR comment."
        )
        return

    comments_url = f"{api_base}/repos/{repo}/issues/{pr_number}/comments"
    try:
        existing = _api("GET", f"{comments_url}?per_page=100", token) or []
        mine = next(
            (c for c in existing if MARKER in (c.get("body") or "")), None
        )
        if mine:
            _api(
                "PATCH",
                f"{api_base}/repos/{repo}/issues/comments/{mine['id']}",
                token,
                {"body": body},
            )
            print(f"updated behavioral-diff comment {mine['id']} on PR #{pr_number}")
        else:
            created = _api("POST", comments_url, token, {"body": body})
            print(
                f"posted behavioral-diff comment {created.get('id')} "
                f"on PR #{pr_number}"
            )
    except urllib.error.HTTPError as exc:
        print(
            f"::warning::could not post the PR comment (HTTP {exc.code}). "
            "If this is a fork PR, the default token can't comment — the "
            "behavioral diff is still in the job log above."
        )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: gha_pr_comment.py <report_dir>", file=sys.stderr)
        return 2
    report_dir = Path(argv[1])
    envelopes = load_reports(report_dir)
    body = build_comment(envelopes)
    print(body)  # always visible in the job log, even when commenting fails
    upsert_comment(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

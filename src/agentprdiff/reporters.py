"""Reporters — render RunReports for humans and for CI.

`TerminalReporter` uses rich for pretty output. `JsonReporter` writes a
stable JSON envelope you can archive as a CI artifact. `ReviewReporter` is
the verbose, per-case view used by ``agentprdiff review`` for local
iteration — think `pytest -k` rather than the summary table CI cares about.
"""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .differ import AssertionChange, TraceDelta
from .graders.semantic import case_uses_semantic, describe_default_judge
from .runner import CaseReport, RunReport


class TerminalReporter:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def render(self, report: RunReport) -> None:
        header = Text()
        header.append(f"agentprdiff {report.mode} ", style="bold cyan")
        header.append("— suite ", style="dim")
        header.append(f"{report.suite_name}", style="bold")
        header.append(
            f"  ({report.cases_passed}/{report.cases_total} passed, "
            f"{report.cases_regressed} regressed)",
            style="dim",
        )
        self.console.print(header)
        _maybe_print_judge_banner(self.console, report)

        table = Table(show_header=True, header_style="bold", show_lines=False, expand=True)
        table.add_column("Case", style="bold")
        table.add_column("Result")
        table.add_column("Cost Δ", justify="right")
        table.add_column("Latency Δ", justify="right")
        table.add_column("Notes")

        for cr in report.case_reports:
            if cr.has_regression:
                result = Text("REGRESSION", style="bold red")
            elif cr.passed:
                result = Text("PASS", style="bold green")
            else:
                result = Text("FAIL", style="bold red")

            cost_cell = ""
            latency_cell = ""
            notes = []
            if cr.delta is not None:
                if cr.delta.cost_delta_usd:
                    cost_cell = _format_delta(cr.delta.cost_delta_usd, "${:+.4f}")
                if cr.delta.latency_delta_ms:
                    latency_cell = _format_delta(cr.delta.latency_delta_ms, "{:+.0f} ms")
                if cr.delta.tool_sequence_changed:
                    notes.append(
                        "tools: "
                        f"{cr.delta.baseline_tool_sequence} → "
                        f"{cr.delta.current_tool_sequence}"
                    )
                if cr.delta.output_changed and not cr.has_regression:
                    notes.append("output changed")
                for ac in cr.delta.regressions:
                    notes.append(f"[red]{ac.grader_name}[/red] {ac.current_reason}")
            if cr.trace.error:
                notes.append(f"[red]error:[/red] {cr.trace.error}")
            for r in cr.grader_results:
                if not r.passed and cr.delta is None:
                    notes.append(f"[red]{r.grader_name}[/red] {r.reason}")

            table.add_row(cr.case_name, result, cost_cell, latency_cell, "\n".join(notes) or "—")

        self.console.print(table)

        # Per-regression expanded section.
        for cr in report.case_reports:
            if cr.has_regression and cr.delta is not None and cr.delta.output_diff:
                self.console.print(
                    Panel(
                        cr.delta.output_diff,
                        title=f"{cr.case_name}: output diff",
                        border_style="red",
                    )
                )

        if report.mode == "check":
            if report.has_regression:
                self.console.print(
                    Text(
                        f"\n✗ {report.cases_regressed} regression(s) detected.",
                        style="bold red",
                    )
                )
            else:
                self.console.print(Text("\n✓ no regressions.", style="bold green"))


class JsonReporter:
    """Write a stable JSON envelope suitable for CI artifact archiving."""

    def render(self, report: RunReport, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "suite": report.suite_name,
            "mode": report.mode,
            "summary": {
                "cases_total": report.cases_total,
                "cases_passed": report.cases_passed,
                "cases_regressed": report.cases_regressed,
                "has_regression": report.has_regression,
            },
            "cases": [cr.model_dump(mode="json") for cr in report.case_reports],
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path


def _format_delta(value: float, fmt: str) -> str:
    if value == 0:
        return ""
    text = fmt.format(value)
    color = "green" if value < 0 else "red"
    return f"[{color}]{text}[/{color}]"


def _maybe_print_judge_banner(console: Console, report: RunReport) -> None:
    """Print "semantic judge: <mode>" once per run if any case used semantic().

    Skipped silently when no case has a semantic grader — most suites don't
    use them and the banner would be noise. When a suite *does* have semantic
    coverage, the banner makes the silent fake_judge fallback (no key, no
    AGENTGUARD_JUDGE) loud at the moment of execution rather than buried in
    trace JSON. Coloured yellow when fake_judge would run so the warning is
    visually distinct from real-judge runs.
    """
    if not any(case_uses_semantic(cr.grader_results) for cr in report.case_reports):
        return
    description = describe_default_judge()
    style = "yellow" if description.startswith("fake_judge") else "dim"
    line = Text()
    line.append("semantic judge: ", style="dim")
    line.append(description, style=style)
    console.print(line)


# ---------------------------------------------------------------------------
# ReviewReporter — verbose per-case view for `agentprdiff review`.
# ---------------------------------------------------------------------------


class ReviewReporter:
    """Render one detailed panel per case in a RunReport.

    Designed for the local-iteration workflow: you've narrowed to a single
    case (or a handful) with ``--case`` and want to see *everything* about
    that run — input, output, every assertion's baseline-vs-current verdict,
    cost / latency / token deltas, the tool-call sequence diff, and any
    output diff. This is intentionally noisier than ``TerminalReporter``;
    it's for humans staring at one case, not CI scanning a hundred.
    """

    # Symbols used in the assertion table.
    _MARK_PASS = "✓"
    _MARK_FAIL = "✗"
    _MARK_ABSENT = "—"

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    # ------------------------------------------------------------------ api

    def render(self, report: RunReport) -> None:
        """Print a header + one detailed panel per case."""
        self._render_header(report)
        for cr in report.case_reports:
            self.console.print(self._render_case(cr))
        self._render_footer(report)

    # ------------------------------------------------------------ internals

    def _render_header(self, report: RunReport) -> None:
        header = Text()
        header.append("agentprdiff review ", style="bold cyan")
        header.append("— suite ", style="dim")
        header.append(f"{report.suite_name}", style="bold")
        header.append(
            f"  ({report.cases_total} case{'s' if report.cases_total != 1 else ''})",
            style="dim",
        )
        self.console.print(header)
        _maybe_print_judge_banner(self.console, report)
        self.console.print()

    def _render_footer(self, report: RunReport) -> None:
        if report.cases_total == 0:
            return
        regressed = report.cases_regressed
        if regressed:
            self.console.print(
                Text(
                    f"\n{regressed} of {report.cases_total} case(s) "
                    f"regressed. (review exits 0 — use `agentprdiff check` "
                    f"in CI.)",
                    style="yellow",
                )
            )
        else:
            self.console.print(
                Text(
                    f"\n✓ all {report.cases_total} case(s) clean.",
                    style="green",
                )
            )

    def _render_case(self, cr: CaseReport) -> Panel:
        """One Panel per case, containing several stacked sub-blocks."""
        status = self._status_text(cr)
        baseline_state = (
            "baseline: present"
            if cr.delta is not None and cr.delta.baseline_exists
            else "baseline: not yet recorded"
        )

        sections: list[object] = []

        # Top metadata line.
        meta = Text()
        meta.append(f"suite: {cr.suite_name}", style="dim")
        meta.append("    ")
        meta.append("status: ", style="dim")
        meta.append_text(status)
        meta.append("    ")
        meta.append(baseline_state, style="dim")
        sections.append(meta)

        sections.append(self._render_input(cr))

        if cr.trace.error:
            sections.append(self._render_error(cr))

        sections.append(self._render_assertions(cr))

        if cr.delta is not None and cr.delta.baseline_exists:
            sections.append(self._render_metrics(cr.delta))
            sections.append(self._render_tools(cr.delta))
            sections.append(self._render_output(cr))
        else:
            # No baseline — just show the current output verbatim.
            sections.append(self._render_output(cr))

        title = Text()
        title.append("case: ", style="dim")
        title.append(cr.case_name, style="bold")
        border = (
            "red" if cr.has_regression else ("yellow" if not cr.passed else "green")
        )
        return Panel(Group(*sections), title=title, border_style=border, padding=(1, 2))

    # -- sub-blocks ----------------------------------------------------------

    def _status_text(self, cr: CaseReport) -> Text:
        if cr.has_regression:
            return Text("REGRESSION", style="bold red")
        if cr.passed:
            return Text("PASS", style="bold green")
        return Text("FAIL", style="bold yellow")

    def _render_input(self, cr: CaseReport) -> Group:
        body = _stringify(cr.trace.input)
        return Group(
            Text("\ninput:", style="bold"),
            Text(_indent(body, 2), style="dim"),
        )

    def _render_error(self, cr: CaseReport) -> Group:
        return Group(
            Text("\nerror:", style="bold red"),
            Text(_indent(cr.trace.error or "", 2), style="red"),
        )

    def _render_assertions(self, cr: CaseReport) -> Group:
        header = Text("\nassertions:", style="bold")
        if not cr.grader_results:
            return Group(header, Text("  (no assertions defined)", style="dim"))

        table = Table(
            show_header=True,
            header_style="dim",
            show_edge=False,
            padding=(0, 1),
            box=None,
        )
        # When there's a baseline we show before → after; otherwise just current.
        has_baseline_marks = (
            cr.delta is not None
            and cr.delta.baseline_exists
            and any(c.baseline_passed is not None for c in cr.delta.assertion_changes)
        )
        if has_baseline_marks:
            table.add_column("was", justify="center", width=3)
            table.add_column("→", justify="center", width=1, style="dim")
        table.add_column("now", justify="center", width=3)
        table.add_column("grader")
        table.add_column("reason", overflow="fold")

        # We index grader_results by name so we can pair them up with the
        # delta's AssertionChange (which already knows baseline pass/fail).
        deltas_by_name: dict[str, AssertionChange] = {}
        if cr.delta is not None:
            for ac in cr.delta.assertion_changes:
                deltas_by_name[ac.grader_name] = ac

        for r in cr.grader_results:
            ac = deltas_by_name.get(r.grader_name)
            now_mark = self._mark(r.passed, regression=False)
            if has_baseline_marks:
                was = ac.baseline_passed if ac is not None else None
                regression = ac.is_regression if ac is not None else False
                was_mark = self._mark(was, regression=False)
                if regression:
                    now_mark = Text(self._MARK_FAIL, style="bold red")
                elif (
                    ac is not None
                    and ac.is_improvement
                ):
                    now_mark = Text(self._MARK_PASS, style="bold green")
                table.add_row(
                    was_mark,
                    Text("→", style="dim"),
                    now_mark,
                    Text(r.grader_name, style="bold" if not r.passed else ""),
                    Text(r.reason, style="dim" if r.passed else "red"),
                )
            else:
                table.add_row(
                    now_mark,
                    Text(r.grader_name, style="bold" if not r.passed else ""),
                    Text(r.reason, style="dim" if r.passed else "red"),
                )

        return Group(header, table)

    def _render_metrics(self, delta: TraceDelta) -> Group:
        header = Text("\nmetrics:", style="bold")
        rows = [
            (
                "cost",
                _format_money_delta(delta.cost_delta_usd),
            ),
            (
                "latency",
                _format_ms_delta(delta.latency_delta_ms),
            ),
            (
                "prompt tokens",
                _format_int_delta(delta.prompt_tokens_delta),
            ),
            (
                "completion tokens",
                _format_int_delta(delta.completion_tokens_delta),
            ),
        ]
        table = Table(show_header=False, show_edge=False, padding=(0, 1), box=None)
        table.add_column("metric", style="dim")
        table.add_column("delta")
        for name, val in rows:
            table.add_row(name, val)
        return Group(header, table)

    def _render_tools(self, delta: TraceDelta) -> Group:
        header = Text("\ntools:", style="bold")
        if not delta.tool_sequence_changed:
            seq = delta.current_tool_sequence or ["(none)"]
            return Group(
                header,
                Text(f"  {seq}  (unchanged)", style="dim"),
            )
        return Group(
            header,
            Text(f"  baseline: {delta.baseline_tool_sequence}", style="dim"),
            Text(f"  current:  {delta.current_tool_sequence}", style="bold yellow"),
        )

    def _render_output(self, cr: CaseReport) -> Group:
        header = Text("\noutput:", style="bold")
        delta = cr.delta
        if delta is not None and delta.baseline_exists:
            if not delta.output_changed:
                return Group(header, Text("  (unchanged)", style="dim"))
            # Show the unified diff in its own panel for readability.
            return Group(
                header,
                Panel(
                    delta.output_diff,
                    border_style="yellow",
                    padding=(0, 1),
                ),
            )
        # No baseline — print the raw output.
        body = _stringify(cr.trace.output)
        return Group(header, Text(_indent(body, 2), style="dim"))

    def _mark(self, passed: bool | None, *, regression: bool) -> Text:
        if passed is None:
            return Text(self._MARK_ABSENT, style="dim")
        if passed:
            return Text(self._MARK_PASS, style="green")
        return Text(self._MARK_FAIL, style="red")


# ---------------------------------------------------------------------------
# Small formatting helpers used by ReviewReporter.
# ---------------------------------------------------------------------------


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    if not text:
        return pad + "(empty)"
    return "\n".join(pad + line for line in text.splitlines())


def _format_money_delta(value: float) -> Text:
    if value == 0:
        return Text("no change", style="dim")
    color = "green" if value < 0 else "red"
    return Text(f"{value:+.4f} USD", style=color)


def _format_ms_delta(value: float) -> Text:
    if value == 0:
        return Text("no change", style="dim")
    color = "green" if value < 0 else "red"
    return Text(f"{value:+.1f} ms", style=color)


def _format_int_delta(value: int) -> Text:
    if value == 0:
        return Text("no change", style="dim")
    color = "green" if value < 0 else "red"
    return Text(f"{value:+d}", style=color)

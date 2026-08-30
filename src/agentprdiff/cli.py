"""Command-line interface for agentprdiff.

Six subcommands:

* `agentprdiff init`     — scaffold a .agentprdiff/ directory
* `agentprdiff record`   — record baselines for every suite in the given files
* `agentprdiff check`    — compare against baselines; exit 1 on regression
* `agentprdiff review`   — verbose per-case diff for local iteration; exit 0
* `agentprdiff scaffold` — stamp out the canonical suite layout
* `agentprdiff diff`     — show the saved baseline trace for one case

`record`, `check`, and `review` accept ``--case`` / ``--skip`` filters and a
``--list`` flag for case discovery; see :mod:`agentprdiff.filtering` for
pattern syntax. ``review`` is the local-iteration counterpart to ``check``:
it runs the same comparison but renders one detailed panel per case and
always exits 0, the way ``pytest -k`` lets you focus on a single test
without changing exit semantics.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .core import Suite
from .filtering import apply_filter, parse_patterns
from .loader import load_suites
from .reporters import JsonReporter, ReviewReporter, TerminalReporter
from .runner import Runner
from .scaffold import VALID_RECIPES, scaffold
from .store import BaselineStore

# Click options shared by `record` and `check`. Defined once so the help text
# stays in sync between the two commands.
_CASE_OPTION = click.option(
    "--case",
    "case_patterns",
    multiple=True,
    metavar="PATTERN",
    help=(
        "Only run cases whose name matches PATTERN. Repeatable; "
        "comma-separated values are also split (`--case a,b`). "
        "Globs (`*`, `?`) and substrings both work; prefix `~` to negate "
        "(`--case ~slow`). Use `suite:case` to qualify by suite."
    ),
)
_SKIP_OPTION = click.option(
    "--skip",
    "skip_patterns",
    multiple=True,
    metavar="PATTERN",
    help="Skip cases matching PATTERN. Same syntax as --case; repeatable.",
)
_LIST_OPTION = click.option(
    "--list",
    "list_only",
    is_flag=True,
    help="Print suite/case names without running anything, then exit.",
)
_CONCURRENCY_OPTION = click.option(
    "--concurrency",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help=(
        "Execute up to this many cases at once on a thread pool. Agent "
        "suites are I/O-bound, so this cuts wall-clock time near-linearly. "
        "Your agent must be safe to call from multiple threads."
    ),
)


@click.group(help="Snapshot testing for LLM agents.")
@click.version_option(package_name="agentprdiff", prog_name="agentprdiff")
@click.option(
    "--root",
    default=".agentprdiff",
    show_default=True,
    help="Directory where baselines and runs are stored.",
)
@click.pass_context
def main(ctx: click.Context, root: str) -> None:
    ctx.ensure_object(dict)
    ctx.obj["store"] = BaselineStore(root=root)


@main.command("init")
@click.pass_context
def cmd_init(ctx: click.Context) -> None:
    """Create the .agentprdiff/ directory and a starter .gitignore."""
    store: BaselineStore = ctx.obj["store"]
    store.ensure_initialized()
    click.echo(f"initialized {store.root}/")
    click.echo(f"  baselines: {store.baselines_dir}/   (commit this)")
    click.echo(f"  runs:      {store.runs_dir}/        (gitignored)")


@main.command("record")
@click.argument(
    "suite_files",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--json-out", type=click.Path(path_type=Path), help="Write JSON report to this path.")
@_CASE_OPTION
@_SKIP_OPTION
@_LIST_OPTION
@_CONCURRENCY_OPTION
@click.pass_context
def cmd_record(
    ctx: click.Context,
    suite_files: tuple[Path, ...],
    json_out: Path | None,
    case_patterns: tuple[str, ...],
    skip_patterns: tuple[str, ...],
    list_only: bool,
    concurrency: int,
) -> None:
    """Run every suite in SUITE_FILES and save each trace as the baseline."""
    store: BaselineStore = ctx.obj["store"]
    runner = Runner(store, concurrency=concurrency)
    terminal = TerminalReporter()

    suites_all = [s for f in suite_files for s in load_suites(f)]
    if list_only:
        _print_listing(suites_all)
        return

    suites = _select_or_exit(suites_all, case_patterns, skip_patterns)

    any_error = False
    reports = []
    for s in suites:
        report = runner.record(s)
        terminal.render(report)
        reports.append(report)
        # record mode doesn't fail on grader failures, but a literal exception
        # during execution still warrants a nonzero exit.
        if any(cr.trace.error for cr in report.case_reports):
            any_error = True
    if json_out:
        JsonReporter().render_many(reports, json_out)

    sys.exit(1 if any_error else 0)


@main.command("check")
@click.argument(
    "suite_files",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--json-out", type=click.Path(path_type=Path), help="Write JSON report to this path.")
@_CASE_OPTION
@_SKIP_OPTION
@_LIST_OPTION
@click.option(
    "--fail-on/--no-fail-on",
    "fail_on_regression",
    default=True,
    show_default=True,
    help="Exit non-zero when regressions are detected.",
)
@click.option(
    "--strict-judge",
    is_flag=True,
    default=False,
    help=(
        "Fail when any semantic() grader silently fell back to fake_judge "
        "(no AGENTPRDIFF_JUDGE and no provider API key set). Recommended in "
        "CI; will become the default in v1.0. Explicit opt-in via "
        "AGENTPRDIFF_JUDGE=fake still passes."
    ),
)
@click.option(
    "--runs",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help=(
        "Execute each case this many times; a case passes when at least its "
        "min_pass_rate fraction of attempts fully pass (default 1.0 — all "
        "attempts). The flakiness guard for stochastic agents: e.g. --runs 3 "
        "with case(..., min_pass_rate=0.6) tolerates one wobble out of three."
    ),
)
@_CONCURRENCY_OPTION
@click.pass_context
def cmd_check(
    ctx: click.Context,
    suite_files: tuple[Path, ...],
    json_out: Path | None,
    case_patterns: tuple[str, ...],
    skip_patterns: tuple[str, ...],
    list_only: bool,
    fail_on_regression: bool,
    strict_judge: bool,
    runs: int,
    concurrency: int,
) -> None:
    """Run every suite in SUITE_FILES and diff against saved baselines."""
    store: BaselineStore = ctx.obj["store"]
    runner = Runner(store, runs=runs, concurrency=concurrency)
    terminal = TerminalReporter()

    suites_all = [s for f in suite_files for s in load_suites(f)]
    if list_only:
        _print_listing(suites_all)
        return

    suites = _select_or_exit(suites_all, case_patterns, skip_patterns)

    any_regression = False
    silent_judge_cases: list[str] = []
    reports = []
    for s in suites:
        report = runner.check(s)
        terminal.render(report)
        reports.append(report)
        any_regression = any_regression or report.has_regression
        for cr in report.case_reports:
            if any(r.metadata.get("silent_fallback") for r in cr.grader_results):
                silent_judge_cases.append(f"{cr.suite_name}/{cr.case_name}")
    if json_out:
        JsonReporter().render_many(reports, json_out)

    if strict_judge and silent_judge_cases:
        click.echo(
            "\n--strict-judge: semantic() graders were judged by fake_judge via "
            "silent fallback (no AGENTPRDIFF_JUDGE, no OPENAI_API_KEY/"
            "ANTHROPIC_API_KEY) in:\n  "
            + "\n  ".join(silent_judge_cases)
            + "\nSet a real judge (AGENTPRDIFF_JUDGE=openai|anthropic plus the "
            "matching API key) or opt in explicitly with AGENTPRDIFF_JUDGE=fake.",
            err=True,
        )
        sys.exit(1)

    sys.exit(1 if (any_regression and fail_on_regression) else 0)


@main.command("review")
@click.argument("suite_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@_CASE_OPTION
@_SKIP_OPTION
@_LIST_OPTION
@click.pass_context
def cmd_review(
    ctx: click.Context,
    suite_file: Path,
    case_patterns: tuple[str, ...],
    skip_patterns: tuple[str, ...],
    list_only: bool,
) -> None:
    """Run cases in SUITE_FILE and print a detailed baseline-vs-current view.

    Designed for local iteration on a single failing case — pair it with
    ``--case`` the way you'd use ``pytest -k``. ``review`` always exits 0
    so you can pipe it into a watcher / re-run loop without your shell
    going red on every regression. Use ``agentprdiff check`` for the CI
    gate.
    """
    store: BaselineStore = ctx.obj["store"]
    runner = Runner(store)
    reporter = ReviewReporter()

    suites_all = load_suites(suite_file)
    if list_only:
        _print_listing(suites_all)
        return

    suites = _select_or_exit(suites_all, case_patterns, skip_patterns)

    for s in suites:
        report = runner.check(s)
        reporter.render(report)


@main.command("scaffold")
@click.argument("name")
@click.option(
    "--recipe",
    type=click.Choice(VALID_RECIPES, case_sensitive=False),
    default="sync-openai",
    show_default=True,
    help=(
        "Eval-wrapper template to generate. "
        "`sync-openai` uses instrument_client with a sync OpenAI client; "
        "`async-openai` does the same with AsyncOpenAI (the runner resolves "
        "async agents natively since 0.5.0; the template keeps a sync "
        "wrapper for older pins); `stubbed` substitutes a single LLM helper "
        "(see docs/adapters.md)."
    ),
)
@click.option(
    "--dir",
    "root_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Project root to scaffold into. Defaults to the current directory.",
)
def cmd_scaffold(name: str, recipe: str, root_dir: Path) -> None:
    """Stamp out the canonical suite layout for NAME.

    Produces ``suites/__init__.py``, ``_eval_agent.py``, ``_stubs.py``,
    ``<NAME>.py``, ``<NAME>_cases.md``, ``suites/README.md``, and
    ``.github/workflows/agentprdiff.yml``.
    Existing files are never overwritten — they are reported as `[skip]`.
    """
    try:
        result = scaffold(name, recipe=recipe, root=root_dir)
    except ValueError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    for p in result.written:
        click.echo(f"  [new]  {p.relative_to(root_dir) if p.is_relative_to(root_dir) else p}")
    for p in result.skipped:
        click.echo(
            f"  [skip] {p.relative_to(root_dir) if p.is_relative_to(root_dir) else p} "
            "(already exists)"
        )

    if not result.written:
        click.echo("\nNothing scaffolded — every target file already exists.")
        return

    click.echo(
        f"\nScaffolded {len(result.written)} file(s) for '{name}' "
        f"using recipe '{recipe}'."
    )
    click.echo("\nNext steps:")
    click.echo("  1. Edit suites/_eval_agent.py — replace the TODOs with imports")
    click.echo("     and call sites for your production agent.")
    click.echo("  2. Edit suites/_stubs.py — one stub per side-effecting tool.")
    click.echo(f"  3. Edit suites/{name}.py — flesh out the case list.")
    click.echo("  4. Run: agentprdiff init")
    click.echo(f"  5. Run: agentprdiff record suites/{name}.py")


@main.command("diff")
@click.argument("suite_name")
@click.argument("case_name")
@click.pass_context
def cmd_diff(ctx: click.Context, suite_name: str, case_name: str) -> None:
    """Show the saved baseline trace for SUITE_NAME / CASE_NAME as pretty JSON."""
    store: BaselineStore = ctx.obj["store"]
    trace = store.load_baseline(suite_name, case_name)
    if trace is None:
        click.echo(f"no baseline found for {suite_name}/{case_name}", err=True)
        sys.exit(2)
    click.echo(json.dumps(trace.model_dump(mode="json"), indent=2))


# ---------------------------------------------------------------------------
# Helpers shared between `record` and `check`.
# ---------------------------------------------------------------------------


def _print_listing(suites: list[Suite]) -> None:
    """Print suite/case names as a flat tree.

    Output is intentionally plain (no rich styling) so it pipes cleanly into
    grep / fzf when users want to discover what to filter on.
    """
    for s in suites:
        n = len(s.cases)
        click.echo(f"{s.name}  ({n} case{'s' if n != 1 else ''})")
        for c in s.cases:
            click.echo(f"  {c.name}")


def _select_or_exit(
    suites_all: list[Suite],
    case_patterns: tuple[str, ...],
    skip_patterns: tuple[str, ...],
) -> list[Suite]:
    """Apply ``--case`` / ``--skip`` filters and announce the selection.

    Returns the filtered suite list, or exits with code 2 if filters were
    provided but matched zero cases — an exit-0 silent zero-match is a worse
    failure mode than a noisy error.
    """
    include = parse_patterns(list(case_patterns))
    exclude = parse_patterns(list(skip_patterns))
    if not include and not exclude:
        return suites_all

    selected = apply_filter(suites_all, include=include, exclude=exclude)

    if not selected:
        click.echo("error: no cases matched --case/--skip filters.", err=True)
        all_names = [f"{s.name}/{c.name}" for s in suites_all for c in s.cases]
        if all_names:
            click.echo("available cases:", err=True)
            for name in all_names:
                click.echo(f"  {name}", err=True)
            click.echo(
                "(tip: run with --list to see suite/case names; patterns are "
                "case-insensitive substrings or globs.)",
                err=True,
            )
        sys.exit(2)

    # Announce per-suite selection so a partial match doesn't silently look
    # like the suite shrank.
    selected_by_name = {s.name: s for s in selected}
    for s_orig in suites_all:
        s_new = selected_by_name.get(s_orig.name)
        if s_new is None:
            click.echo(
                f"skipping suite {s_orig.name} (0 of {len(s_orig.cases)} cases match)"
            )
            continue
        kept_names = ", ".join(c.name for c in s_new.cases)
        click.echo(
            f"running {len(s_new.cases)} of {len(s_orig.cases)} cases in "
            f"{s_orig.name}: {kept_names}"
        )

    return selected


if __name__ == "__main__":  # pragma: no cover
    main()

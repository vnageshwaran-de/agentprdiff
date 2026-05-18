"""Studio Tour — the guided end-to-end workflow.

Drives a fresh user from "I just connected a project" to "I have a regression
guard running in CI", in seven steps:

    1. connect         — picked the intake mode; this is implicitly done on
                          the tour page (the project exists).
    2. discover        — Studio walked the workspace; suites are listed.
    3. scaffold        — if discover found nothing, scaffold one (AI or
                          deterministic from *_cases.md cases).
    4. configure-keys  — if any suite uses a ``semantic(...)`` grader,
                          surface the Secrets form. Skip-able.
    5. record-baseline — at least one ``record`` run has succeeded.
    6. regression-demo — simulate a regression (edit + check + revert) so
                          the user sees the diff viewer. Skip-able.
    7. ship-ci         — generate / commit the GitHub Actions workflow.

Step completion is mostly computed from real data: rather than asking the
UI to "mark complete," we look at the DB (suites? runs? regressions? secrets?)
and infer. The :class:`TourState` JSON column on Project only persists
deliberate skips and the final "all done" flag.
"""

from .state import (
    DEFAULT_TOUR_STATE,
    STEP_DEFINITIONS,
    StepStatus,
    TourSnapshot,
    TourState,
    compute_tour,
    update_tour_state,
)

__all__ = [
    "TourState",
    "TourSnapshot",
    "StepStatus",
    "STEP_DEFINITIONS",
    "DEFAULT_TOUR_STATE",
    "compute_tour",
    "update_tour_state",
]

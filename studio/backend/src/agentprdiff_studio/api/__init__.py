"""HTTP API routers for Studio. Mounted under ``/api`` in ``main.py``."""

from .agents_md import router as agents_md_router
from .baseline_review import router as baseline_review_router
from .benchmark import router as benchmark_router
from .case_runs import router as case_runs_router
from .case_timeline import router as case_timeline_router
from .coverage import router as coverage_router
from .projects import router as projects_router
from .replay import router as replay_router
from .runs import router as runs_router
from .secrets import router as secrets_router
from .suite_health import router as suite_health_router
from .tour import router as tour_router

__all__ = [
    "projects_router",
    "runs_router",
    "secrets_router",
    "case_runs_router",
    "agents_md_router",
    "tour_router",
    # New routers
    "suite_health_router",
    "baseline_review_router",
    "case_timeline_router",
    "coverage_router",
    "benchmark_router",
    "replay_router",
]

"""Core data model for agentprdiff.

A `Suite` is a list of `Case`s. Each case, when run, produces a `Trace`. A
`Grader` is a callable `(Trace) -> GradeResult` that asserts something about
the trace. The test result for a case is the logical AND of every grader's
result.

Traces are serializable (JSON via pydantic) so they can be stored as baselines
and diffed across runs.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Trace model — what an agent run produces.
# ---------------------------------------------------------------------------


class LLMCall(BaseModel):
    """A single model invocation captured during an agent run."""

    provider: str
    model: str
    input_messages: list[dict[str, Any]] = Field(default_factory=list)
    output_text: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    timestamp: str = ""


class ToolCall(BaseModel):
    """A single tool / function invocation captured during an agent run."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    latency_ms: float = 0.0
    error: str | None = None


class Trace(BaseModel):
    """Full record of one agent run.

    A trace is what we record as a baseline and what we diff against on
    subsequent runs. Keep it JSON-serializable.
    """

    model_config = ConfigDict(extra="allow")

    case_name: str
    suite_name: str
    input: Any
    output: Any = None
    llm_calls: list[LLMCall] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    total_latency_ms: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # A stable id (per-run, not per-case) is handy for telemetry / ci logs.
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def record_llm_call(self, call: LLMCall) -> None:
        self.llm_calls.append(call)
        self.total_cost_usd += call.cost_usd
        self.total_latency_ms += call.latency_ms
        self.total_prompt_tokens += call.prompt_tokens
        self.total_completion_tokens += call.completion_tokens

    def record_tool_call(self, call: ToolCall) -> None:
        self.tool_calls.append(call)
        self.total_latency_ms += call.latency_ms


# ---------------------------------------------------------------------------
# Grader model — what's being asserted about a trace.
# ---------------------------------------------------------------------------


class GradeResult(BaseModel):
    """The outcome of a single grader on a single trace."""

    passed: bool
    grader_name: str
    # Optional stable identity for baseline matching. When set (via the
    # `id=` argument on grader factories), diffs match assertions by this id
    # instead of the display name, so renaming an argument or rewording a
    # rubric doesn't register as a removed + added assertion.
    grader_id: str | None = None
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


# A Grader is any callable (Trace) -> GradeResult. We keep it as a simple
# callable rather than an abstract class so users can pass lambdas.
Grader = Callable[[Trace], GradeResult]


# ---------------------------------------------------------------------------
# Case and Suite.
# ---------------------------------------------------------------------------


class Case(BaseModel):
    """One input + the assertions that must hold for the resulting trace."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    input: Any
    expect: list[Grader] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    # Minimum fraction of attempts that must fully pass when the runner
    # executes the case more than once (`check --runs N`). 1.0 = every
    # attempt must pass (the single-run behavior). E.g. 0.6 with --runs 3
    # tolerates one stochastic wobble out of three.
    min_pass_rate: float = Field(default=1.0, gt=0.0, le=1.0)


# An Agent is any callable `(input) -> (output, Trace)`. If the user's agent
# returns only an output, the runner wraps it so latency is captured but the
# returned `Trace` has empty llm_calls / tool_calls.
AgentFn = Callable[[Any], Any]


class Suite(BaseModel):
    """A named group of cases sharing one agent under test."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    agent: AgentFn
    cases: list[Case]
    description: str = ""


# ---------------------------------------------------------------------------
# Public constructors — keep them short and opinionated.
# ---------------------------------------------------------------------------


def suite(name: str, agent: AgentFn, cases: list[Case], description: str = "") -> Suite:
    """Create a Suite.

    >>> s = suite("billing", my_agent, cases=[case(...)])
    """
    return Suite(name=name, agent=agent, cases=cases, description=description)


def case(
    name: str,
    input: Any,
    expect: list[Grader],
    tags: list[str] | None = None,
    min_pass_rate: float = 1.0,
) -> Case:
    """Create a Case.

    `min_pass_rate` only matters with `check --runs N` (N > 1): the case
    passes when at least this fraction of attempts fully pass. Defaults to
    1.0 — every attempt must pass.

    >>> c = case("refund", input="I want a refund", expect=[contains("refund")])
    """
    return Case(
        name=name,
        input=input,
        expect=expect or [],
        tags=tags or [],
        min_pass_rate=min_pass_rate,
    )


# ---------------------------------------------------------------------------
# Small helper: run an agent callable and build a Trace.
# ---------------------------------------------------------------------------


def run_agent(
    agent: AgentFn,
    *,
    suite_name: str,
    case_name: str,
    input_value: Any,
) -> Trace:
    """Invoke an agent callable and build a Trace around its execution.

    If the agent returns a `(output, Trace)` tuple, we use the returned trace
    and just fill in the metadata we can see from out here (suite/case names).
    Otherwise we build a minimal trace with latency only.
    """
    start = time.perf_counter()
    trace: Trace
    try:
        result = agent(input_value)
    except Exception as exc:  # noqa: BLE001 — we want to capture any failure mode
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return Trace(
            suite_name=suite_name,
            case_name=case_name,
            input=input_value,
            output=None,
            error=f"{type(exc).__name__}: {exc}",
            total_latency_ms=elapsed_ms,
        )
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], Trace):
        output, trace = result
        trace.suite_name = suite_name
        trace.case_name = case_name
        trace.input = input_value
        trace.output = output
        # If the agent didn't track latency itself, fill in wall time as a
        # floor.
        if trace.total_latency_ms == 0.0:
            trace.total_latency_ms = elapsed_ms
    else:
        trace = Trace(
            suite_name=suite_name,
            case_name=case_name,
            input=input_value,
            output=result,
            total_latency_ms=elapsed_ms,
        )
    return trace

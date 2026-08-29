"""Semantic grader — LLM-as-judge.

The `semantic` grader accepts a rubric (natural language) and a `judge`
callable that returns a pass/fail verdict with a reason. We ship two built-in
judges:

* `fake_judge` — deterministic; used in tests and CI when you want a green
  pipeline without API keys. It passes iff any rubric keyword appears in the
  agent's output.
* `openai_judge(model=...)` and `anthropic_judge(model=...)` — thin wrappers
  over the respective SDKs. Imported lazily so agentprdiff has no required
  runtime dependency on either SDK.

Custom judges are encouraged. A judge is a callable:

    judge(rubric: str, trace: Trace) -> (passed: bool, reason: str)
"""

from __future__ import annotations

import os
import warnings
from collections.abc import Callable

from ..core import Grader, GradeResult, Trace

Judge = Callable[[str, Trace], tuple[bool, str]]

# Environment variable that selects the default judge. The legacy name
# (AGENTGUARD_JUDGE, from a pre-release name of this project) is still honored
# with a DeprecationWarning and will be removed in v1.0.
_ENV_JUDGE = "AGENTPRDIFF_JUDGE"
_ENV_JUDGE_LEGACY = "AGENTGUARD_JUDGE"

_warned_legacy_env = False


def _judge_env_choice() -> tuple[str, str]:
    """Return ``(choice, env_var_name)`` for the judge-selection env var.

    ``choice`` is the lowercased value ("" when neither var is set) and
    ``env_var_name`` is the variable it came from (the new name when unset,
    so callers can render accurate messages).
    """
    value = os.environ.get(_ENV_JUDGE)
    if value:
        return value.lower(), _ENV_JUDGE
    legacy = os.environ.get(_ENV_JUDGE_LEGACY)
    if legacy:
        global _warned_legacy_env
        if not _warned_legacy_env:
            _warned_legacy_env = True
            warnings.warn(
                f"{_ENV_JUDGE_LEGACY} is deprecated; use {_ENV_JUDGE} instead. "
                "The legacy name still works but will be removed in v1.0.",
                DeprecationWarning,
                stacklevel=3,
            )
        return legacy.lower(), _ENV_JUDGE_LEGACY
    return "", _ENV_JUDGE


def default_judge_is_silent_fallback() -> bool:
    """True when ``semantic()`` without an explicit ``judge=`` would fall back
    to :func:`fake_judge` *silently* — no judge env var set and no provider
    API key in the environment.

    This is the configuration ``agentprdiff check --strict-judge`` refuses to
    accept: a green build whose semantic assertions were graded by keyword
    matching is not a green build.
    """
    choice, _ = _judge_env_choice()
    return (
        not choice
        and not os.environ.get("OPENAI_API_KEY")
        and not os.environ.get("ANTHROPIC_API_KEY")
    )


# ---------------------------------------------------------------------------
# Public grader.
# ---------------------------------------------------------------------------


def semantic(rubric: str, *, judge: Judge | None = None, id: str | None = None) -> Grader:
    """Pass iff the `judge` says the trace satisfies the `rubric`.

    The rubric is natural language, e.g. "the agent acknowledged the refund
    and provided a ticket number".

    ``id`` optionally gives the assertion a stable identity for baseline
    matching, so rewording the rubric doesn't register as a removed + added
    assertion in diffs.
    """
    silent_fallback = judge is None and default_judge_is_silent_fallback()
    backend = judge or _default_judge()
    metadata: dict[str, object] = {"silent_fallback": True} if silent_fallback else {}

    def _grader(trace: Trace) -> GradeResult:
        try:
            passed, reason = backend(rubric, trace)
        except Exception as exc:  # noqa: BLE001
            return GradeResult(
                passed=False,
                grader_name=f"semantic({rubric!r})",
                grader_id=id,
                reason=f"judge raised {type(exc).__name__}: {exc}",
                metadata=dict(metadata),
            )
        return GradeResult(
            passed=passed,
            grader_name=f"semantic({rubric!r})",
            grader_id=id,
            reason=reason,
            metadata=dict(metadata),
        )

    return _grader


# ---------------------------------------------------------------------------
# Built-in judges.
# ---------------------------------------------------------------------------


def fake_judge(rubric: str, trace: Trace) -> tuple[bool, str]:
    """Keyword-match judge for tests. Passes iff ANY rubric word (>= 4 chars)
    appears in the agent output, case-insensitive.

    Deterministic, free, and good enough for demos and CI smoke tests. Do
    not use in production eval pipelines.
    """
    output = str(trace.output or "").lower()
    keywords = [w for w in _tokenize(rubric) if len(w) >= 4]
    matched = [w for w in keywords if w in output]
    passed = bool(matched)
    reason = (
        f"fake_judge matched keywords {matched}" if passed else "fake_judge matched no keywords"
    )
    return passed, reason


def _tokenize(text: str) -> list[str]:
    import re

    return re.findall(r"[a-zA-Z]+", text.lower())


_JUDGE_PROMPT = (
    "You are an evaluator. Given a RUBRIC and an agent's OUTPUT, decide "
    "whether the output satisfies the rubric.\n\n"
    "Respond with the single word PASS or FAIL on the first line, and a "
    "one-sentence reason on the second line. Be strict: if the rubric is "
    "not clearly satisfied, answer FAIL.\n\n"
    "RUBRIC:\n{rubric}\n\n"
    "OUTPUT:\n{output}\n"
)


def openai_judge(model: str = "gpt-4o-mini", api_key: str | None = None) -> Judge:
    """Return a judge backed by the OpenAI Chat Completions API.

    Requires the `openai` package (install with `pip install agentprdiff[openai]`).
    """

    def _judge(rubric: str, trace: Trace) -> tuple[bool, str]:
        from openai import OpenAI  # lazy import

        client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
        prompt = _JUDGE_PROMPT.format(rubric=rubric, output=str(trace.output or ""))
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        text = resp.choices[0].message.content or ""
        return _parse_verdict(text)

    return _judge


def anthropic_judge(model: str = "claude-haiku-4-5-20251001", api_key: str | None = None) -> Judge:
    """Return a judge backed by the Anthropic Messages API.

    Requires the `anthropic` package (install with `pip install agentprdiff[anthropic]`).
    """

    def _judge(rubric: str, trace: Trace) -> tuple[bool, str]:
        import anthropic  # lazy import

        client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        prompt = _JUDGE_PROMPT.format(rubric=rubric, output=str(trace.output or ""))
        resp = client.messages.create(
            model=model,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        return _parse_verdict(text)

    return _judge


def _parse_verdict(text: str) -> tuple[bool, str]:
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    if not lines:
        return False, "judge returned empty response"
    verdict = lines[0].upper()
    reason = lines[1] if len(lines) > 1 else ""
    if verdict.startswith("PASS"):
        return True, reason or "judge said PASS"
    if verdict.startswith("FAIL"):
        return False, reason or "judge said FAIL"
    # Be strict: anything other than an explicit PASS is a FAIL.
    return False, f"judge returned unparseable verdict: {lines[0]!r}"


# ---------------------------------------------------------------------------
# Backend selection.
# ---------------------------------------------------------------------------


def _default_judge() -> Judge:
    """Pick a default judge based on environment.

    Order of preference:
    * AGENTPRDIFF_JUDGE=fake -> fake_judge
    * AGENTPRDIFF_JUDGE=openai or OPENAI_API_KEY set -> openai_judge()
    * AGENTPRDIFF_JUDGE=anthropic or ANTHROPIC_API_KEY set -> anthropic_judge()
    * otherwise -> fake_judge (so pipelines stay green in CI without keys —
      surfaced by the judge banner, and refused by `check --strict-judge`)

    The legacy AGENTGUARD_JUDGE name is honored with a DeprecationWarning.
    """
    choice, _ = _judge_env_choice()
    if choice == "fake":
        return fake_judge
    if choice == "openai" or (not choice and os.environ.get("OPENAI_API_KEY")):
        return openai_judge()
    if choice == "anthropic" or (not choice and os.environ.get("ANTHROPIC_API_KEY")):
        return anthropic_judge()
    return fake_judge


# Default model names mirrored from `openai_judge` and `anthropic_judge`. Kept
# in module scope so `describe_default_judge` reports the same string the
# default judge would actually use without instantiating the judge (which
# would import the SDK lazily).
_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
_DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"


def describe_default_judge() -> str:
    """Return a one-line description of the currently-selected default judge.

    Mirrors the env-var precedence in :func:`_default_judge` so the string is
    a faithful preview of what `semantic(...)` graders without an explicit
    `judge=` argument will use at runtime. Reporters print this once per run
    so the silent fake_judge fallback (no key set, no AGENTGUARD_JUDGE) is
    visible at the moment a suite executes — not buried in trace JSON.

    Examples:
        - ``"fake_judge (AGENTPRDIFF_JUDGE=fake)"``
        - ``"openai/gpt-4o-mini (OPENAI_API_KEY set)"``
        - ``"anthropic/claude-haiku-4-5-20251001 (AGENTPRDIFF_JUDGE=anthropic)"``
        - ``"fake_judge (no AGENTPRDIFF_JUDGE, no OPENAI_API_KEY/ANTHROPIC_API_KEY — silent fallback)"``

    When the legacy AGENTGUARD_JUDGE variable is what's set, the description
    names it, so the banner tells the truth about where the choice came from.
    """
    choice, env_var = _judge_env_choice()
    if choice == "fake":
        return f"fake_judge ({env_var}=fake)"
    if choice == "openai":
        return f"openai/{_DEFAULT_OPENAI_MODEL} ({env_var}=openai)"
    if choice == "anthropic":
        return (
            f"anthropic/{_DEFAULT_ANTHROPIC_MODEL} ({env_var}=anthropic)"
        )
    if not choice and os.environ.get("OPENAI_API_KEY"):
        return f"openai/{_DEFAULT_OPENAI_MODEL} (OPENAI_API_KEY set)"
    if not choice and os.environ.get("ANTHROPIC_API_KEY"):
        return f"anthropic/{_DEFAULT_ANTHROPIC_MODEL} (ANTHROPIC_API_KEY set)"
    return (
        "fake_judge (no AGENTPRDIFF_JUDGE, no OPENAI_API_KEY/ANTHROPIC_API_KEY"
        " — silent fallback)"
    )


def case_uses_semantic(grader_results: list[GradeResult]) -> bool:
    """Return True if any grader in `grader_results` was a `semantic()` grader.

    Detection is based on the grader name format produced by :func:`semantic`
    (``semantic(<rubric>)``). This is stable because the public grader name is
    part of the user-visible grading contract; reporters and serializers
    already depend on it.
    """
    return any(r.grader_name.startswith("semantic(") for r in grader_results)

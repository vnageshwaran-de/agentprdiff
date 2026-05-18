"""Replay sandbox — seed loader + replay stub.

GET  /api/case-runs/{case_run_id}/replay-seed
  → { case_name, suite_name, input, output, latency_ms, cost_usd,
      graders, messages }
  Builds a sandbox seed from the case_run's recorded trace + the case's
  expect list. Used by the ReplaySandbox page to populate its editors.

POST /api/case-runs/{case_run_id}/replay
  → 501 until the engine exposes a replay-from-step adapter hook
  (RecordingAdapter / set_default_adapter). The frontend's live grader
  preview (contains / regex_match / latency_lt_ms / cost_lt_usd) works
  entirely client-side and doesn't depend on this endpoint.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import models
from ..db.session import get_session

router = APIRouter(prefix="/api/case-runs", tags=["replay"])


def _stringify_output(out: Any) -> str:
    if out is None:
        return ""
    if isinstance(out, str):
        return out
    try:
        return json.dumps(out, indent=2, default=str)
    except (TypeError, ValueError):
        return str(out)


def _extract_graders(suite: models.Suite, case_name: str) -> list[str]:
    """Grab the case's grader strings from the suite definition.

    For HTTP-mode suites the definition_json carries the cases inline. For
    git/zip suites the case definitions live in Python source we don't parse
    here — return an empty list and let the user paste graders manually in
    the sandbox (or paste them from the suite source).
    """
    if not suite.definition_json:
        return []
    cases = (suite.definition_json or {}).get("cases", [])
    for c in cases:
        if (c or {}).get("name") == case_name:
            expect = c.get("expect") or []
            out: list[str] = []
            for item in expect:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    # The HTTP-mode "expect" entries are objects like
                    # {"tool_called": {"name": "foo"}}; render them as a
                    # readable Python-ish string the sandbox understands.
                    if "tool_called" in item:
                        name = (item["tool_called"] or {}).get("name", "")
                        out.append(f'tool_called({name!r})')
                    elif "contains" in item:
                        val = item["contains"]
                        out.append(f'contains({val!r})')
                    elif "regex_match" in item:
                        out.append(f'regex_match({item["regex_match"]!r})')
                    elif "latency_lt_ms" in item:
                        out.append(f"latency_lt_ms({item['latency_lt_ms']})")
                    elif "cost_lt_usd" in item:
                        out.append(f"cost_lt_usd({item['cost_lt_usd']})")
                    elif "semantic" in item:
                        out.append(f'semantic({item["semantic"]!r})')
                    else:
                        # Unknown shape; serialize verbatim
                        out.append(json.dumps(item))
            return out
    return []


@router.get("/{case_run_id}/replay-seed")
async def replay_seed(
    case_run_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    case_run = await session.get(models.CaseRun, case_run_id)
    if case_run is None:
        raise HTTPException(status_code=404, detail="case run not found")
    run = await session.get(models.Run, case_run.run_id)
    suite = await session.get(models.Suite, run.suite_id) if run else None
    if suite is None:
        raise HTTPException(status_code=404, detail="parent suite missing")

    trace: dict[str, Any] = case_run.trace_json or {}
    return {
        "case_name": case_run.case_name,
        "suite_name": suite.name,
        "input": trace.get("input"),
        "output": _stringify_output(trace.get("output")),
        "latency_ms": float(case_run.latency_ms or 0),
        "cost_usd": float(case_run.cost_usd or 0),
        "graders": _extract_graders(suite, case_run.case_name),
        "messages": [],
    }


class ReplayRequest(BaseModel):
    output: str
    latency_ms: float
    cost_usd: float


@router.post("/{case_run_id}/replay")
async def replay(
    case_run_id: int,
    body: ReplayRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    # Live grader preview already works fully in the browser for pure-function
    # graders (contains / regex_match / latency_lt_ms / cost_lt_usd). True
    # replay-from-step requires the engine's RecordingAdapter +
    # set_default_adapter() hook — not yet wired.
    raise HTTPException(
        status_code=501,
        detail=(
            "Replay-from-step requires the engine's RecordingAdapter hook "
            "(not yet wired). Live grader preview already works in the "
            "browser for contains / regex_match / latency_lt_ms / "
            "cost_lt_usd graders."
        ),
    )

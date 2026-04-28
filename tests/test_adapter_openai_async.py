"""Tests for the async path of the OpenAI adapter (AsyncOpenAI clients).

We mirror the sync test file's strategy: a hand-rolled fake client whose
``client.chat.completions.create`` is ``async def``, so the adapter's
``asyncio.iscoroutinefunction`` branch fires and the awaitable patched
``create`` gets installed. Tests run with ``asyncio.run(...)`` inside each
case so we don't take a pytest-asyncio dev dep just for this suite.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from agentprdiff import LLMCall, Trace
from agentprdiff.adapters.openai import instrument_client, instrument_tools

# ───────────────────────── fake AsyncOpenAI client ─────────────────────────


class _FakeUsage(SimpleNamespace):
    pass


class _FakeFunction(SimpleNamespace):
    pass


class _FakeToolCall(SimpleNamespace):
    pass


class _FakeMessage(SimpleNamespace):
    pass


class _FakeChoice(SimpleNamespace):
    pass


class _FakeResponse(SimpleNamespace):
    pass


class _FakeAsyncCompletions:
    """Mimics ``AsyncOpenAI().chat.completions``.

    The ``create`` method must be an actual coroutine function so the adapter's
    ``asyncio.iscoroutinefunction`` check returns True and installs the async
    patched create. ``await asyncio.sleep(0)`` is enough to make the body do
    real coroutine work, so ``time.perf_counter`` measures non-zero elapsed.
    """

    def __init__(self, scripted: list[Any]) -> None:
        self._scripted = list(scripted)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        await asyncio.sleep(0)
        if not self._scripted:
            raise RuntimeError("fake async client ran out of scripted responses")
        result = self._scripted.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeAsyncChat:
    def __init__(self, completions: _FakeAsyncCompletions) -> None:
        self.completions = completions


class _FakeAsyncOpenAIClient:
    def __init__(
        self,
        scripted: list[Any],
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.completions = _FakeAsyncCompletions(scripted)
        self.chat = _FakeAsyncChat(self.completions)
        self.base_url = base_url


def _build_response(
    *,
    model: str = "gpt-4o-mini",
    text: str = "hello",
    tool_calls: list[tuple[str, str, str]] | None = None,
    prompt_tokens: int = 50,
    completion_tokens: int = 10,
) -> _FakeResponse:
    tcs = []
    for tc_id, name, args in tool_calls or []:
        tcs.append(_FakeToolCall(id=tc_id, function=_FakeFunction(name=name, arguments=args)))
    message = _FakeMessage(content=text, tool_calls=tcs or None)
    choice = _FakeChoice(message=message)
    usage = _FakeUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return _FakeResponse(model=model, choices=[choice], usage=usage)


# ─────────────────────── async create ─────────────────────────────────────


def test_async_records_one_llmcall_per_create_invocation() -> None:
    client = _FakeAsyncOpenAIClient(scripted=[_build_response(text="hi async")])

    async def _run() -> Trace:
        with instrument_client(client) as trace:
            await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "hello"}],
            )
        return trace

    trace = asyncio.run(_run())

    assert len(trace.llm_calls) == 1
    call: LLMCall = trace.llm_calls[0]
    assert call.provider == "openai"
    assert call.model == "gpt-4o-mini"
    assert call.output_text == "hi async"
    assert call.prompt_tokens == 50
    assert call.completion_tokens == 10
    # gpt-4o-mini cost — same math as the sync test, but reached through await.
    assert call.cost_usd == pytest.approx(50 * 0.00015 / 1000 + 10 * 0.00060 / 1000)
    assert call.latency_ms >= 0.0


def test_async_extracts_tool_calls_from_response() -> None:
    client = _FakeAsyncOpenAIClient(
        scripted=[
            _build_response(
                text="",
                tool_calls=[
                    ("call_1", "lookup_order", '{"id": "1234"}'),
                    ("call_2", "send_email", '{"to": "x"}'),
                ],
            )
        ]
    )

    async def _run() -> Trace:
        with instrument_client(client) as trace:
            await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "do the thing"}],
            )
        return trace

    trace = asyncio.run(_run())

    summary = trace.llm_calls[0].tool_calls
    assert [tc["name"] for tc in summary] == ["lookup_order", "send_email"]
    assert summary[0]["arguments"] == '{"id": "1234"}'


def test_async_patch_is_restored_on_exit() -> None:
    """After the with block, calling create returns the bare scripted response
    and does NOT record onto trace."""
    client = _FakeAsyncOpenAIClient(scripted=[_build_response(), _build_response()])

    async def _run() -> Trace:
        with instrument_client(client) as trace:
            await client.chat.completions.create(model="gpt-4o-mini", messages=[])
        # Outside the context manager — should hit the bare async create.
        await client.chat.completions.create(model="gpt-4o-mini", messages=[])
        return trace

    trace = asyncio.run(_run())

    assert len(trace.llm_calls) == 1
    assert len(client.completions.calls) == 2


def test_async_patch_is_restored_even_when_agent_raises() -> None:
    client = _FakeAsyncOpenAIClient(scripted=[_build_response()])

    async def _run() -> None:
        with instrument_client(client):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(_run())

    # After the failed with block, the patch should be restored — call through.
    async def _post() -> None:
        await client.chat.completions.create(model="gpt-4o-mini", messages=[])

    asyncio.run(_post())
    assert len(client.completions.calls) == 1


def test_async_records_failed_call_when_create_raises() -> None:
    err = RuntimeError("upstream 500")
    client = _FakeAsyncOpenAIClient(scripted=[err])

    async def _run() -> Trace:
        with instrument_client(client) as trace, pytest.raises(
            RuntimeError, match="upstream 500"
        ):
            await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "x"}],
            )
        return trace

    trace = asyncio.run(_run())

    assert len(trace.llm_calls) == 1
    assert trace.llm_calls[0].output_text.startswith("<exception:")
    assert trace.llm_calls[0].latency_ms >= 0.0


def test_async_uses_externally_supplied_trace() -> None:
    """An async agent that builds its own Trace and passes it into the adapter
    should see the same Trace come back, with llm_calls appended."""
    client = _FakeAsyncOpenAIClient(scripted=[_build_response()])
    existing = Trace(suite_name="s", case_name="c", input="x")

    async def _run() -> None:
        with instrument_client(client, trace=existing) as trace:
            assert trace is existing
            await client.chat.completions.create(model="gpt-4o-mini", messages=[])

    asyncio.run(_run())
    assert len(existing.llm_calls) == 1


def test_async_adapter_works_when_client_create_is_async_method() -> None:
    """Sanity check: confirm asyncio.iscoroutinefunction sees the bound method
    as a coroutine function. Guards against a refactor that breaks detection."""
    client = _FakeAsyncOpenAIClient(scripted=[])
    assert asyncio.iscoroutinefunction(client.chat.completions.create)


# ─────────────────────── async tools ──────────────────────────────────────


def test_instrument_tools_wraps_async_tool_as_awaitable() -> None:
    trace = Trace(suite_name="", case_name="", input="x")

    async def lookup_order_async(order_id: str) -> dict:
        await asyncio.sleep(0)
        return {"id": order_id, "status": "ok"}

    tools = instrument_tools({"lookup_order": lookup_order_async}, trace)

    async def _run() -> dict:
        # The wrapper must itself be awaitable.
        return await tools["lookup_order"](order_id="1234")

    result = asyncio.run(_run())
    assert result == {"id": "1234", "status": "ok"}
    assert len(trace.tool_calls) == 1
    tc = trace.tool_calls[0]
    assert tc.name == "lookup_order"
    assert tc.arguments == {"order_id": "1234"}
    assert tc.result == {"id": "1234", "status": "ok"}
    assert tc.latency_ms >= 0.0


def test_instrument_tools_async_records_errors_and_reraises() -> None:
    trace = Trace(suite_name="", case_name="", input="x")

    async def broken_async(arg: str) -> None:
        await asyncio.sleep(0)
        raise ValueError(f"bad arg: {arg}")

    tools = instrument_tools({"broken": broken_async}, trace)

    async def _run() -> None:
        await tools["broken"](arg="hi")

    with pytest.raises(ValueError, match="bad arg: hi"):
        asyncio.run(_run())

    assert len(trace.tool_calls) == 1
    tc = trace.tool_calls[0]
    assert tc.name == "broken"
    assert tc.error is not None and "ValueError" in tc.error
    assert tc.result is None


def test_instrument_tools_mixes_sync_and_async_in_one_map() -> None:
    """An agent with one async DB call and one sync local helper should be
    able to put both in TOOL_MAP and have each wrapped appropriately."""
    trace = Trace(suite_name="", case_name="", input="x")

    async def fetch_remote(key: str) -> dict:
        await asyncio.sleep(0)
        return {"key": key, "v": 1}

    def local_compute(value: int) -> int:
        return value * 2

    tools = instrument_tools(
        {"fetch_remote": fetch_remote, "local_compute": local_compute}, trace
    )

    async def _run() -> tuple[dict, int]:
        a = await tools["fetch_remote"](key="alpha")
        b = tools["local_compute"](value=21)
        return a, b

    a, b = asyncio.run(_run())
    assert a == {"key": "alpha", "v": 1}
    assert b == 42
    assert [tc.name for tc in trace.tool_calls] == ["fetch_remote", "local_compute"]


# ─────────────────────── sync regression check ─────────────────────────────


def test_sync_path_still_works_after_refactor() -> None:
    """The sync code-path was refactored to share helpers with the async path.
    Smoke-test it in this file too so a future async refactor can't quietly
    break sync without showing up in the same test run."""

    class _SyncCompletions:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **kwargs: Any) -> Any:
            self.calls += 1
            return _build_response(text="sync-ok")

    class _SyncClient:
        def __init__(self) -> None:
            self.completions = _SyncCompletions()
            self.chat = SimpleNamespace(completions=self.completions)
            self.base_url = "https://api.openai.com/v1"

    c = _SyncClient()
    with instrument_client(c) as trace:
        c.chat.completions.create(model="gpt-4o-mini", messages=[])

    assert len(trace.llm_calls) == 1
    assert trace.llm_calls[0].output_text == "sync-ok"
    assert trace.llm_calls[0].provider == "openai"

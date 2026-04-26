"""Tests for the OpenAI / OpenAI-compatible adapter.

These tests use a hand-rolled fake client that mirrors the openai-python
client API surface we patch — we don't require the real ``openai`` package to
be installed. That keeps the test suite fast and self-contained, and is the
same shape any OpenAI-compatible SDK would expose.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agentprdiff import LLMCall, Trace
from agentprdiff.adapters.openai import (
    _infer_provider_from_client,
    instrument_client,
    instrument_tools,
)

# ───────────────────────── fake openai client ──────────────────────────────


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


class _FakeCompletions:
    """Mimics ``client.chat.completions``."""

    def __init__(self, scripted: list[Any]) -> None:
        self._scripted = list(scripted)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._scripted:
            raise RuntimeError("fake client ran out of scripted responses")
        result = self._scripted.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeOpenAIClient:
    def __init__(self, scripted: list[Any], base_url: str = "https://api.openai.com/v1") -> None:
        self.completions = _FakeCompletions(scripted)
        self.chat = _FakeChat(self.completions)
        self.base_url = base_url


def _build_response(
    *,
    model: str = "gpt-4o-mini",
    text: str = "hello",
    tool_calls: list[tuple[str, str, str]] | None = None,
    prompt_tokens: int = 50,
    completion_tokens: int = 10,
) -> _FakeResponse:
    """Construct a fake ChatCompletion response.

    `tool_calls` is a list of (id, name, arguments_json_string) tuples — the
    same shape the OpenAI SDK exposes via ``message.tool_calls[i].function.*``.
    """
    tcs = []
    for tc_id, name, args in tool_calls or []:
        tcs.append(_FakeToolCall(id=tc_id, function=_FakeFunction(name=name, arguments=args)))
    message = _FakeMessage(content=text, tool_calls=tcs or None)
    choice = _FakeChoice(message=message)
    usage = _FakeUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return _FakeResponse(model=model, choices=[choice], usage=usage)


# ─────────────────────────── tests ─────────────────────────────────────────


def test_records_one_llmcall_per_create_invocation() -> None:
    client = _FakeOpenAIClient(scripted=[_build_response(text="hi there")])

    with instrument_client(client) as trace:
        client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "hello"}],
        )

    assert len(trace.llm_calls) == 1
    call: LLMCall = trace.llm_calls[0]
    assert call.provider == "openai"
    assert call.model == "gpt-4o-mini"
    assert call.output_text == "hi there"
    assert call.prompt_tokens == 50
    assert call.completion_tokens == 10
    # gpt-4o-mini costs (0.00015, 0.00060) per 1k tokens.
    assert call.cost_usd == pytest.approx(50 * 0.00015 / 1000 + 10 * 0.00060 / 1000)
    assert call.latency_ms >= 0.0


def test_records_tool_calls_extracted_from_response() -> None:
    client = _FakeOpenAIClient(
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

    with instrument_client(client) as trace:
        client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "do the thing"}],
        )

    assert len(trace.llm_calls) == 1
    summary = trace.llm_calls[0].tool_calls
    assert [tc["name"] for tc in summary] == ["lookup_order", "send_email"]
    assert summary[0]["arguments"] == '{"id": "1234"}'


def test_patch_is_restored_on_exit() -> None:
    """After the context exits, the create method must call straight through
    to the original — i.e., no Trace recording should happen."""
    client = _FakeOpenAIClient(scripted=[_build_response(), _build_response()])

    with instrument_client(client) as trace:
        client.chat.completions.create(model="gpt-4o-mini", messages=[])
    assert len(trace.llm_calls) == 1

    # Outside the context: should hit the bare _FakeCompletions.create with no
    # side effects on `trace`.
    client.chat.completions.create(model="gpt-4o-mini", messages=[])
    assert len(trace.llm_calls) == 1
    assert len(client.completions.calls) == 2


def test_patch_is_restored_even_when_agent_raises() -> None:
    client = _FakeOpenAIClient(scripted=[_build_response()])

    with pytest.raises(RuntimeError, match="boom"), instrument_client(client):
        raise RuntimeError("boom")

    # Restored: a subsequent call hits the underlying client uninstrumented.
    client.chat.completions.create(model="gpt-4o-mini", messages=[])
    assert len(client.completions.calls) == 1


def test_records_failed_call_when_create_raises() -> None:
    err = RuntimeError("upstream 500")
    client = _FakeOpenAIClient(scripted=[err])

    with instrument_client(client) as trace, pytest.raises(RuntimeError, match="upstream 500"):
        client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "x"}],
        )

    assert len(trace.llm_calls) == 1
    assert trace.llm_calls[0].output_text.startswith("<exception:")
    assert trace.llm_calls[0].latency_ms >= 0.0


def test_uses_externally_supplied_trace() -> None:
    client = _FakeOpenAIClient(scripted=[_build_response()])
    existing = Trace(suite_name="s", case_name="c", input="x")

    with instrument_client(client, trace=existing) as trace:
        assert trace is existing
        client.chat.completions.create(model="gpt-4o-mini", messages=[])

    assert len(existing.llm_calls) == 1


def test_unknown_model_records_zero_cost_and_warns() -> None:
    client = _FakeOpenAIClient(scripted=[_build_response(model="brand-new-frontier-x")])

    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with instrument_client(client) as trace:
            client.chat.completions.create(model="brand-new-frontier-x", messages=[])

    assert trace.llm_calls[0].cost_usd == 0.0
    assert any("no pricing entry" in str(w.message) for w in caught)


def test_per_call_price_override() -> None:
    client = _FakeOpenAIClient(scripted=[_build_response(model="custom-model")])

    with instrument_client(client, prices={"custom-model": (1.0, 2.0)}) as trace:
        client.chat.completions.create(model="custom-model", messages=[])

    expected = 50 * 1.0 / 1000 + 10 * 2.0 / 1000
    assert trace.llm_calls[0].cost_usd == pytest.approx(expected)


def test_provider_inference_from_base_url() -> None:
    cases = [
        ("https://api.openai.com/v1", "openai"),
        ("https://api.groq.com/openai/v1", "groq"),
        ("https://openrouter.ai/api/v1", "openrouter"),
        ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini"),
        ("http://localhost:11434/v1", "ollama"),
        ("https://mystery.example.com/v1", "openai-compatible"),
    ]
    for url, expected in cases:
        c = _FakeOpenAIClient(scripted=[], base_url=url)
        assert _infer_provider_from_client(c) == expected, url


def test_explicit_provider_overrides_inference() -> None:
    client = _FakeOpenAIClient(scripted=[_build_response()], base_url="https://api.openai.com")

    with instrument_client(client, provider="my-private-fork") as trace:
        client.chat.completions.create(model="gpt-4o-mini", messages=[])

    assert trace.llm_calls[0].provider == "my-private-fork"


def test_rejects_non_openai_shaped_client() -> None:
    class WrongShape:
        pass

    with pytest.raises(TypeError, match="OpenAI-style client"), instrument_client(WrongShape()):
        pass


# ─────────────────────────── instrument_tools ──────────────────────────────


def test_instrument_tools_records_each_call() -> None:
    trace = Trace(suite_name="", case_name="", input="x")

    def lookup_order(order_id: str) -> dict:
        return {"id": order_id, "status": "ok"}

    def send_email(to: str, subject: str) -> str:
        return f"sent to {to}"

    tools = instrument_tools({"lookup_order": lookup_order, "send_email": send_email}, trace)

    r1 = tools["lookup_order"](order_id="1234")
    r2 = tools["send_email"](to="x@y.com", subject="hi")

    assert r1 == {"id": "1234", "status": "ok"}
    assert r2 == "sent to x@y.com"

    assert [tc.name for tc in trace.tool_calls] == ["lookup_order", "send_email"]
    assert trace.tool_calls[0].arguments == {"order_id": "1234"}
    assert trace.tool_calls[0].result == {"id": "1234", "status": "ok"}
    assert trace.tool_calls[1].arguments == {"to": "x@y.com", "subject": "hi"}
    assert all(tc.latency_ms >= 0.0 for tc in trace.tool_calls)


def test_instrument_tools_records_errors_and_reraises() -> None:
    trace = Trace(suite_name="", case_name="", input="x")

    def broken(arg: str) -> None:
        raise ValueError(f"bad arg: {arg}")

    tools = instrument_tools({"broken": broken}, trace)

    with pytest.raises(ValueError, match="bad arg: hi"):
        tools["broken"](arg="hi")

    assert len(trace.tool_calls) == 1
    tc = trace.tool_calls[0]
    assert tc.name == "broken"
    assert tc.error is not None and "ValueError" in tc.error
    assert tc.result is None


def test_instrument_tools_returns_a_new_dict_doesnt_mutate_input() -> None:
    trace = Trace(suite_name="", case_name="", input="x")
    original = {"f": lambda: 1}
    wrapped = instrument_tools(original, trace)
    assert wrapped is not original
    assert original["f"]() == 1  # not wrapped

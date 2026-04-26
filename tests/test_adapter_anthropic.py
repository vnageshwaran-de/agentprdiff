"""Tests for the Anthropic Messages API adapter.

Like ``test_adapter_openai.py``, we use a hand-rolled fake client so the test
suite has no real SDK dependency.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agentprdiff import Trace
from agentprdiff.adapters.anthropic import (
    _extract_anthropic_blocks,
    instrument_client,
    instrument_tools,
)

# ───────────────────────── fake anthropic client ───────────────────────────


class _FakeUsage(SimpleNamespace):
    pass


class _FakeBlock(SimpleNamespace):
    pass


class _FakeResponse(SimpleNamespace):
    pass


class _FakeMessages:
    def __init__(self, scripted: list[Any]) -> None:
        self._scripted = list(scripted)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._scripted:
            raise RuntimeError("fake anthropic client out of responses")
        result = self._scripted.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeAnthropicClient:
    def __init__(self, scripted: list[Any]) -> None:
        self.messages = _FakeMessages(scripted)


def _build_response(
    *,
    model: str = "claude-sonnet-4-6",
    text: str = "hello",
    tool_uses: list[tuple[str, str, dict]] | None = None,
    input_tokens: int = 80,
    output_tokens: int = 20,
) -> _FakeResponse:
    blocks: list[_FakeBlock] = []
    if text:
        blocks.append(_FakeBlock(type="text", text=text))
    for tu_id, name, inputs in tool_uses or []:
        blocks.append(_FakeBlock(type="tool_use", id=tu_id, name=name, input=inputs))
    usage = _FakeUsage(input_tokens=input_tokens, output_tokens=output_tokens)
    return _FakeResponse(model=model, content=blocks, usage=usage)


# ─────────────────────────── tests ─────────────────────────────────────────


def test_records_one_llmcall_per_create_invocation() -> None:
    client = _FakeAnthropicClient(scripted=[_build_response(text="hi there")])

    with instrument_client(client) as trace:
        client.messages.create(
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hello"}],
        )

    assert len(trace.llm_calls) == 1
    call = trace.llm_calls[0]
    assert call.provider == "anthropic"
    assert call.model == "claude-sonnet-4-6"
    assert call.output_text == "hi there"
    assert call.prompt_tokens == 80
    assert call.completion_tokens == 20
    # claude-sonnet-4-6: (0.0030, 0.0150) per 1k tokens.
    assert call.cost_usd == pytest.approx(80 * 0.0030 / 1000 + 20 * 0.0150 / 1000)


def test_extracts_tool_uses_from_content_blocks() -> None:
    client = _FakeAnthropicClient(
        scripted=[
            _build_response(
                text="I'll look that up.",
                tool_uses=[
                    ("toolu_1", "lookup_order", {"id": "1234"}),
                    ("toolu_2", "send_email", {"to": "a@b"}),
                ],
            )
        ]
    )

    with instrument_client(client) as trace:
        client.messages.create(model="claude-sonnet-4-6", messages=[])

    summary = trace.llm_calls[0].tool_calls
    assert [t["name"] for t in summary] == ["lookup_order", "send_email"]
    assert summary[0]["arguments"] == {"id": "1234"}
    # Output text is the concatenation of text blocks only.
    assert trace.llm_calls[0].output_text == "I'll look that up."


def test_extract_blocks_handles_dict_blocks() -> None:
    """Some test scaffolds and the anthropic-python streaming helpers expose
    blocks as plain dicts; the extractor should handle either shape."""
    blocks = [
        {"type": "text", "text": "ok"},
        {"type": "tool_use", "id": "toolu_x", "name": "ping", "input": {"q": 1}},
        {"type": "thinking", "thinking": "..."},  # ignored
    ]
    text, tool_calls = _extract_anthropic_blocks(blocks)
    assert text == "ok"
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "ping"
    assert tool_calls[0]["arguments"] == {"q": 1}


def test_patch_is_restored_on_exit() -> None:
    """Same behavioral test as the OpenAI adapter — after exit, calls must
    pass through cleanly without recording onto the Trace."""
    client = _FakeAnthropicClient(scripted=[_build_response(), _build_response()])

    with instrument_client(client) as trace:
        client.messages.create(model="claude-sonnet-4-6", messages=[])
    assert len(trace.llm_calls) == 1

    client.messages.create(model="claude-sonnet-4-6", messages=[])
    assert len(trace.llm_calls) == 1
    assert len(client.messages.calls) == 2


def test_patch_restored_even_when_agent_raises() -> None:
    client = _FakeAnthropicClient(scripted=[_build_response()])

    with pytest.raises(RuntimeError, match="boom"), instrument_client(client):
        raise RuntimeError("boom")

    client.messages.create(model="claude-sonnet-4-6", messages=[])
    assert len(client.messages.calls) == 1


def test_records_failed_call_when_create_raises() -> None:
    client = _FakeAnthropicClient(scripted=[RuntimeError("rate-limited")])

    with instrument_client(client) as trace, pytest.raises(RuntimeError, match="rate-limited"):
        client.messages.create(model="claude-sonnet-4-6", messages=[])

    assert trace.llm_calls[0].output_text.startswith("<exception:")


def test_rejects_non_anthropic_shaped_client() -> None:
    class WrongShape:
        pass

    with pytest.raises(TypeError, match="Anthropic client"), instrument_client(WrongShape()):
        pass


def test_instrument_tools_works_for_anthropic_adapter() -> None:
    """The tool wrapper is shared between adapters; sanity check that it's
    re-exported from the anthropic module."""
    trace = Trace(suite_name="", case_name="", input="x")

    def lookup(id: str) -> dict:
        return {"id": id}

    tools = instrument_tools({"lookup": lookup}, trace)
    assert tools["lookup"](id="42") == {"id": "42"}
    assert len(trace.tool_calls) == 1
    assert trace.tool_calls[0].name == "lookup"
    assert trace.tool_calls[0].arguments == {"id": "42"}


def test_unknown_model_warns_and_records_zero_cost() -> None:
    client = _FakeAnthropicClient(scripted=[_build_response(model="claude-future-1")])

    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with instrument_client(client) as trace:
            client.messages.create(model="claude-future-1", messages=[])

    assert trace.llm_calls[0].cost_usd == 0.0
    assert any("no pricing entry" in str(w.message) for w in caught)


def test_explicit_provider_label() -> None:
    client = _FakeAnthropicClient(scripted=[_build_response()])
    with instrument_client(client, provider="anthropic-bedrock") as trace:
        client.messages.create(model="claude-sonnet-4-6", messages=[])
    assert trace.llm_calls[0].provider == "anthropic-bedrock"

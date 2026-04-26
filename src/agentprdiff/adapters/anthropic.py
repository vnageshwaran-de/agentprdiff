"""Anthropic Messages API adapter.

The Messages API has a different shape from OpenAI's Chat Completions:

* Output is a list of content blocks (``text``, ``tool_use``, ``thinking``,
  ...) on ``response.content``, not ``response.choices[0].message.content``.
* Token usage is ``response.usage.input_tokens`` / ``output_tokens``.
* Tool calls live as ``tool_use`` blocks inside ``response.content``, each with
  an ``id``, ``name``, and ``input`` dict.

The user's loop pattern is also different — they iterate the content blocks,
execute matching tools, and feed back ``tool_result`` blocks. We don't try to
hide that; we just record whatever Anthropic returns, on the same ``Trace``
data model the rest of agentprdiff uses.

Usage::

    from anthropic import Anthropic
    from agentprdiff.adapters.anthropic import instrument_client, instrument_tools

    def my_agent(query: str):
        client = Anthropic()
        with instrument_client(client) as trace:
            tools = instrument_tools(TOOL_MAP, trace)
            # ...standard Anthropic Messages tool-use loop...
            return final_text, trace
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from typing import Any

from ..core import LLMCall, Trace
from .openai import _jsonable, _make_tool_wrapper, _serialize_messages
from .pricing import PriceTable, estimate_cost_usd


def _extract_anthropic_blocks(content: Any) -> tuple[str, list[dict[str, Any]]]:
    """Walk Anthropic content blocks and extract output text + tool_use calls.

    Returns ``(output_text, tool_calls_summary)``. Any thinking / redacted /
    unknown block types are quietly ignored — they're not asserted against by
    any current grader.
    """
    output_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    if not content:
        return "", []
    for block in content:
        btype = getattr(block, "type", None)
        if btype is None and isinstance(block, dict):
            btype = block.get("type")
        if btype == "text":
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text", "")
            output_parts.append(text or "")
        elif btype == "tool_use":
            name = getattr(block, "name", None)
            inputs = getattr(block, "input", None)
            tu_id = getattr(block, "id", None)
            if name is None and isinstance(block, dict):
                name = block.get("name")
                inputs = block.get("input")
                tu_id = block.get("id")
            if name:
                tool_calls.append(
                    {
                        "id": tu_id,
                        "name": name,
                        "arguments": inputs or {},
                    }
                )
    return "".join(output_parts), tool_calls


@contextmanager
def instrument_client(
    client: Any,
    *,
    trace: Trace | None = None,
    prices: PriceTable | None = None,
    provider: str | None = None,
) -> Iterator[Trace]:
    """Patch ``client.messages.create`` to record onto a Trace.

    See the OpenAI adapter docstring for parameter semantics — they match.
    """
    if trace is None:
        trace = Trace(suite_name="", case_name="", input=None)
    provider_str = provider or "anthropic"

    messages_attr = getattr(client, "messages", None)
    if messages_attr is None or not hasattr(messages_attr, "create"):
        raise TypeError(
            "instrument_client expected an Anthropic client with "
            "client.messages.create; got "
            f"{type(client).__name__}."
        )

    original_create: Callable[..., Any] = messages_attr.create
    had_instance_attr = "create" in vars(messages_attr)
    instance_attr_value = vars(messages_attr).get("create")

    def patched_create(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            response = original_create(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            trace.record_llm_call(
                LLMCall(
                    provider=provider_str,
                    model=str(kwargs.get("model", "")),
                    input_messages=_serialize_messages(kwargs.get("messages")),
                    output_text=f"<exception: {type(exc).__name__}: {exc}>",
                    latency_ms=elapsed_ms,
                )
            )
            raise

        elapsed_ms = (time.perf_counter() - start) * 1000.0

        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        model_id = str(getattr(response, "model", "") or kwargs.get("model", "") or "")

        content = getattr(response, "content", None)
        output_text, tool_calls_summary = _extract_anthropic_blocks(content)

        cost = estimate_cost_usd(
            model_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prices=prices,
        )

        trace.record_llm_call(
            LLMCall(
                provider=provider_str,
                model=model_id,
                input_messages=_serialize_messages(kwargs.get("messages")),
                output_text=output_text,
                tool_calls=tool_calls_summary,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost,
                latency_ms=elapsed_ms,
            )
        )
        return response

    messages_attr.create = patched_create  # type: ignore[method-assign]
    try:
        yield trace
    finally:
        if had_instance_attr:
            messages_attr.create = instance_attr_value  # type: ignore[method-assign]
        else:
            with suppress(AttributeError):
                del messages_attr.create  # type: ignore[attr-defined]


def instrument_tools(
    tool_map: Mapping[str, Callable[..., Any]],
    trace: Trace,
) -> dict[str, Callable[..., Any]]:
    """Wrap each tool callable to record a ``ToolCall`` per invocation.

    Identical semantics to the OpenAI adapter version — the data model is
    SDK-agnostic. We re-export here so adopters can do the natural::

        from agentprdiff.adapters.anthropic import instrument_client, instrument_tools
    """
    wrapped: dict[str, Callable[..., Any]] = {}
    for name, fn in tool_map.items():
        wrapped[name] = _make_tool_wrapper(name, fn, trace)
    return wrapped


# Re-export the helpers so tests / advanced users don't have to import from
# the OpenAI module explicitly when they're already in the Anthropic adapter.
__all__ = [
    "instrument_client",
    "instrument_tools",
    "_jsonable",
]

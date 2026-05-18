"""OpenAI / OpenAI-compatible adapter.

The adapter monkey-patches ``client.chat.completions.create`` for the duration
of a ``with`` block, recording one ``LLMCall`` per invocation onto a ``Trace``.
The patch is reversed on exit, so the user's client object behaves identically
outside the ``with`` block.

This adapter works with any SDK that uses the OpenAI Python client shape:

* OpenAI / **AsyncOpenAI** itself
* Groq (``base_url="https://api.groq.com/openai/v1"``)
* Google Gemini's OpenAI-compatible endpoint
* OpenRouter
* Ollama (``base_url="http://localhost:11434/v1"``)
* vLLM, Together, Fireworks, DeepInfra, Anyscale, etc.

Sync usage::

    from agentprdiff.adapters.openai import instrument_client, instrument_tools

    def my_agent(query: str):
        client = OpenAI(api_key=...)
        with instrument_client(client) as trace:
            tools = instrument_tools(TOOL_MAP, trace)
            # ...standard OpenAI tool-calling loop, unchanged...
            return final_text, trace

Async usage — same API. ``instrument_client`` detects an ``AsyncOpenAI``
client (``client.chat.completions.create`` is a coroutine function) and
installs an awaitable patched method automatically. The ``with`` block
itself stays a plain ``with`` — the patch is bound to the client instance,
not the running event loop::

    from openai import AsyncOpenAI
    from agentprdiff.adapters.openai import instrument_client, instrument_tools

    async def my_agent_async(query: str):
        client = AsyncOpenAI(api_key=...)
        with instrument_client(client) as trace:
            tools = instrument_tools(TOOL_MAP, trace)
            response = await client.chat.completions.create(...)
            # ...async tool-calling loop, unchanged; tool wrappers are
            # awaitable iff the underlying tool is `async def`...
            return final_text, trace

    def my_agent(query: str):
        # agentprdiff's runner is sync — bridge with asyncio.run.
        return asyncio.run(my_agent_async(query))

The Trace's ``suite_name`` / ``case_name`` / ``input`` are filled in by
``run_agent`` after the agent returns; you can leave them blank inside the
adapter.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from typing import Any

from ..core import LLMCall, ToolCall, Trace
from .pricing import PriceTable, estimate_cost_usd


def _infer_provider_from_client(client: Any) -> str:
    """Best-effort guess at the underlying provider from the client's base_url.

    Falls back to ``"openai-compatible"`` if we can't tell. The provider string
    only affects the recorded `LLMCall.provider` field — it doesn't change
    behavior — so a fuzzy match is fine.
    """
    base_url = ""
    try:
        base_url = str(getattr(client, "base_url", "") or "")
    except Exception:  # noqa: BLE001
        return "openai-compatible"

    url = base_url.lower()
    if "groq" in url:
        return "groq"
    if "openrouter" in url:
        return "openrouter"
    if "googleapis" in url or "generativelanguage" in url:
        return "gemini"
    if "ollama" in url or "11434" in url:
        return "ollama"
    if "together" in url:
        return "together"
    if "fireworks" in url:
        return "fireworks"
    if "deepinfra" in url:
        return "deepinfra"
    if "anthropic" in url:
        # Anthropic's OpenAI-compat shim. Use the native adapter instead for
        # full fidelity, but still flag it.
        return "anthropic-openai-compat"
    if "openai" in url or url == "":
        return "openai"
    return "openai-compatible"


def _extract_tool_calls(message: Any) -> list[dict[str, Any]]:
    """Pull tool_calls off a ChatCompletionMessage in a defensive way."""
    raw = getattr(message, "tool_calls", None) or []
    out: list[dict[str, Any]] = []
    for tc in raw:
        # OpenAI SDK objects are pydantic-like; fall through to dict access too.
        try:
            fn = tc.function
            name = getattr(fn, "name", None)
            arguments = getattr(fn, "arguments", None)
            tc_id = getattr(tc, "id", None)
        except AttributeError:
            try:
                fn = tc.get("function", {})
                name = fn.get("name")
                arguments = fn.get("arguments")
                tc_id = tc.get("id")
            except Exception:  # noqa: BLE001
                continue
        if name is None:
            continue
        out.append({"id": tc_id, "name": name, "arguments": arguments})
    return out


def _serialize_messages(messages: Any) -> list[dict[str, Any]]:
    """Best-effort JSON-friendly copy of the request messages.

    Trace baselines must be JSON-serializable, and `messages` is sometimes a
    list of pydantic objects, sometimes a list of plain dicts. Prefer dicts;
    skip anything we can't represent cleanly.
    """
    if not messages:
        return []
    out: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, dict):
            out.append(m)
            continue
        # pydantic-ish?
        for attr in ("model_dump", "dict"):
            dump = getattr(m, attr, None)
            if callable(dump):
                try:
                    out.append(dump())
                    break
                except Exception:  # noqa: BLE001
                    pass
        else:
            # Last resort — string repr so the trace round-trips.
            out.append({"_repr": repr(m)})
    return out


@contextmanager
def instrument_client(
    client: Any,
    *,
    trace: Trace | None = None,
    prices: PriceTable | None = None,
    provider: str | None = None,
) -> Iterator[Trace]:
    """Patch ``client.chat.completions.create`` to record onto a Trace.

    Yields the ``Trace`` so the caller can return ``(output, trace)`` from
    their agent function. The patch is restored on exit even if the agent
    raises.

    Parameters
    ----------
    client:
        An ``openai.OpenAI`` (or compatible) client instance. We patch a bound
        attribute on this specific instance — global SDK state is untouched.
    trace:
        Optional pre-existing ``Trace`` to record into. Useful when nesting
        adapters or stitching together a multi-stage agent. If omitted, we
        create a fresh one with empty suite/case/input fields (the runner
        fills them in after the agent returns).
    prices:
        Optional override for the model→price table. See
        :mod:`agentprdiff.adapters.pricing`.
    provider:
        Optional explicit provider tag; otherwise we infer from
        ``client.base_url``.
    """
    if trace is None:
        trace = Trace(suite_name="", case_name="", input=None)
    provider_str = provider or _infer_provider_from_client(client)

    # Locate the create function we need to patch. Newer SDKs:
    #   client.chat.completions.create
    chat = getattr(client, "chat", None)
    completions = getattr(chat, "completions", None) if chat is not None else None
    if completions is None or not hasattr(completions, "create"):
        raise TypeError(
            "instrument_client expected an OpenAI-style client with "
            "client.chat.completions.create; got "
            f"{type(client).__name__}. If you're using a non-OpenAI SDK, see "
            "agentprdiff/adapters/anthropic.py or open an issue."
        )

    # Stash the original so we can call through, plus remember whether the
    # attribute was carried as an instance attr or was descriptor-resolved
    # from the class. We need that to cleanly restore on exit (del-on-exit if
    # it wasn't originally an instance attr; assign-on-exit if it was).
    original_create: Callable[..., Any] = completions.create
    had_instance_attr = "create" in vars(completions)
    instance_attr_value = vars(completions).get("create")

    # AsyncOpenAI's `create` is `async def`; the sync OpenAI's is plain. Pick
    # the right shape so callers get an awaitable iff the original was one.
    is_async = asyncio.iscoroutinefunction(original_create)
    patched_create = (
        _make_async_patched_create(
            original_create, trace=trace, provider=provider_str, prices=prices
        )
        if is_async
        else _make_sync_patched_create(
            original_create, trace=trace, provider=provider_str, prices=prices
        )
    )

    # Apply the patch on this specific instance only.
    completions.create = patched_create  # type: ignore[method-assign]
    try:
        yield trace
    finally:
        if had_instance_attr:
            completions.create = instance_attr_value  # type: ignore[method-assign]
        else:
            # Drop the instance attribute so the original class-level
            # descriptor (the bound method) shines through again.
            # Defensive: someone else already cleaned up → no-op.
            with suppress(AttributeError):
                del completions.create  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Patched-create factories.
#
# Sync and async paths share everything except the call-and-await step. We
# factor the shared response-parsing into _record_completion so the timing
# code stays a thin wrapper above it. Avoids drift between the two paths.
# ---------------------------------------------------------------------------


def _record_completion(
    response: Any,
    *,
    trace: Trace,
    kwargs: Mapping[str, Any],
    elapsed_ms: float,
    provider: str,
    prices: PriceTable | None,
) -> None:
    """Extract usage/output/tool-calls from an OpenAI-shaped response and
    record an ``LLMCall`` onto ``trace``. Shared by sync and async patches."""
    # Pull usage / model / output safely; some compatible servers omit fields
    # we'd like to have.
    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    model_id = str(getattr(response, "model", "") or kwargs.get("model", "") or "")

    choices = getattr(response, "choices", None) or []
    message = getattr(choices[0], "message", None) if choices else None
    output_text = getattr(message, "content", None) or "" if message is not None else ""
    tool_calls_summary = _extract_tool_calls(message) if message is not None else []

    cost = estimate_cost_usd(
        model_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prices=prices,
    )

    trace.record_llm_call(
        LLMCall(
            provider=provider,
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


def _record_failure(
    exc: BaseException,
    *,
    trace: Trace,
    kwargs: Mapping[str, Any],
    elapsed_ms: float,
    provider: str,
) -> None:
    trace.record_llm_call(
        LLMCall(
            provider=provider,
            model=str(kwargs.get("model", "")),
            input_messages=_serialize_messages(kwargs.get("messages")),
            output_text=f"<exception: {type(exc).__name__}: {exc}>",
            latency_ms=elapsed_ms,
        )
    )


def _apply_model_override(kwargs: dict[str, Any]) -> dict[str, Any]:
    """If a global model override is set, return a new kwargs dict with
    ``model`` rewritten. Returns the original dict when no override is active.

    The lookup happens at call time so multi-leg benchmarks can flip the
    override between runs in the same process.
    """
    # Import at call time to avoid an import cycle (adapters/__init__.py
    # itself imports from .pricing, which is fine; this module imports from
    # adapters at package level but we want the *live* value).
    from . import get_default_model

    override = get_default_model()
    if override is None:
        return kwargs
    # Only rewrite if the caller actually passed a model — preserves the
    # SDK's own error path when model is missing.
    if "model" not in kwargs:
        return kwargs
    new_kwargs = dict(kwargs)
    new_kwargs["model"] = override
    return new_kwargs


def _make_sync_patched_create(
    original_create: Callable[..., Any],
    *,
    trace: Trace,
    provider: str,
    prices: PriceTable | None,
) -> Callable[..., Any]:
    def patched_create(*args: Any, **kwargs: Any) -> Any:
        kwargs = _apply_model_override(kwargs)
        start = time.perf_counter()
        try:
            response = original_create(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            _record_failure(
                exc, trace=trace, kwargs=kwargs, elapsed_ms=elapsed_ms, provider=provider
            )
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _record_completion(
            response,
            trace=trace,
            kwargs=kwargs,
            elapsed_ms=elapsed_ms,
            provider=provider,
            prices=prices,
        )
        return response

    return patched_create


def _make_async_patched_create(
    original_create: Callable[..., Awaitable[Any]],
    *,
    trace: Trace,
    provider: str,
    prices: PriceTable | None,
) -> Callable[..., Awaitable[Any]]:
    async def patched_create(*args: Any, **kwargs: Any) -> Any:
        kwargs = _apply_model_override(kwargs)
        start = time.perf_counter()
        try:
            response = await original_create(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            _record_failure(
                exc, trace=trace, kwargs=kwargs, elapsed_ms=elapsed_ms, provider=provider
            )
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _record_completion(
            response,
            trace=trace,
            kwargs=kwargs,
            elapsed_ms=elapsed_ms,
            provider=provider,
            prices=prices,
        )
        return response

    return patched_create


def instrument_tools(
    tool_map: Mapping[str, Callable[..., Any]],
    trace: Trace,
) -> dict[str, Callable[..., Any]]:
    """Wrap each callable in ``tool_map`` so invocations record ``ToolCall``s.

    Returns a new dict — the original is untouched. Use it the same way you'd
    use the original::

        tools = instrument_tools(TOOL_MAP, trace)
        result = tools[fn_name](**fn_args)

    For async tools (``async def``), the wrapper is itself ``async def``, so
    awaiting it works exactly like awaiting the original::

        result = await tools[fn_name](**fn_args)

    The choice is made per-tool — a tool map can mix sync and async entries
    and each wrapper matches the underlying callable. Each call records:

    * ``name`` — the dict key
    * ``arguments`` — the kwargs (and positional args under ``"_args"`` if any)
    * ``result`` — the return value (or None if the call raised)
    * ``latency_ms`` — wall-clock latency (await time included for async)
    * ``error`` — exception text on failure
    """
    wrapped: dict[str, Callable[..., Any]] = {}
    for name, fn in tool_map.items():
        if asyncio.iscoroutinefunction(fn):
            wrapped[name] = _make_async_tool_wrapper(name, fn, trace)
        else:
            wrapped[name] = _make_tool_wrapper(name, fn, trace)
    return wrapped


def _record_tool_success(
    *,
    trace: Trace,
    name: str,
    arguments: dict[str, Any],
    result: Any,
    elapsed_ms: float,
) -> None:
    trace.record_tool_call(
        ToolCall(
            name=name,
            arguments=arguments,
            result=_jsonable(result),
            latency_ms=elapsed_ms,
        )
    )


def _record_tool_failure(
    *,
    trace: Trace,
    name: str,
    arguments: dict[str, Any],
    exc: BaseException,
    elapsed_ms: float,
) -> None:
    trace.record_tool_call(
        ToolCall(
            name=name,
            arguments=arguments,
            result=None,
            latency_ms=elapsed_ms,
            error=f"{type(exc).__name__}: {exc}",
        )
    )


def _arguments_dict(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    arguments: dict[str, Any] = dict(kwargs)
    if args:
        arguments["_args"] = list(args)
    return arguments


def _make_tool_wrapper(
    name: str,
    fn: Callable[..., Any],
    trace: Trace,
) -> Callable[..., Any]:
    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        arguments = _arguments_dict(args, kwargs)
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            _record_tool_failure(
                trace=trace,
                name=name,
                arguments=arguments,
                exc=exc,
                elapsed_ms=elapsed_ms,
            )
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _record_tool_success(
            trace=trace,
            name=name,
            arguments=arguments,
            result=result,
            elapsed_ms=elapsed_ms,
        )
        return result

    _wrapped.__name__ = f"instrumented_{name}"
    _wrapped.__doc__ = getattr(fn, "__doc__", None)
    return _wrapped


def _make_async_tool_wrapper(
    name: str,
    fn: Callable[..., Awaitable[Any]],
    trace: Trace,
) -> Callable[..., Awaitable[Any]]:
    async def _wrapped(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        arguments = _arguments_dict(args, kwargs)
        try:
            result = await fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            _record_tool_failure(
                trace=trace,
                name=name,
                arguments=arguments,
                exc=exc,
                elapsed_ms=elapsed_ms,
            )
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _record_tool_success(
            trace=trace,
            name=name,
            arguments=arguments,
            result=result,
            elapsed_ms=elapsed_ms,
        )
        return result

    _wrapped.__name__ = f"instrumented_async_{name}"
    _wrapped.__doc__ = getattr(fn, "__doc__", None)
    return _wrapped


def _jsonable(value: Any) -> Any:
    """Best-effort coerce a tool's return value to something JSON-serializable.

    Pydantic models get model_dump'd; primitive types, lists, and dicts pass
    through; anything else falls back to repr.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except Exception:  # noqa: BLE001
            pass
    return repr(value)

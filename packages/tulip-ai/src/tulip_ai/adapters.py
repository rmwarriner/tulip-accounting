"""Provider adapters — the one chokepoint where prompts leave the local boundary.

Per ADR-0005 §Q2, v1 ships a single ``LitellmAdapter`` that routes by provider
name. Tests use ``RecordingAdapter`` to capture the exact messages a capability
would have sent, without issuing a real network call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable

from tulip_ai.errors import AIProviderError


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """One adapter call's result."""

    text: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_estimate_usd: Decimal
    provider_response_id: str | None = None


@runtime_checkable
class ProviderAdapter(Protocol):
    """One synchronous LLM call. ADR-0005 §Q2.

    Implementations must raise :class:`tulip_ai.errors.AIProviderError`
    on any failure — the caller does not catch broader exception types.
    """

    async def chat(
        self,
        *,
        provider: str,
        model: str,
        api_key: str | None,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float = 0.0,
    ) -> ProviderResponse:
        """Issue one chat-completion request; return text + token counts."""
        ...


class LitellmAdapter:
    """Production adapter routing through ``litellm.acompletion``.

    Pulls minimal data out of the litellm response — enough to populate
    an ``ai_invocations`` row, no more. ``litellm`` is imported lazily
    so the module is cheap to import in tests that never call it.
    """

    async def chat(
        self,
        *,
        provider: str,
        model: str,
        api_key: str | None,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float = 0.0,
    ) -> ProviderResponse:
        """See :class:`ProviderAdapter` Protocol."""
        import time

        import litellm

        # litellm uses ``{provider}/{model}`` notation for some providers.
        # For ollama, the model name is e.g. ``ollama/llama3:70b``.
        # Adapters that need a more involved mapping subclass / replace this.
        model_id = model if "/" in model else f"{provider}/{model}"

        kwargs: dict[str, object] = {
            "model": model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if api_key is not None:
            kwargs["api_key"] = api_key

        started = time.monotonic()
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:
            raise AIProviderError(
                f"{provider} ({model}) call failed: {type(exc).__name__}: {exc}"
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        usage = getattr(response, "usage", None)
        tokens_in = int(getattr(usage, "prompt_tokens", 0)) if usage else 0
        tokens_out = int(getattr(usage, "completion_tokens", 0)) if usage else 0
        text = ""
        if response.choices:
            text = response.choices[0].message.content or ""
        cost = Decimal(str(getattr(response, "_hidden_params", {}).get("response_cost", 0) or 0))
        return ProviderResponse(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_estimate_usd=cost,
            provider_response_id=getattr(response, "id", None),
        )


@dataclass(slots=True)
class RecordingAdapter:
    """Test seam — captures messages without issuing a real call.

    Usage::

        adapter = RecordingAdapter(canned_reply='{"account_code": "5100"}')
        await adapter.chat(...)
        assert adapter.calls[0]["messages"] == ...
    """

    canned_reply: str = ""
    cost: Decimal = Decimal("0.00")
    calls: list[dict[str, object]] = field(default_factory=list)

    async def chat(
        self,
        *,
        provider: str,
        model: str,
        api_key: str | None,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float = 0.0,
    ) -> ProviderResponse:
        """Record the call args; return the canned reply."""
        self.calls.append(
            {
                "provider": provider,
                "model": model,
                "api_key_was_passed": api_key is not None,
                "messages": list(messages),
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        # Rough token estimates so the audit row carries non-zero usage.
        tokens_in = sum(len(m.get("content", "")) for m in messages) // 4
        tokens_out = len(self.canned_reply) // 4
        return ProviderResponse(
            text=self.canned_reply,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=0,
            cost_estimate_usd=self.cost,
            provider_response_id=None,
        )

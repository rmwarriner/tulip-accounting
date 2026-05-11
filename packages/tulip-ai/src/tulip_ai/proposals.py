"""``AIProposalCapability`` — AI-driven proposal generation (P6.4.b, ADR-0005 §Q3).

The "agentic" capability from the ADR's data-flow contract. Each method
produces a structured proposal that the API layer wraps in a
``PendingProposal`` row (with ``created_by_kind=ai_agent`` and the
``ai_invocation_id`` link back to the audit row this call wrote).

v1 ships one suggestion kind: ``suggest_envelope_budget``. Same pattern
applies to future suggestion kinds — the capability stays close to the
data and just returns ``ProposedChange`` payloads; the user-side review
flow (P6.4) handles approval / rejection.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from tulip_ai.adapters import ProviderAdapter
from tulip_ai.audit import AIInvocationRecord, AIInvocationWriter, hash_prompt_payload
from tulip_ai.cost import PreCallApproval, enforce_pre_call
from tulip_ai.errors import AIProviderError
from tulip_ai.forecast import bucket_time_series
from tulip_ai.policy import resolve_policy

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session, sessionmaker


log = logging.getLogger("tulip_ai.proposals")


@dataclass(frozen=True, slots=True)
class ProposedChange:
    """A capability-generated proposal ready for the queue.

    Mirrors the body the ``POST /v1/ai/proposals`` endpoint takes — kind,
    title, payload, rationale — plus the ``ai_invocation_id`` link the
    capability stamped on its audit row.
    """

    kind: str
    title: str
    payload: dict[str, object]
    rationale: str
    ai_invocation_id: UUID | None


@dataclass(frozen=True, slots=True)
class SuggestionResult:
    """Capability outcome — either a proposed change or a structured error."""

    proposal: ProposedChange | None
    error: str | None = None


class AIProposalCapability:
    """Production proposal-generation capability."""

    def __init__(
        self,
        *,
        session_maker: sessionmaker[Session],
        adapter: ProviderAdapter,
    ) -> None:
        """Bind to the session factory and the provider adapter."""
        self._session_maker = session_maker
        self._adapter = adapter

    async def suggest_envelope_budget(
        self,
        *,
        household_id: UUID,
        actor_user_id: UUID | None,
        api_key: str | None,
        envelope_id: UUID,
        envelope_name: str,
        currency: str,
        current_budget: Decimal | None,
        recent_spend_series: list[tuple[date, Decimal]],
    ) -> SuggestionResult:
        """Suggest a new ``budget_amount`` for one envelope.

        Wide try/except at the boundary mirrors the other capabilities —
        AI-stack failures never crash the API request that asked for a
        suggestion; the user gets a structured error instead.
        """
        try:
            return await self._suggest_envelope_budget_inner(
                household_id=household_id,
                actor_user_id=actor_user_id,
                api_key=api_key,
                envelope_id=envelope_id,
                envelope_name=envelope_name,
                currency=currency,
                current_budget=current_budget,
                recent_spend_series=recent_spend_series,
            )
        except Exception:
            log.exception(
                "ai.suggest_envelope_budget.failed",
                extra={
                    "household_id": str(household_id),
                    "envelope_id": str(envelope_id),
                },
            )
            return SuggestionResult(proposal=None, error="Internal AI capability failure.")

    async def _suggest_envelope_budget_inner(
        self,
        *,
        household_id: UUID,
        actor_user_id: UUID | None,
        api_key: str | None,
        envelope_id: UUID,
        envelope_name: str,
        currency: str,
        current_budget: Decimal | None,
        recent_spend_series: list[tuple[date, Decimal]],
    ) -> SuggestionResult:
        from tulip_storage.models import Household

        with self._session_maker() as session:
            household = session.get(Household, household_id)
            if household is None:
                return SuggestionResult(proposal=None, error="household not found")
            policy = resolve_policy(household.ai_policy, None, "agentic")

            if policy.level == "disabled":
                self._audit(
                    household_id=household_id,
                    actor_user_id=actor_user_id,
                    policy_level="disabled",
                    profile=policy.profile,
                    outcome="policy_disabled",
                    prompt_hash=hash_prompt_payload(
                        {"task": "suggest_envelope_budget", "envelope_id": str(envelope_id)}
                    ),
                )
                return SuggestionResult(proposal=None, error="agentic disabled")

            if api_key is None and policy.provider != "ollama":
                self._audit(
                    household_id=household_id,
                    actor_user_id=actor_user_id,
                    policy_level=policy.level,
                    profile=policy.profile,
                    provider=policy.provider,
                    model=policy.model,
                    outcome="provider_error",
                    prompt_hash=hash_prompt_payload(
                        {"task": "suggest_envelope_budget", "envelope_id": str(envelope_id)}
                    ),
                    response_text="no api key configured for provider",
                )
                return SuggestionResult(proposal=None, error="no api key")

            gate = enforce_pre_call(
                session,
                household_id=household_id,
                user_id=actor_user_id,
                rate_limit_per_hour=policy.rate_limit_per_hour,
                monthly_cost_cap_usd=policy.monthly_cost_cap_usd,
                cost_cap_behaviour=policy.cost_cap_behaviour,
                fallback_provider=policy.fallback_provider,
                fallback_model=policy.fallback_model,
                primary_provider=policy.provider,
                primary_model=policy.model,
            )
            if not isinstance(gate, PreCallApproval):
                self._audit(
                    household_id=household_id,
                    actor_user_id=actor_user_id,
                    policy_level=policy.level,
                    profile=policy.profile,
                    provider=policy.provider,
                    model=policy.model,
                    outcome=gate.outcome,
                    prompt_hash=hash_prompt_payload(
                        {"task": "suggest_envelope_budget", "envelope_id": str(envelope_id)}
                    ),
                    response_text=gate.reason[:500],
                )
                return SuggestionResult(proposal=None, error=gate.outcome)

        call_provider = gate.provider or ""
        call_model = gate.model or ""
        call_api_key = api_key if not gate.degraded else None

        bucketed = bucket_time_series(recent_spend_series, profile=policy.profile)
        prompt_body: dict[str, object] = {
            "task": "suggest_envelope_budget",
            "envelope_id": str(envelope_id),
            "currency": currency,
            "current_budget": str(current_budget) if current_budget is not None else None,
            "recent_spend_series": [{"date": d.isoformat(), "amount": str(a)} for d, a in bucketed],
        }
        if policy.profile != "strict":
            prompt_body["envelope_name"] = envelope_name
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an envelope-budgeting analyst. Given an envelope's "
                    "recent spending series and its current budget, propose a new "
                    "budget_amount that reflects observed spending plus a small "
                    "buffer. Respond with a strict JSON object: "
                    '{"new_budget_amount": "<decimal>", "rationale": "<short>"}. '
                    "Do not include code fences or commentary."
                ),
            },
            {"role": "user", "content": json.dumps(prompt_body, ensure_ascii=False)},
        ]

        try:
            response = await self._adapter.chat(
                provider=call_provider,
                model=call_model,
                api_key=call_api_key,
                messages=messages,
                max_tokens=300,
            )
        except AIProviderError as exc:
            self._audit(
                household_id=household_id,
                actor_user_id=actor_user_id,
                policy_level=policy.level,
                profile=policy.profile,
                provider=call_provider,
                model=call_model,
                outcome="provider_error",
                prompt_hash=hash_prompt_payload(prompt_body),
                response_text=str(exc)[:500],
            )
            return SuggestionResult(proposal=None, error=f"provider error: {exc}")

        parsed = _parse_suggestion(response.text, currency=currency)
        if parsed is None:
            self._audit(
                household_id=household_id,
                actor_user_id=actor_user_id,
                policy_level=policy.level,
                profile=policy.profile,
                provider=call_provider,
                model=call_model,
                outcome="provider_error",
                prompt_hash=hash_prompt_payload(prompt_body),
                response_text=f"unparseable suggestion: {response.text[:300]}",
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                cost_estimate_usd=response.cost_estimate_usd,
                latency_ms=response.latency_ms,
            )
            return SuggestionResult(proposal=None, error="model returned unparseable suggestion")

        new_amount, rationale = parsed
        invocation_id = self._audit(
            household_id=household_id,
            actor_user_id=actor_user_id,
            policy_level=policy.level,
            profile=policy.profile,
            provider=call_provider,
            model=call_model,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            cost_estimate_usd=response.cost_estimate_usd,
            latency_ms=response.latency_ms,
            outcome="success",
            prompt_hash=hash_prompt_payload(prompt_body),
            provider_response_id=response.provider_response_id,
            prompt_json=(
                json.dumps(prompt_body, ensure_ascii=False) if policy.log_prompts else None
            ),
            response_text=response.text if policy.log_prompts else None,
        )

        proposal = ProposedChange(
            kind="envelope_budget_update",
            title=f"Adjust {envelope_name} budget to {new_amount} {currency}",
            payload={
                "envelope_id": str(envelope_id),
                "new_budget_amount": str(new_amount),
            },
            rationale=rationale,
            ai_invocation_id=invocation_id,
        )
        return SuggestionResult(proposal=proposal)

    def _audit(
        self,
        *,
        household_id: UUID,
        actor_user_id: UUID | None,
        policy_level: str,
        profile: str,
        outcome: str,
        prompt_hash: bytes,
        provider: str | None = None,
        model: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_estimate_usd: Decimal = Decimal("0"),
        latency_ms: int = 0,
        provider_response_id: str | None = None,
        prompt_json: str | None = None,
        response_text: str | None = None,
    ) -> UUID | None:
        """Write one ``ai_invocations`` row and return its id."""
        with self._session_maker() as session:
            row = AIInvocationWriter(session).write(
                AIInvocationRecord(
                    household_id=household_id,
                    capability="agentic",
                    policy_resolved=policy_level,
                    profile=profile,
                    provider=provider,
                    model=model,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_estimate_usd=cost_estimate_usd,
                    latency_ms=latency_ms,
                    outcome=outcome,
                    prompt_hash=prompt_hash,
                    provider_response_id=provider_response_id,
                    actor_user_id=actor_user_id,
                    prompt_json=prompt_json,
                    response_text=response_text,
                )
            )
            session.commit()
            return row.id


def _parse_suggestion(text: str, *, currency: str) -> tuple[Decimal, str] | None:
    """Pull ``new_budget_amount`` + ``rationale`` from the model response."""
    del currency  # reserved for future cross-currency validation
    cleaned = text.strip()
    # Tolerate fenced JSON despite the system prompt.
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            first, _, rest = cleaned.partition("\n")
            if first.strip().lower() in ("json", "javascript"):
                cleaned = rest
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    amount_raw = parsed.get("new_budget_amount")
    rationale = parsed.get("rationale", "")
    if amount_raw is None:
        return None
    try:
        amount = Decimal(str(amount_raw))
    except (ArithmeticError, ValueError):
        return None
    if amount < 0:
        return None
    return amount, str(rationale)


__all__ = [
    "AIProposalCapability",
    "ProposedChange",
    "SuggestionResult",
]

"""``AIForecastCapability`` — envelope runout + sinking-fund on-track (P6.3.b, ADR-0005 §Q3).

Single-turn flow: send ``{envelope_name, time_series, target_amount,
target_date, recent_inflow_average}`` to the model and ask for a short
natural-language forecast. The result lands in ``notifications`` as
``kind=forecast``; the user reads via the same ``tulip notifications list``
surface the anomaly detector populates.

Bucketing matters because per ADR-0005 §Q3 the model doesn't need exact
amounts to summarise a trend. Default: round each amount to the nearest
5% of the series' maximum absolute value. Strict: 25%. ``local_only``
passes amounts through unchanged.

Failures (provider error, malformed response, no key) return an
``NLAnswer``-shaped error rather than raising — the daily-insights
handler logs the audit row and skips the forecast row for that envelope,
so the rest of the run keeps going.
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
from tulip_ai.policy import resolve_policy
from tulip_ai.redaction import RedactionProfile

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session, sessionmaker


log = logging.getLogger("tulip_ai.forecast")


@dataclass(frozen=True, slots=True)
class ForecastPromptPayload:
    """The exact shape the forecast capability sends per envelope."""

    envelope_id: str
    envelope_name: str | None  # None when strict profile elides the name
    currency: str
    time_series: tuple[tuple[str, Decimal], ...]  # (iso_date, bucketed_amount) tuples
    target_amount: Decimal | None
    target_date: str | None
    recent_inflow_average: Decimal | None

    def to_dict(self) -> dict[str, object]:
        """JSON-serializable view."""
        out: dict[str, object] = {
            "task": "forecast",
            "envelope_id": self.envelope_id,
            "currency": self.currency,
            "time_series": [{"date": d, "amount": str(a)} for d, a in self.time_series],
        }
        if self.envelope_name is not None:
            out["envelope_name"] = self.envelope_name
        if self.target_amount is not None:
            out["target_amount"] = str(self.target_amount)
        if self.target_date is not None:
            out["target_date"] = self.target_date
        if self.recent_inflow_average is not None:
            out["recent_inflow_average"] = str(self.recent_inflow_average)
        return out


@dataclass(frozen=True, slots=True)
class ForecastResult:
    """One forecast call's outcome."""

    text: str
    error: str | None = None


def _round_to_bucket(amount: Decimal, bucket_size: Decimal) -> Decimal:
    """Round ``amount`` to the nearest multiple of ``bucket_size``.

    Bucket size of zero (degenerate input — all amounts are zero)
    passes the input through unchanged.
    """
    if bucket_size == 0:
        return amount
    return (amount / bucket_size).to_integral_value() * bucket_size


def bucket_time_series(
    series: list[tuple[date, Decimal]], *, profile: RedactionProfile
) -> list[tuple[date, Decimal]]:
    """Bucket a time-series per ADR-0005 §Q3.

    ``default``: each amount rounded to the nearest 5% of the series'
    maximum absolute value.
    ``strict``: 25%.
    ``local_only``: pass-through (the local model already sees raw data).
    """
    if profile == "local_only" or not series:
        return list(series)
    max_abs = max((abs(amt) for _, amt in series), default=Decimal("0"))
    if max_abs == 0:
        return list(series)
    pct = Decimal("0.05") if profile == "default" else Decimal("0.25")
    bucket = max_abs * pct
    return [(d, _round_to_bucket(amt, bucket)) for d, amt in series]


def build_forecast_prompt(
    *,
    envelope_id: str,
    envelope_name: str,
    currency: str,
    time_series: list[tuple[date, Decimal]],
    target_amount: Decimal | None,
    target_date: date | None,
    recent_inflow_average: Decimal | None,
    profile: RedactionProfile,
) -> ForecastPromptPayload:
    """Assemble the per-envelope forecast prompt payload.

    The bucketing happens here so the preview surface and the live call
    see the same bytes — tests can assert the byte-faithful preview
    equals what the capability actually sends.
    """
    bucketed = bucket_time_series(time_series, profile=profile)
    # Strict elides the envelope name (ADR-0005 §Q3 — "id only").
    name_for_prompt = envelope_name if profile != "strict" else None
    return ForecastPromptPayload(
        envelope_id=envelope_id,
        envelope_name=name_for_prompt,
        currency=currency,
        time_series=tuple((d.isoformat(), amt) for d, amt in bucketed),
        target_amount=target_amount,
        target_date=target_date.isoformat() if target_date else None,
        recent_inflow_average=recent_inflow_average,
    )


def _build_messages(payload: ForecastPromptPayload) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are an envelope-budgeting forecaster. Given a recent "
                "spending series for one envelope, predict whether it is on "
                "track to run out before next cycle or stay positive. Respond "
                "in 1-2 sentences with the calendar date by which the envelope "
                "is expected to be empty if applicable, or 'on track' if not."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload.to_dict(), ensure_ascii=False),
        },
    ]


class AIForecastCapability:
    """Production forecast capability; constructed once at app boot."""

    def __init__(
        self,
        *,
        session_maker: sessionmaker[Session],
        adapter: ProviderAdapter,
    ) -> None:
        """Bind to the session factory and the provider adapter."""
        self._session_maker = session_maker
        self._adapter = adapter

    async def forecast(
        self,
        *,
        household_id: UUID,
        actor_user_id: UUID | None,
        api_key: str | None,
        envelope_id: UUID,
        envelope_name: str,
        currency: str,
        time_series: list[tuple[date, Decimal]],
        target_amount: Decimal | None = None,
        target_date: date | None = None,
        recent_inflow_average: Decimal | None = None,
    ) -> ForecastResult:
        """One forecast call for one envelope."""
        try:
            return await self._forecast_inner(
                household_id=household_id,
                actor_user_id=actor_user_id,
                api_key=api_key,
                envelope_id=envelope_id,
                envelope_name=envelope_name,
                currency=currency,
                time_series=time_series,
                target_amount=target_amount,
                target_date=target_date,
                recent_inflow_average=recent_inflow_average,
            )
        except Exception:
            log.exception(
                "ai.forecast.failed",
                extra={
                    "household_id": str(household_id),
                    "envelope_id": str(envelope_id),
                },
            )
            return ForecastResult(text="", error="Internal AI capability failure.")

    async def _forecast_inner(
        self,
        *,
        household_id: UUID,
        actor_user_id: UUID | None,
        api_key: str | None,
        envelope_id: UUID,
        envelope_name: str,
        currency: str,
        time_series: list[tuple[date, Decimal]],
        target_amount: Decimal | None,
        target_date: date | None,
        recent_inflow_average: Decimal | None,
    ) -> ForecastResult:
        from tulip_storage.models import Household, User

        with self._session_maker() as session:
            household = session.get(Household, household_id)
            if household is None:
                return ForecastResult(text="", error="household not found")
            user_policy: dict[str, object] | None = None
            if actor_user_id is not None:
                user = session.get(User, (household_id, actor_user_id))
                if user is not None:
                    user_policy = user.ai_policy
            policy = resolve_policy(household.ai_policy, user_policy, "forecast")

            if policy.level == "disabled":
                self._audit(
                    household_id=household_id,
                    actor_user_id=actor_user_id,
                    policy_level="disabled",
                    profile=policy.profile,
                    outcome="policy_disabled",
                    prompt_hash=hash_prompt_payload(
                        {"task": "forecast", "envelope_id": str(envelope_id)}
                    ),
                )
                return ForecastResult(text="", error="forecast disabled")

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
                        {"task": "forecast", "envelope_id": str(envelope_id)}
                    ),
                    # H-1 (#234): gate error-path response_text on log_prompts.
                    response_text=(
                        "no api key configured for provider" if policy.log_prompts else None
                    ),
                )
                return ForecastResult(text="", error="no api key")

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
                        {"task": "forecast", "envelope_id": str(envelope_id)}
                    ),
                    response_text=gate.reason[:500] if policy.log_prompts else None,
                )
                return ForecastResult(text="", error=gate.outcome)

        call_provider = gate.provider or ""
        call_model = gate.model or ""
        degraded = gate.degraded
        # Build the prompt outside the session — pure assembly.
        payload = build_forecast_prompt(
            envelope_id=str(envelope_id),
            envelope_name=envelope_name,
            currency=currency,
            time_series=time_series,
            target_amount=target_amount,
            target_date=target_date,
            recent_inflow_average=recent_inflow_average,
            profile=policy.profile,
        )
        messages = _build_messages(payload)
        try:
            response = await self._adapter.chat(
                provider=call_provider,
                model=call_model,
                api_key=api_key if not degraded else None,
                messages=messages,
                max_tokens=200,
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
                prompt_hash=hash_prompt_payload(payload.to_dict()),
                response_text=str(exc)[:500] if policy.log_prompts else None,
            )
            return ForecastResult(text="", error=f"provider error: {exc}")

        self._audit(
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
            prompt_hash=hash_prompt_payload(payload.to_dict()),
            provider_response_id=response.provider_response_id,
            prompt_json=(
                json.dumps(payload.to_dict(), ensure_ascii=False) if policy.log_prompts else None
            ),
            response_text=response.text if policy.log_prompts else None,
        )
        return ForecastResult(text=response.text.strip())

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
    ) -> None:
        """Write one ``ai_invocations`` row in its own session/commit."""
        with self._session_maker() as session:
            AIInvocationWriter(session).write(
                AIInvocationRecord(
                    household_id=household_id,
                    capability="forecast",
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


__all__ = [
    "AIForecastCapability",
    "ForecastPromptPayload",
    "ForecastResult",
    "bucket_time_series",
    "build_forecast_prompt",
]

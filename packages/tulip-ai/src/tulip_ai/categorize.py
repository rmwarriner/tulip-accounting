"""``AICategorizer`` — implements ``tulip_core.reconciliation.Categorizer`` (ADR-0005 §Q3, P6.1).

The flow for each ``categorize()`` call:

1. Open a session; fetch the household's ``ai_policy`` + chart of accounts.
2. Resolve the policy via :func:`tulip_ai.policy.resolve_policy`.
3. If disabled → ``CategorizationResult("Imbalance:Unknown", 0.0)`` with an
   ``ai_invocations`` row stamped ``outcome=policy_disabled``. No provider call.
4. Decrypt the user-or-household API key for the resolved provider.
   No key → same fall-through as ``disabled`` but with
   ``outcome=provider_error`` and a descriptive note.
5. Build the ``CategorizePromptPayload``; run the redactor.
6. Call the adapter; parse the JSON response into a ``CategorizationResult``.
7. Write the audit row; return the result.

Failures (provider down, malformed response, key missing) **never** propagate
into the importer flow — the importer falls back to ``Imbalance:Unknown`` and
the user reviews manually. The audit row records *why* so an operator can
diagnose later.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from tulip_ai._sessions import use_session_or_make_one
from tulip_ai.adapters import ProviderAdapter, ProviderResponse
from tulip_ai.audit import AIInvocationRecord, AIInvocationWriter, hash_prompt_payload
from tulip_ai.cost import PreCallApproval, enforce_pre_call
from tulip_ai.errors import AIProviderError
from tulip_ai.policy import resolve_policy
from tulip_ai.redaction import (
    CategorizePromptPayload,
    ChartEntry,
    PromptRedactor,
)
from tulip_core.reconciliation.categorizer import CategorizationResult
from tulip_storage.encryption import decrypt_field
from tulip_storage.models import Account, AccountType, Household, User

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.orm import Session, sessionmaker

    from tulip_core.reconciliation.categorizer import HouseholdContext
    from tulip_core.reconciliation.statement_line import StatementLine


log = logging.getLogger("tulip_ai.categorize")

_FALLBACK_RESULT = CategorizationResult(account_code="Imbalance:Unknown", confidence=0.0)


@dataclass(slots=True)
class _PromptInputs:
    """What we gathered from the DB to build the prompt + audit the call."""

    household: Household
    user_policy: dict[str, object] | None
    api_key: str | None
    chart: list[ChartEntry]


def build_categorize_prompt(
    line: StatementLine, chart: Sequence[ChartEntry]
) -> CategorizePromptPayload:
    """Pure assembly of the categorize prompt payload.

    Same function the byte-faithful preview surface invokes; testing the
    preview against the live call goes through here.
    """
    return CategorizePromptPayload(
        description=line.description,
        amount=line.amount.amount,
        currency=line.amount.currency,
        posted_date=line.posted_date.isoformat(),
        chart=tuple(chart),
        recent_examples=(),  # P6.1 ships without examples; P6.2 may add them
    )


class AICategorizer:
    """Production ``Categorizer`` implementation; registered at app boot.

    Construction is async-safe (no I/O); per-call work happens inside
    :meth:`categorize`.
    """

    def __init__(
        self,
        *,
        session_maker: sessionmaker[Session],
        master_key: bytes,
        adapter: ProviderAdapter,
    ) -> None:
        """Bind to the session factory, master key, and provider adapter."""
        self._session_maker = session_maker
        self._master_key = master_key
        self._adapter = adapter

    async def categorize(
        self,
        line: StatementLine,
        household_context: HouseholdContext,
        *,
        session: Session | None = None,
    ) -> CategorizationResult:
        """Suggest a category for one statement line.

        ``session`` is the opt-in session-sharing path (#199, #200): callers
        that are mid-transaction (the import-apply flow is the motivating
        case) pass their session so the audit-row write doesn't deadlock
        against the caller's own write lock. Standalone callers pass
        nothing and the capability opens its own session.

        Wide ``except`` is intentional at the outer boundary: importer
        failures from a flaky AI provider — or from any AI-stack bug —
        must not block the whole apply batch. The provider-error branch
        produces an explicit ``ai_invocations`` row; deeper failures
        (decryption, unexpected exception) fall through to a silent
        ``Imbalance:Unknown`` so the apply succeeds and the operator
        gets the signal via structlog.
        """
        try:
            return await self._categorize_inner(line, household_context, session=session)
        except Exception:
            log.exception(
                "ai.categorize.failed",
                extra={"household_id": str(household_context.household_id)},
            )
            return _FALLBACK_RESULT

    async def _categorize_inner(
        self,
        line: StatementLine,
        household_context: HouseholdContext,
        *,
        session: Session | None,
    ) -> CategorizationResult:
        """Inner categorize body — caller wraps in a broad exception guard."""
        with use_session_or_make_one(session, self._session_maker) as (session, should_commit):
            inputs = self._load_inputs(
                session,
                household_context.household_id,
                household_context.acting_user_id,
            )
            if inputs is None:
                # Household vanished mid-call (shouldn't happen) — fall back silently.
                return _FALLBACK_RESULT

            policy = resolve_policy(inputs.household.ai_policy, inputs.user_policy, "categorize")
            writer = AIInvocationWriter(session)

            if policy.level == "disabled":
                payload = build_categorize_prompt(line, inputs.chart)
                writer.write(
                    AIInvocationRecord(
                        household_id=household_context.household_id,
                        capability="categorize",
                        policy_resolved="disabled",
                        profile=policy.profile,
                        outcome="policy_disabled",
                        prompt_hash=hash_prompt_payload(payload.to_dict()),
                    )
                )
                if should_commit:
                    session.commit()
                return _FALLBACK_RESULT

            if inputs.api_key is None and policy.provider != "ollama":
                payload = build_categorize_prompt(line, inputs.chart)
                writer.write(
                    AIInvocationRecord(
                        household_id=household_context.household_id,
                        capability="categorize",
                        policy_resolved=policy.level,
                        profile=policy.profile,
                        provider=policy.provider,
                        model=policy.model,
                        outcome="provider_error",
                        prompt_hash=hash_prompt_payload(payload.to_dict()),
                        # H-1 (#234): gate error-path response_text on log_prompts.
                        response_text=(
                            "no api key configured for provider" if policy.log_prompts else None
                        ),
                    )
                )
                if should_commit:
                    session.commit()
                return _FALLBACK_RESULT

            payload = build_categorize_prompt(line, inputs.chart)
            redactor = PromptRedactor(policy.profile)
            body = redactor.to_message_body(payload)
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a transaction categorizer. Choose one account_code "
                        "from the provided chart and return JSON with "
                        '{"account_code": "<code>", "confidence": <0.0-1.0>}.'
                    ),
                },
                {"role": "user", "content": json.dumps(body, ensure_ascii=False)},
            ]

            gate = enforce_pre_call(
                session,
                household_id=household_context.household_id,
                user_id=household_context.acting_user_id,
                rate_limit_per_hour=policy.rate_limit_per_hour,
                monthly_cost_cap_usd=policy.monthly_cost_cap_usd,
                cost_cap_behaviour=policy.cost_cap_behaviour,
                fallback_provider=policy.fallback_provider,
                fallback_model=policy.fallback_model,
                primary_provider=policy.provider,
                primary_model=policy.model,
            )
            if not isinstance(gate, PreCallApproval):
                writer.write(
                    AIInvocationRecord(
                        household_id=household_context.household_id,
                        capability="categorize",
                        policy_resolved=policy.level,
                        profile=policy.profile,
                        provider=policy.provider,
                        model=policy.model,
                        outcome=gate.outcome,
                        prompt_hash=hash_prompt_payload(body),
                        response_text=gate.reason[:500] if policy.log_prompts else None,
                    )
                )
                if should_commit:
                    session.commit()
                return _FALLBACK_RESULT

            call_provider = gate.provider or ""
            call_model = gate.model or ""
            try:
                response = await self._adapter.chat(
                    provider=call_provider,
                    model=call_model,
                    api_key=inputs.api_key if not gate.degraded else None,
                    messages=messages,
                    max_tokens=200,
                )
            except AIProviderError as exc:
                writer.write(
                    AIInvocationRecord(
                        household_id=household_context.household_id,
                        capability="categorize",
                        policy_resolved=policy.level,
                        profile=policy.profile,
                        provider=call_provider,
                        model=call_model,
                        outcome="provider_error",
                        prompt_hash=hash_prompt_payload(body),
                        response_text=str(exc)[:500] if policy.log_prompts else None,
                    )
                )
                if should_commit:
                    session.commit()
                return _FALLBACK_RESULT

            result = _parse_response(response, fallback_chart=inputs.chart)
            writer.write(
                AIInvocationRecord(
                    household_id=household_context.household_id,
                    capability="categorize",
                    policy_resolved=policy.level,
                    profile=policy.profile,
                    provider=call_provider,
                    model=call_model,
                    tokens_in=response.tokens_in,
                    tokens_out=response.tokens_out,
                    cost_estimate_usd=response.cost_estimate_usd,
                    latency_ms=response.latency_ms,
                    outcome="success",
                    prompt_hash=hash_prompt_payload(body),
                    provider_response_id=response.provider_response_id,
                    prompt_json=(
                        json.dumps(body, ensure_ascii=False) if policy.log_prompts else None
                    ),
                    response_text=response.text if policy.log_prompts else None,
                )
            )
            if should_commit:
                session.commit()
            return result

    def _load_inputs(
        self,
        session: Session,
        household_id: UUID,
        acting_user_id: UUID | None,
    ) -> _PromptInputs | None:
        household = session.get(Household, household_id)
        if household is None:
            return None

        user: User | None = None
        if acting_user_id is not None:
            user = session.get(User, (household_id, acting_user_id))

        # Resolve API key (#239): per-user override > household for the
        # resolved provider. Use the user's key when set for the provider;
        # otherwise fall back to the household's. Note we look up the
        # provider from the household policy here (categorize doesn't yet
        # let users override provider — only severity); a Phase 9 follow-up
        # might revisit if per-user provider routing becomes a thing.
        api_key = self._resolve_api_key(household, user)

        # Chart of expense + income accounts — categorize doesn't propose
        # asset / liability codes per ADR-0005 §Q3.
        rows = (
            session.execute(
                select(Account).where(
                    Account.household_id == household_id,
                    Account.type.in_((AccountType.EXPENSE, AccountType.INCOME)),
                    Account.is_active.is_(True),
                )
            )
            .scalars()
            .all()
        )
        chart = [
            ChartEntry(code=a.code, name=a.name, type=a.type.value)
            for a in rows
            if a.code is not None
        ]
        return _PromptInputs(
            household=household,
            user_policy=user.ai_policy if user is not None else None,
            api_key=api_key,
            chart=chart,
        )

    def _resolve_api_key(self, household: Household, user: User | None) -> str | None:
        """Per-user key overrides household key for the resolved provider (#239)."""
        provider = household.ai_policy.get("default_provider")
        if not isinstance(provider, str):
            return None
        if user is not None and user.ai_keys_encrypted:
            user_key = self._extract_provider_key(user.ai_keys_encrypted, provider)
            if user_key is not None:
                return user_key
        if household.ai_keys_encrypted:
            return self._extract_provider_key(household.ai_keys_encrypted, provider)
        return None

    def _extract_provider_key(self, blob: bytes, provider: str) -> str | None:
        """Decrypt + extract a single provider's key from a ``{provider: key}`` blob."""
        try:
            decrypted = decrypt_field(blob, master_key=self._master_key).decode("utf-8")
            keys_dict = json.loads(decrypted)
        except (ValueError, json.JSONDecodeError):
            return None
        value = keys_dict.get(provider)
        return value if isinstance(value, str) else None


def _parse_response(
    response: ProviderResponse, *, fallback_chart: Sequence[ChartEntry]
) -> CategorizationResult:
    """Parse the provider's JSON response into a ``CategorizationResult``.

    Defensive: if the model returns malformed JSON or an account_code that
    isn't in the chart, we fall back to ``Imbalance:Unknown`` rather than
    propose a code the household doesn't actually have.
    """
    valid_codes = {entry.code for entry in fallback_chart}
    text = response.text.strip()
    # Strip code fences and surrounding chatter — the model sometimes
    # wraps JSON in ```json ... ``` despite the prompt.
    if text.startswith("```"):
        text = text.strip("`")
        # After stripping, the first line may be the language tag.
        if "\n" in text:
            first, _, rest = text.partition("\n")
            if first.strip().lower() in ("json", "javascript"):
                text = rest
    # Find the first {...} block.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return _FALLBACK_RESULT
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return _FALLBACK_RESULT
    code = parsed.get("account_code")
    confidence = parsed.get("confidence")
    if not isinstance(code, str) or code not in valid_codes:
        return _FALLBACK_RESULT
    try:
        conf_value = float(confidence) if confidence is not None else 0.5
    except (TypeError, ValueError):
        conf_value = 0.5
    conf_value = max(0.0, min(1.0, conf_value))
    if conf_value <= 0.0:
        # CategorizationResult requires confidence > 0 per __post_init__;
        # we represent "model uncertain" by falling back rather than passing
        # a sub-threshold value through.
        return _FALLBACK_RESULT
    return CategorizationResult(account_code=code, confidence=conf_value)

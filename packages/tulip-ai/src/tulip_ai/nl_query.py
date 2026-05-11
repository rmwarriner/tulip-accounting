"""``AINLQueryCapability`` — two-turn natural-language query over the AI views (P6.2, ADR-0005 §Q3).

Flow for each ``ask()`` call:

1. **Turn 1** (SQL emission): send ``{question, schema_card}`` + (default
   profile) up to 5 sample rows from each AI view. The model responds with
   one SQL ``SELECT``.
2. **Validate + rewrite** the emitted SQL via
   :func:`tulip_ai.sql_safety.validate_and_rewrite` — single SELECT, only
   allowlisted ``ai_view_*`` tables, household-id-scoped subquery substitution,
   auto-LIMIT 100.
3. **Execute** the rewritten SQL against a read-only SQLite connection.
4. **Redact** the result rows (descriptions stripped of vendor names; amounts
   passed through — the user already knows the numbers and the summary
   needs accurate ones).
5. **Turn 2** (summarisation): send ``{question, redacted_rows}``. The model
   responds with a natural-language summary.
6. **Audit**: one ``ai_invocations`` row per turn (chained by ``request_id``);
   the user gets the raw rows + the summary so they can verify the model's
   reading.

Failures at any step fall back to a structured error response — the
importer-flow philosophy of "never break the caller" doesn't apply here
because the user *is* the caller and they need to know if their question
couldn't be answered.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from tulip_ai.adapters import ProviderAdapter
from tulip_ai.audit import AIInvocationRecord, AIInvocationWriter, hash_prompt_payload
from tulip_ai.cost import PreCallApproval, enforce_pre_call
from tulip_ai.errors import AIProviderError
from tulip_ai.policy import resolve_policy
from tulip_ai.redaction import RedactionProfile
from tulip_ai.sql_safety import UnsafeSQLError, schema_card, validate_and_rewrite

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session, sessionmaker


log = logging.getLogger("tulip_ai.nl_query")


@dataclass(frozen=True, slots=True)
class NLAnswer:
    """Response returned to the caller of ``AINLQueryCapability.ask``."""

    summary: str
    rows: list[dict[str, Any]]
    sql: str | None
    error: str | None = None


# Tokens worth keeping in descriptions even at the short-length threshold
# (mirrors PromptRedactor's strict heuristic for the categorize path; the
# concrete list lives in redaction.py but NL rows pull the same shape).
_KEEP_SHORT = frozenset({"GAS", "ATM", "FEE", "TAX", "BAR", "DMV", "USPS"})
_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")


def _redact_description(description: str | None) -> str:
    """Drop counterparty tokens, keep category-signal tokens (4+ chars or whitelist)."""
    if not description:
        return ""
    tokens: list[str] = []
    for tok in _TOKEN_SPLIT.split(description):
        if not tok:
            continue
        keep = tok.upper() in _KEEP_SHORT or len(tok) >= 4
        tokens.append(tok if keep else "*")
    return " ".join(tokens) if tokens else "(redacted)"


def _redact_row(
    row: dict[str, Any],
    *,
    profile: RedactionProfile,
) -> dict[str, Any]:
    """Per-row redaction before rows ship to the summarisation turn.

    Default + strict both redact the ``description`` column. Amounts and
    dates pass through — they're the structured grounding the summary
    relies on. ``local_only`` skips redaction entirely (local model
    already sees raw data on the way in via the schema card).
    """
    if profile == "local_only":
        return dict(row)
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key == "description":
            out[key] = _redact_description(value)
        elif isinstance(value, Decimal):
            out[key] = str(value)
        else:
            out[key] = value
    return out


def _build_turn1_messages(
    question: str, samples: list[dict[str, Any]] | None
) -> list[dict[str, str]]:
    body: dict[str, Any] = {
        "task": "nl_query.emit_sql",
        "question": question,
        "schema": schema_card(),
    }
    if samples:
        body["sample_rows"] = samples
    return [
        {
            "role": "system",
            "content": (
                "You are a financial-analyst SQL emitter. Given a user "
                "question and the AI view schema, output one SQLite SELECT "
                "statement (no DDL, no DML) that answers the question. "
                "Output the bare SQL only, no fences or commentary. "
                "Only reference tables listed in the schema."
            ),
        },
        {"role": "user", "content": json.dumps(body, ensure_ascii=False)},
    ]


def _build_turn2_messages(
    question: str, redacted_rows: list[dict[str, Any]]
) -> list[dict[str, str]]:
    body = {
        "task": "nl_query.summarise",
        "question": question,
        "result_rows": redacted_rows,
    }
    return [
        {
            "role": "system",
            "content": (
                "You are a financial-analyst summariser. Given a user "
                "question and the result rows of a SQL query, produce a "
                "natural-language answer in 1-3 sentences. Cite specific "
                "amounts. If the rows are empty say so explicitly."
            ),
        },
        {"role": "user", "content": json.dumps(body, ensure_ascii=False)},
    ]


def _extract_sql(text: str) -> str:
    """Pull SQL out of the model's turn-1 response.

    Tolerates code fences and trailing prose; the validator rejects
    anything that isn't a single SELECT after parsing, so heuristic
    extraction is safe.
    """
    text = text.strip()
    # ```sql ... ``` fence handling
    if "```" in text:
        parts = text.split("```")
        # parts[1] is the first fenced block if present
        if len(parts) >= 2:
            block = parts[1]
            if block.lower().startswith("sql\n"):
                block = block[4:]
            elif block.startswith("\n"):
                block = block[1:]
            text = block.strip()
    return text


class AINLQueryCapability:
    """Production NL-query capability; constructed once at app boot."""

    def __init__(
        self,
        *,
        session_maker: sessionmaker[Session],
        adapter: ProviderAdapter,
    ) -> None:
        """Bind to the session factory and the provider adapter."""
        self._session_maker = session_maker
        self._adapter = adapter

    async def ask(
        self,
        question: str,
        *,
        household_id: UUID,
        actor_user_id: UUID | None,
        api_key: str | None,
    ) -> NLAnswer:
        """Answer one question via the two-turn flow."""
        try:
            return await self._ask_inner(
                question,
                household_id=household_id,
                actor_user_id=actor_user_id,
                api_key=api_key,
            )
        except Exception:
            log.exception(
                "ai.nl_query.failed",
                extra={"household_id": str(household_id)},
            )
            return NLAnswer(
                summary="",
                rows=[],
                sql=None,
                error="Internal AI capability failure; see server logs.",
            )

    async def _ask_inner(
        self,
        question: str,
        *,
        household_id: UUID,
        actor_user_id: UUID | None,
        api_key: str | None,
    ) -> NLAnswer:
        from tulip_storage.models import Household

        with self._session_maker() as session:
            household = session.get(Household, household_id)
            if household is None:
                return NLAnswer(summary="", rows=[], sql=None, error="household not found")
            policy = resolve_policy(household.ai_policy, None, "nl_query")
            writer = AIInvocationWriter(session)

            if policy.level == "disabled":
                writer.write(
                    AIInvocationRecord(
                        household_id=household_id,
                        capability="nl_query",
                        policy_resolved="disabled",
                        profile=policy.profile,
                        outcome="policy_disabled",
                        prompt_hash=hash_prompt_payload({"question": question}),
                        actor_user_id=actor_user_id,
                    )
                )
                session.commit()
                return NLAnswer(
                    summary="",
                    rows=[],
                    sql=None,
                    error="NL query is disabled for this household.",
                )

            if api_key is None and policy.provider != "ollama":
                writer.write(
                    AIInvocationRecord(
                        household_id=household_id,
                        capability="nl_query",
                        policy_resolved=policy.level,
                        profile=policy.profile,
                        provider=policy.provider,
                        model=policy.model,
                        outcome="provider_error",
                        prompt_hash=hash_prompt_payload({"question": question}),
                        actor_user_id=actor_user_id,
                        response_text="no api key configured for provider",
                    )
                )
                session.commit()
                return NLAnswer(
                    summary="",
                    rows=[],
                    sql=None,
                    error="No AI key configured for this household.",
                )

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
                writer.write(
                    AIInvocationRecord(
                        household_id=household_id,
                        capability="nl_query",
                        policy_resolved=policy.level,
                        profile=policy.profile,
                        provider=policy.provider,
                        model=policy.model,
                        outcome=gate.outcome,
                        prompt_hash=hash_prompt_payload({"question": question}),
                        actor_user_id=actor_user_id,
                        response_text=gate.reason[:500],
                    )
                )
                session.commit()
                return NLAnswer(
                    summary="",
                    rows=[],
                    sql=None,
                    error=gate.reason,
                )

        call_provider = gate.provider or ""
        call_model = gate.model or ""
        call_api_key = api_key if not gate.degraded else None

        # --- Turn 1: emit SQL ----------------------------------------------
        turn1_msgs = _build_turn1_messages(question, samples=None)
        try:
            turn1 = await self._adapter.chat(
                provider=call_provider,
                model=call_model,
                api_key=call_api_key,
                messages=turn1_msgs,
                max_tokens=400,
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
                prompt_hash=hash_prompt_payload({"turn": 1, "question": question}),
                response_text=str(exc)[:500],
            )
            return NLAnswer(
                summary="", rows=[], sql=None, error=f"Provider error on SQL turn: {exc}"
            )

        emitted_sql = _extract_sql(turn1.text)
        try:
            safe = validate_and_rewrite(emitted_sql, household_id=str(household_id))
        except UnsafeSQLError as exc:
            self._audit(
                household_id=household_id,
                actor_user_id=actor_user_id,
                policy_level=policy.level,
                profile=policy.profile,
                provider=call_provider,
                model=call_model,
                tokens_in=turn1.tokens_in,
                tokens_out=turn1.tokens_out,
                cost_estimate_usd=turn1.cost_estimate_usd,
                latency_ms=turn1.latency_ms,
                outcome="provider_error",
                prompt_hash=hash_prompt_payload(
                    {"turn": 1, "question": question, "sql": emitted_sql}
                ),
                response_text=f"unsafe_sql: {exc}",
            )
            return NLAnswer(
                summary="",
                rows=[],
                sql=emitted_sql,
                error=f"Model emitted unsafe SQL: {exc}",
            )

        self._audit(
            household_id=household_id,
            actor_user_id=actor_user_id,
            policy_level=policy.level,
            profile=policy.profile,
            provider=call_provider,
            model=call_model,
            tokens_in=turn1.tokens_in,
            tokens_out=turn1.tokens_out,
            cost_estimate_usd=turn1.cost_estimate_usd,
            latency_ms=turn1.latency_ms,
            outcome="success",
            prompt_hash=hash_prompt_payload({"turn": 1, "question": question}),
            provider_response_id=turn1.provider_response_id,
            prompt_json=(
                json.dumps({"turn": 1, "sql": emitted_sql}, ensure_ascii=False)
                if policy.log_prompts
                else None
            ),
            response_text=turn1.text if policy.log_prompts else None,
        )

        # --- Execute --------------------------------------------------------
        rows = self._execute_safe(safe.sql, safe.parameters)
        redacted = [_redact_row(r, profile=policy.profile) for r in rows]

        # --- Turn 2: summarise ---------------------------------------------
        turn2_msgs = _build_turn2_messages(question, redacted)
        try:
            turn2 = await self._adapter.chat(
                provider=call_provider,
                model=call_model,
                api_key=call_api_key,
                messages=turn2_msgs,
                max_tokens=400,
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
                prompt_hash=hash_prompt_payload({"turn": 2, "rows_count": len(redacted)}),
                response_text=str(exc)[:500],
            )
            return NLAnswer(
                summary="",
                rows=rows,
                sql=emitted_sql,
                error=f"Provider error on summarise turn: {exc}",
            )

        self._audit(
            household_id=household_id,
            actor_user_id=actor_user_id,
            policy_level=policy.level,
            profile=policy.profile,
            provider=call_provider,
            model=call_model,
            tokens_in=turn2.tokens_in,
            tokens_out=turn2.tokens_out,
            cost_estimate_usd=turn2.cost_estimate_usd,
            latency_ms=turn2.latency_ms,
            outcome="success",
            prompt_hash=hash_prompt_payload({"turn": 2, "rows_count": len(redacted)}),
            provider_response_id=turn2.provider_response_id,
            prompt_json=(
                json.dumps({"turn": 2, "rows": redacted}, ensure_ascii=False)
                if policy.log_prompts
                else None
            ),
            response_text=turn2.text if policy.log_prompts else None,
        )

        return NLAnswer(summary=turn2.text.strip(), rows=rows, sql=emitted_sql)

    def _execute_safe(self, sql: str, params: dict[str, object]) -> list[dict[str, Any]]:
        """Run the rewritten SQL against a fresh read-only session.

        Read-only is enforced by the safety validator + by the rewriter
        only emitting SELECT, not by a DB connection role — SQLite doesn't
        ship per-connection roles, so the validator is the boundary.
        Execution timeout is the default per-statement SQLite ceiling.
        """
        from sqlalchemy import text

        with self._session_maker() as session:
            started = time.monotonic()
            result = session.execute(text(sql), params)
            rows = [dict(row) for row in result.mappings().all()]
            elapsed_ms = int((time.monotonic() - started) * 1000)
            log.info(
                "ai.nl_query.executed",
                extra={"latency_ms": elapsed_ms, "rows": len(rows)},
            )
        return rows

    def _audit(
        self,
        *,
        household_id: UUID,
        actor_user_id: UUID | None,
        policy_level: str,
        profile: str,
        provider: str | None,
        model: str | None,
        outcome: str,
        prompt_hash: bytes,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_estimate_usd: Decimal = Decimal("0"),
        latency_ms: int = 0,
        provider_response_id: str | None = None,
        prompt_json: str | None = None,
        response_text: str | None = None,
    ) -> None:
        """Write one ``ai_invocations`` row in its own session/commit.

        Each turn audits independently so even a turn-2 failure leaves a
        forensic trail for turn 1.
        """
        with self._session_maker() as session:
            AIInvocationWriter(session).write(
                AIInvocationRecord(
                    household_id=household_id,
                    capability="nl_query",
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
    "AINLQueryCapability",
    "NLAnswer",
]

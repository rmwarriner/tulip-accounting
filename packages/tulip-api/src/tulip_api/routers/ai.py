"""HTTP surface for AI features (P6.1, ADR-0005).

Five endpoints in v1:

- ``POST   /v1/ai/keys/{provider}``  — set the household's API key.
- ``DELETE /v1/ai/keys/{provider}``  — forget the household's API key.
- ``GET    /v1/ai/keys``             — list providers that have keys configured.
- ``GET    /v1/ai/status``           — resolved policy summary for the caller.
- ``POST   /v1/ai/preview``          — byte-faithful categorize prompt preview.

The preview endpoint is the per-ADR §Q4 surface that lets a household
admin see exactly what would be sent to the provider, *without* a
network call. Tests assert that the preview output equals what the
live capability would actually send.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, Request, status

from tulip_ai.categorize import build_categorize_prompt
from tulip_ai.policy import resolve_policy
from tulip_ai.redaction import ChartEntry, PromptRedactor
from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.config import Settings, get_settings
from tulip_api.deps import get_session
from tulip_api.errors import UserNotFoundError, problem_response
from tulip_api.schemas.ai import (
    CLEAR_SENTINEL,
    AIAskRequest,
    AIAskResponse,
    AIConfigCapability,
    AIConfigCapabilityPatch,
    AIConfigPatch,
    AIConfigRead,
    AIKeyCreate,
    AIKeysList,
    AIPreviewRequest,
    AIPreviewResponse,
    AIStatusRead,
)
from tulip_core.money import Money
from tulip_core.reconciliation.statement_line import StatementLine
from tulip_storage.encryption import decrypt_field, encrypt_field, field_aad
from tulip_storage.models import Account, AccountType, Household, User
from tulip_storage.repositories import AIInvocationRepository, AuditLogWriter
from tulip_storage.runner.handlers import AI_INVOCATION_RETENTION_DAYS

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_ai import AINLQueryCapability
    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/ai", tags=["ai"])
log = structlog.get_logger("tulip_api.ai")


def _ai_keys_aad(*, table: str, household_id: UUID, row_id: UUID) -> bytes:
    """AAD for an ``ai_keys_encrypted`` blob (#338, M-1)."""
    return field_aad(
        table=table,
        column="ai_keys_encrypted",
        household_id=household_id,
        row_id=row_id,
    )


def _decrypt_keys_blob(blob: bytes | None, master_key: bytes, *, aad: bytes) -> dict[str, str]:
    """Decrypt an ``ai_keys_encrypted`` blob to a ``{provider: key}`` dict.

    Returns ``{}`` for ``None`` / malformed / non-dict ciphertexts. The
    ``aad`` argument binds the ciphertext to its (table, row) identity
    so a swapped blob from a different row / household fails decrypt.
    """
    if not blob:
        return {}
    try:
        decrypted = decrypt_field(blob, master_key=master_key, aad=aad).decode("utf-8")
        parsed = json.loads(decrypted)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, json.JSONDecodeError):
        return {}


def _encrypt_keys_blob(keys: dict[str, str], master_key: bytes, *, aad: bytes) -> bytes | None:
    """Encrypt a ``{provider: key}`` dict; ``None`` when empty."""
    if not keys:
        return None
    return encrypt_field(json.dumps(keys).encode("utf-8"), master_key=master_key, aad=aad)


def _load_household_keys(household: Household, master_key: bytes) -> dict[str, str]:
    """Decrypt ``households.ai_keys_encrypted`` to a ``{provider: key}`` dict."""
    aad = _ai_keys_aad(table="households", household_id=household.id, row_id=household.id)
    return _decrypt_keys_blob(household.ai_keys_encrypted, master_key, aad=aad)


def _store_household_keys(household: Household, keys: dict[str, str], master_key: bytes) -> None:
    """Re-encrypt the ``{provider: key}`` dict back onto the household row."""
    aad = _ai_keys_aad(table="households", household_id=household.id, row_id=household.id)
    household.ai_keys_encrypted = _encrypt_keys_blob(keys, master_key, aad=aad)


def _load_user_keys(user: User, master_key: bytes) -> dict[str, str]:
    """Decrypt ``users.ai_keys_encrypted`` to a ``{provider: key}`` dict (#239)."""
    aad = _ai_keys_aad(table="users", household_id=user.household_id, row_id=user.id)
    return _decrypt_keys_blob(user.ai_keys_encrypted, master_key, aad=aad)


def _store_user_keys(user: User, keys: dict[str, str], master_key: bytes) -> None:
    """Re-encrypt the ``{provider: key}`` dict back onto the user row (#239)."""
    aad = _ai_keys_aad(table="users", household_id=user.household_id, row_id=user.id)
    user.ai_keys_encrypted = _encrypt_keys_blob(keys, master_key, aad=aad)


def _resolve_provider_key(
    *,
    household: Household,
    user: User | None,
    provider: str,
    master_key: bytes,
) -> str | None:
    """Per-user > per-household key precedence for ``provider`` (#239).

    Mirrors the precedence in :class:`tulip_ai.categorize.AICategorizer`:
    if the acting user has set a key for this provider, use it; otherwise
    fall back to the household-level key.
    """
    if user is not None and user.ai_keys_encrypted:
        user_keys = _load_user_keys(user, master_key)
        if provider in user_keys:
            return user_keys[provider]
    if household.ai_keys_encrypted:
        return _load_household_keys(household, master_key).get(provider)
    return None


@router.post(
    "/keys/{provider}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: problem_response("request.body_invalid"),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        422: problem_response("validation.failed"),
    },
)
def set_ai_key(
    provider: str,
    body: AIKeyCreate,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> None:
    """Upload or replace the household's API key for ``provider``."""
    household = session.get(Household, claims.household_id)
    assert household is not None  # noqa: S101 — authenticated households always exist
    keys = _load_household_keys(household, settings.master_key)
    keys[provider] = body.api_key
    _store_household_keys(household, keys, settings.master_key)
    session.commit()
    log.info("ai.key_set", provider=provider, household_id=str(claims.household_id))


@router.delete(
    "/keys/{provider}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
    },
)
def forget_ai_key(
    provider: str,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> None:
    """Remove the household's API key for ``provider``. Idempotent on missing keys."""
    household = session.get(Household, claims.household_id)
    assert household is not None  # noqa: S101
    keys = _load_household_keys(household, settings.master_key)
    keys.pop(provider, None)
    _store_household_keys(household, keys, settings.master_key)
    session.commit()
    log.info("ai.key_forgotten", provider=provider, household_id=str(claims.household_id))


@router.get(
    "/keys",
    response_model=AIKeysList,
    responses={401: problem_response("auth.unauthorized")},
)
def list_ai_keys(
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> AIKeysList:
    """List provider names for which the household has a key set (no key values)."""
    household = session.get(Household, claims.household_id)
    assert household is not None  # noqa: S101
    keys = _load_household_keys(household, settings.master_key)
    return AIKeysList(providers=sorted(keys.keys()))


def _write_consent_audit(
    *,
    session: Session,
    claims: Claims,
    request: Request,
    before: dict[str, object],
    after: dict[str, object],
) -> None:
    """Record an ``ai.consent_changed`` audit row on household policy mutation (#247).

    GDPR Art. 7(1) needs "when and by whom" answerable. Skipped on no-op
    PUTs (``before == after``) so a client that fetches+puts the same
    blob doesn't fill the log with noise.
    """
    if before == after:
        return
    AuditLogWriter(session, claims.household_id).write(
        action="ai.consent_changed",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="household",
        entity_id=claims.household_id,
        before=before,
        after=after,
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


def _set_user_key(
    *,
    session: Session,
    actor_claims: Claims,
    target_user: User,
    provider: str,
    api_key: str,
    master_key: bytes,
    request: Request,
) -> None:
    """Upload or replace ``target_user``'s key for ``provider`` (#239)."""
    keys = _load_user_keys(target_user, master_key)
    keys[provider] = api_key
    _store_user_keys(target_user, keys, master_key)
    AuditLogWriter(session, actor_claims.household_id).write(
        action="user.ai_key_set",
        actor_kind="user",
        actor_user_id=actor_claims.user_id,
        entity_type="user",
        entity_id=target_user.id,
        metadata={"provider": provider},
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    log.info(
        "ai.user_key_set",
        provider=provider,
        user_id=str(target_user.id),
        household_id=str(actor_claims.household_id),
    )


def _forget_user_key(
    *,
    session: Session,
    actor_claims: Claims,
    target_user: User,
    provider: str,
    master_key: bytes,
    request: Request,
) -> None:
    """Remove ``target_user``'s key for ``provider``. Idempotent (#239)."""
    keys = _load_user_keys(target_user, master_key)
    keys.pop(provider, None)
    _store_user_keys(target_user, keys, master_key)
    AuditLogWriter(session, actor_claims.household_id).write(
        action="user.ai_key_forgotten",
        actor_kind="user",
        actor_user_id=actor_claims.user_id,
        entity_type="user",
        entity_id=target_user.id,
        metadata={"provider": provider},
        request_id=_request_uuid(request),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    log.info(
        "ai.user_key_forgotten",
        provider=provider,
        user_id=str(target_user.id),
        household_id=str(actor_claims.household_id),
    )


@router.post(
    "/keys/me/{provider}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: problem_response("request.body_invalid"),
        401: problem_response("auth.unauthorized"),
        422: problem_response("validation.failed"),
    },
)
def set_own_ai_key(
    provider: str,
    body: AIKeyCreate,
    request: Request,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> None:
    """Upload or replace the caller's own per-user key for ``provider`` (#239)."""
    user = session.get(User, (claims.household_id, claims.user_id))
    if user is None:
        raise UserNotFoundError()
    _set_user_key(
        session=session,
        actor_claims=claims,
        target_user=user,
        provider=provider,
        api_key=body.api_key,
        master_key=settings.master_key,
        request=request,
    )


@router.delete(
    "/keys/me/{provider}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={401: problem_response("auth.unauthorized")},
)
def forget_own_ai_key(
    provider: str,
    request: Request,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> None:
    """Remove the caller's own per-user key for ``provider``. Idempotent (#239)."""
    user = session.get(User, (claims.household_id, claims.user_id))
    if user is None:
        raise UserNotFoundError()
    _forget_user_key(
        session=session,
        actor_claims=claims,
        target_user=user,
        provider=provider,
        master_key=settings.master_key,
        request=request,
    )


@router.get(
    "/keys/me",
    response_model=AIKeysList,
    responses={401: problem_response("auth.unauthorized")},
)
def list_own_ai_keys(
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> AIKeysList:
    """List providers for which the caller has a per-user key (#239). No key values."""
    user = session.get(User, (claims.household_id, claims.user_id))
    if user is None:
        raise UserNotFoundError()
    keys = _load_user_keys(user, settings.master_key)
    return AIKeysList(providers=sorted(keys.keys()))


@router.post(
    "/keys/users/{user_id}/{provider}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: problem_response("request.body_invalid"),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("user.not_found"),
        422: problem_response("validation.failed"),
    },
)
def set_user_ai_key(
    user_id: UUID,
    provider: str,
    body: AIKeyCreate,
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> None:
    """Admin: upload or replace another user's per-user key (#239)."""
    user = session.get(User, (claims.household_id, user_id))
    if user is None:
        raise UserNotFoundError()
    _set_user_key(
        session=session,
        actor_claims=claims,
        target_user=user,
        provider=provider,
        api_key=body.api_key,
        master_key=settings.master_key,
        request=request,
    )


@router.delete(
    "/keys/users/{user_id}/{provider}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("user.not_found"),
    },
)
def forget_user_ai_key(
    user_id: UUID,
    provider: str,
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> None:
    """Admin: remove another user's per-user key. Idempotent (#239)."""
    user = session.get(User, (claims.household_id, user_id))
    if user is None:
        raise UserNotFoundError()
    _forget_user_key(
        session=session,
        actor_claims=claims,
        target_user=user,
        provider=provider,
        master_key=settings.master_key,
        request=request,
    )


@router.get(
    "/keys/users/{user_id}",
    response_model=AIKeysList,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("user.not_found"),
    },
)
def list_user_ai_keys(
    user_id: UUID,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> AIKeysList:
    """Admin: list providers for which a user has a per-user key (#239)."""
    user = session.get(User, (claims.household_id, user_id))
    if user is None:
        raise UserNotFoundError()
    keys = _load_user_keys(user, settings.master_key)
    return AIKeysList(providers=sorted(keys.keys()))


@router.get(
    "/status",
    response_model=AIStatusRead,
    responses={401: problem_response("auth.unauthorized")},
)
def get_ai_status(
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> AIStatusRead:
    """Resolved policy summary for the caller's household.

    P6.5.b: includes ``cost_cap_behaviour``, ``rate_limit_per_hour``,
    fallback fields, and (when a cap is configured) the month-to-date
    AI spend so operators can see how close they are to ``degrade`` /
    ``hard_fail``.
    """
    from decimal import Decimal

    from tulip_ai.cost import check_cost_cap

    household = session.get(Household, claims.household_id)
    assert household is not None  # noqa: S101
    user = session.get(User, (claims.household_id, claims.user_id))
    user_policy = user.ai_policy if user is not None else None
    keys = _load_household_keys(household, settings.master_key)
    capabilities: dict[str, dict[str, str | None]] = {}
    for cap in ("categorize", "nl_query", "forecast", "agentic"):
        resolved = resolve_policy(household.ai_policy, user_policy, cap)
        capabilities[cap] = {
            "level": resolved.level,
            "provider": resolved.provider,
            "model": resolved.model,
            "profile": resolved.profile,
        }
    cat_policy = resolve_policy(household.ai_policy, user_policy, "categorize")

    mtd: Decimal | None = None
    if cat_policy.monthly_cost_cap_usd is not None:
        decision = check_cost_cap(
            session,
            household_id=claims.household_id,
            estimated_cost_usd=Decimal("0"),
            monthly_cap_usd=cat_policy.monthly_cost_cap_usd,
        )
        mtd = decision.spent_so_far_usd

    return AIStatusRead(
        default_provider=household.ai_policy.get("default_provider"),
        default_model=household.ai_policy.get("default_model"),
        monthly_cost_cap_usd=cat_policy.monthly_cost_cap_usd,
        cost_cap_behaviour=cat_policy.cost_cap_behaviour,
        rate_limit_per_hour=cat_policy.rate_limit_per_hour,
        fallback_provider=cat_policy.fallback_provider,
        fallback_model=cat_policy.fallback_model,
        log_prompts=cat_policy.log_prompts,
        capabilities=capabilities,
        providers_with_keys=sorted(keys.keys()),
        month_to_date_spend_usd=mtd,
    )


@router.post(
    "/preview",
    response_model=AIPreviewResponse,
    responses={
        400: problem_response("request.body_invalid"),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        422: problem_response("validation.failed"),
    },
)
def preview_categorize_prompt(
    body: AIPreviewRequest,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> AIPreviewResponse:
    """Return the byte-faithful redacted prompt body for a categorize call.

    Same construction path as the live call — the test
    ``test_preview_byte_faithful`` asserts the output equals what the
    capability would actually send.
    """
    household = session.get(Household, claims.household_id)
    assert household is not None  # noqa: S101

    # Build a synthetic statement line. Same dataclass the live flow uses.
    line = StatementLine(
        id=uuid4(),
        import_batch_id=uuid4(),
        line_number=1,
        posted_date=body.posted_date,
        amount=Money(body.amount, body.currency),
        description=body.description,
    )

    # Chart = household's active expense + income accounts.
    from sqlalchemy import select

    rows = (
        session.execute(
            select(Account).where(
                Account.household_id == claims.household_id,
                Account.type.in_((AccountType.EXPENSE, AccountType.INCOME)),
                Account.is_active.is_(True),
            )
        )
        .scalars()
        .all()
    )
    chart = tuple(
        ChartEntry(code=a.code, name=a.name, type=a.type.value) for a in rows if a.code is not None
    )

    user = session.get(User, (claims.household_id, claims.user_id))
    user_policy = user.ai_policy if user is not None else None
    policy = resolve_policy(household.ai_policy, user_policy, "categorize")
    payload = build_categorize_prompt(line, chart)
    redactor = PromptRedactor(policy.profile)
    body_dict = redactor.to_message_body(payload)
    return AIPreviewResponse(
        profile=policy.profile,
        provider=policy.provider,
        model=policy.model,
        payload=body_dict,
    )


@router.post(
    "/ask",
    response_model=AIAskResponse,
    responses={
        400: problem_response("request.body_invalid"),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        422: problem_response("validation.failed"),
    },
)
async def ask_nl_query(
    body: AIAskRequest,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    capability: AINLQueryCapability | None = Depends(lambda: None),  # noqa: B008
) -> AIAskResponse:
    """Run a natural-language query through the two-turn AI flow (P6.2).

    The ``capability`` dependency is ``None`` by default — production
    derives it from the request's session + a ``LitellmAdapter`` below.
    Tests override the dependency with a capability bound to a
    ``RecordingAdapter`` so no real provider call fires.
    """
    from sqlalchemy.orm import sessionmaker as _sessionmaker

    from tulip_ai import AINLQueryCapability as _AINLQueryCapability
    from tulip_ai import LitellmAdapter

    # Resolve the API key — per-user override > household (#239).
    household = session.get(Household, claims.household_id)
    assert household is not None  # noqa: S101
    user = session.get(User, (claims.household_id, claims.user_id))
    api_key: str | None = None
    provider = household.ai_policy.get("default_provider")
    if isinstance(provider, str):
        api_key = _resolve_provider_key(
            household=household,
            user=user,
            provider=provider,
            master_key=settings.master_key,
        )

    if capability is None:
        bind = session.get_bind()
        cap_session_maker = _sessionmaker(bind, expire_on_commit=False)
        capability = _AINLQueryCapability(session_maker=cap_session_maker, adapter=LitellmAdapter())

    answer = await capability.ask(
        body.question,
        household_id=claims.household_id,
        actor_user_id=claims.user_id,
        api_key=api_key,
    )
    return AIAskResponse(
        summary=answer.summary,
        rows=[dict(r) for r in answer.rows],
        sql=answer.sql,
        error=answer.error,
    )


# --- P6.5.b: config editor ------------------------------------------------


def _cap_overrides(ai_policy: dict[str, object], capability: str) -> AIConfigCapability:
    """Extract one capability's per-capability overrides from ``ai_policy``."""
    capabilities = ai_policy.get("capabilities") or {}
    if not isinstance(capabilities, dict):
        capabilities = {}
    settings = capabilities.get(capability) or {}
    if not isinstance(settings, dict):
        settings = {}

    def _str_or_none(value: object) -> str | None:
        return value if isinstance(value, str) else None

    return AIConfigCapability(
        policy=_str_or_none(settings.get("policy")),
        provider=_str_or_none(settings.get("provider")),
        model=_str_or_none(settings.get("model")),
        profile=_str_or_none(settings.get("profile")),
    )


def _coerce_cap(value: object) -> object:
    """Best-effort coerce ``monthly_cost_cap_usd`` from JSON for the read model."""
    if value is None or value == "":
        return None
    try:
        from decimal import Decimal

        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


@router.get(
    "/config",
    response_model=AIConfigRead,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
    },
)
def get_ai_config(
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> AIConfigRead:
    """Return the raw household-level ``ai_policy`` shape + per-capability overrides.

    Admin-only. For the fully-resolved per-capability view, see
    ``GET /v1/ai/status``.
    """
    household = session.get(Household, claims.household_id)
    assert household is not None  # noqa: S101
    ai_policy = household.ai_policy or {}

    def _get_str(key: str) -> str | None:
        value = ai_policy.get(key)
        return value if isinstance(value, str) else None

    behaviour = ai_policy.get("cost_cap_behaviour")
    behaviour_value: str = behaviour if behaviour in ("degrade", "hard_fail") else "degrade"

    rate = ai_policy.get("rate_limit_per_hour")
    rate_value: int = rate if isinstance(rate, int) and rate > 0 else 60

    return AIConfigRead(
        default_provider=_get_str("default_provider"),
        default_model=_get_str("default_model"),
        profile=_get_str("profile"),
        monthly_cost_cap_usd=_coerce_cap(ai_policy.get("monthly_cost_cap_usd")),  # type: ignore[arg-type]
        cost_cap_behaviour=behaviour_value,  # type: ignore[arg-type]
        rate_limit_per_hour=rate_value,
        fallback_provider=_get_str("fallback_provider"),
        fallback_model=_get_str("fallback_model"),
        log_prompts=bool(ai_policy.get("log_prompts", False)),
        invocation_retention_days=AI_INVOCATION_RETENTION_DAYS,
        capabilities={
            cap: _cap_overrides(ai_policy, cap)
            for cap in ("categorize", "nl_query", "forecast", "agentic")
        },
    )


def _apply_household_patch(ai_policy: dict[str, object], patch: AIConfigPatch) -> dict[str, object]:
    """Mutate-and-return ``ai_policy`` with the patch applied.

    Each non-None field on ``patch`` is applied; the sentinel
    ``CLEAR_SENTINEL`` (or empty string for ``monthly_cost_cap_usd``)
    removes the key.
    """
    if patch.default_provider is not None:
        if patch.default_provider == CLEAR_SENTINEL:
            ai_policy.pop("default_provider", None)
        else:
            ai_policy["default_provider"] = patch.default_provider
    if patch.default_model is not None:
        if patch.default_model == CLEAR_SENTINEL:
            ai_policy.pop("default_model", None)
        else:
            ai_policy["default_model"] = patch.default_model
    if patch.profile is not None:
        ai_policy["profile"] = patch.profile
    if patch.monthly_cost_cap_usd is not None:
        if patch.monthly_cost_cap_usd in (CLEAR_SENTINEL, ""):
            ai_policy.pop("monthly_cost_cap_usd", None)
        else:
            ai_policy["monthly_cost_cap_usd"] = patch.monthly_cost_cap_usd
    if patch.cost_cap_behaviour is not None:
        ai_policy["cost_cap_behaviour"] = patch.cost_cap_behaviour
    if patch.rate_limit_per_hour is not None:
        ai_policy["rate_limit_per_hour"] = patch.rate_limit_per_hour
    if patch.fallback_provider is not None:
        if patch.fallback_provider == CLEAR_SENTINEL:
            ai_policy.pop("fallback_provider", None)
        else:
            ai_policy["fallback_provider"] = patch.fallback_provider
    if patch.fallback_model is not None:
        if patch.fallback_model == CLEAR_SENTINEL:
            ai_policy.pop("fallback_model", None)
        else:
            ai_policy["fallback_model"] = patch.fallback_model
    if patch.log_prompts is not None:
        ai_policy["log_prompts"] = patch.log_prompts
    return ai_policy


@router.put(
    "/config",
    response_model=AIConfigRead,
    responses={
        400: problem_response("request.body_invalid"),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        422: problem_response("validation.failed"),
    },
)
def put_ai_config(
    body: AIConfigPatch,
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> AIConfigRead:
    """Patch the household-level ``ai_policy`` shape (admin-only).

    Each field is optional. Pass the sentinel ``"__CLEAR__"`` (or empty
    string for the decimal cap) to remove a key. Unknown keys are
    rejected by Pydantic with a 422.
    """
    household = session.get(Household, claims.household_id)
    assert household is not None  # noqa: S101
    before_policy: dict[str, object] = dict(household.ai_policy or {})
    was_logging = bool(before_policy.get("log_prompts", False))
    after_policy = _apply_household_patch(dict(before_policy), body)
    household.ai_policy = after_policy
    now_logging = bool(after_policy.get("log_prompts", False))
    _write_consent_audit(
        session=session,
        claims=claims,
        request=request,
        before=before_policy,
        after=after_policy,
    )
    if was_logging and not now_logging:
        # GDPR Art. 17(1)(b): withdrawing log_prompts consent retroactively
        # scrubs the prompt + response bodies logged while it was on. The
        # scrub is atomic with the policy change — same commit (#243).
        scrubbed = AIInvocationRepository(session, claims.household_id).scrub_prompt_logs()
        AuditLogWriter(session, claims.household_id).write(
            action="ai.prompt_log_scrubbed",
            actor_kind="user",
            actor_user_id=claims.user_id,
            entity_type="household",
            entity_id=claims.household_id,
            before={"log_prompts": True},
            after={"log_prompts": False, "rows_scrubbed": scrubbed},
        )
    session.commit()
    log.info("ai.config_set", household_id=str(claims.household_id))
    return get_ai_config(claims=claims, session=session)


@router.put(
    "/config/capabilities/{capability}",
    response_model=AIConfigRead,
    responses={
        400: problem_response("request.body_invalid"),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        422: problem_response("validation.failed"),
    },
)
def put_ai_capability_config(
    capability: str,
    body: AIConfigCapabilityPatch,
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> AIConfigRead:
    """Patch one capability's override under ``ai_policy.capabilities[capability]``.

    Capability name must be one of ``categorize / nl_query / forecast /
    agentic``. Unknown capabilities return 422.
    """
    if capability not in ("categorize", "nl_query", "forecast", "agentic"):
        # FastAPI surfaces this as a validation error via Pydantic in
        # the schema layer normally; this is the path-param case.
        from tulip_api.errors import ValidationFailedError

        raise ValidationFailedError(
            errors=[
                {
                    "type": "literal_error",
                    "loc": ["path", "capability"],
                    "msg": "must be one of categorize/nl_query/forecast/agentic",
                    "input": capability,
                }
            ]
        )

    # Value-space validation — handler-side because the patch model
    # accepts ``str`` to share the ``__CLEAR__`` sentinel slot.
    _VALID_POLICY = {"permissive", "requires_approval", "disabled", CLEAR_SENTINEL}
    _VALID_PROFILE = {"default", "strict", "local_only", CLEAR_SENTINEL}
    if body.policy is not None and body.policy not in _VALID_POLICY:
        from tulip_api.errors import ValidationFailedError

        raise ValidationFailedError(
            errors=[
                {
                    "type": "literal_error",
                    "loc": ["body", "policy"],
                    "msg": "must be one of permissive/requires_approval/disabled",
                    "input": body.policy,
                }
            ]
        )
    if body.profile is not None and body.profile not in _VALID_PROFILE:
        from tulip_api.errors import ValidationFailedError

        raise ValidationFailedError(
            errors=[
                {
                    "type": "literal_error",
                    "loc": ["body", "profile"],
                    "msg": "must be one of default/strict/local_only",
                    "input": body.profile,
                }
            ]
        )

    household = session.get(Household, claims.household_id)
    assert household is not None  # noqa: S101
    before_policy: dict[str, object] = dict(household.ai_policy or {})
    ai_policy: dict[str, object] = dict(before_policy)
    raw_caps = ai_policy.get("capabilities") or {}
    capabilities: dict[str, object] = dict(raw_caps) if isinstance(raw_caps, dict) else {}
    raw_settings = capabilities.get(capability) or {}
    cap_settings: dict[str, object] = dict(raw_settings) if isinstance(raw_settings, dict) else {}

    fields = {
        "policy": body.policy,
        "provider": body.provider,
        "model": body.model,
        "profile": body.profile,
    }
    for key, value in fields.items():
        if value is None:
            continue
        if value == CLEAR_SENTINEL:
            cap_settings.pop(key, None)
        else:
            cap_settings[key] = value

    if cap_settings:
        capabilities[capability] = cap_settings
    else:
        capabilities.pop(capability, None)
    if capabilities:
        ai_policy["capabilities"] = capabilities
    else:
        ai_policy.pop("capabilities", None)

    household.ai_policy = ai_policy
    _write_consent_audit(
        session=session,
        claims=claims,
        request=request,
        before=before_policy,
        after=ai_policy,
    )
    session.commit()
    log.info(
        "ai.capability_config_set",
        household_id=str(claims.household_id),
        capability=capability,
    )
    return get_ai_config(claims=claims, session=session)


def _request_uuid(request: Request) -> UUID | None:
    rid = request.headers.get("x-request-id")
    if rid:
        try:
            return UUID(rid)
        except ValueError:
            return None
    return None


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


__all__ = ["router"]

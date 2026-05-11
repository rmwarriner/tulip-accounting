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
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, status

from tulip_ai.categorize import build_categorize_prompt
from tulip_ai.policy import resolve_policy
from tulip_ai.redaction import ChartEntry, PromptRedactor
from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.config import Settings, get_settings
from tulip_api.deps import get_session
from tulip_api.errors import problem_response
from tulip_api.schemas.ai import (
    AIKeyCreate,
    AIKeysList,
    AIPreviewRequest,
    AIPreviewResponse,
    AIStatusRead,
)
from tulip_core.money import Money
from tulip_core.reconciliation.statement_line import StatementLine
from tulip_storage.encryption import decrypt_field, encrypt_field
from tulip_storage.models import Account, AccountType, Household

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/ai", tags=["ai"])
log = structlog.get_logger("tulip_api.ai")


def _load_household_keys(household: Household, master_key: bytes) -> dict[str, str]:
    """Decrypt ``households.ai_keys_encrypted`` to a ``{provider: key}`` dict."""
    if not household.ai_keys_encrypted:
        return {}
    try:
        decrypted = decrypt_field(household.ai_keys_encrypted, master_key=master_key).decode(
            "utf-8"
        )
        parsed = json.loads(decrypted)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, json.JSONDecodeError):
        return {}


def _store_household_keys(household: Household, keys: dict[str, str], master_key: bytes) -> None:
    """Re-encrypt the ``{provider: key}`` dict back onto the household row."""
    if not keys:
        household.ai_keys_encrypted = None
        return
    blob = encrypt_field(json.dumps(keys).encode("utf-8"), master_key=master_key)
    household.ai_keys_encrypted = blob


@router.post(
    "/keys/{provider}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
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
    """Resolved policy summary for the caller's household."""
    household = session.get(Household, claims.household_id)
    assert household is not None  # noqa: S101
    keys = _load_household_keys(household, settings.master_key)
    capabilities: dict[str, dict[str, str | None]] = {}
    for cap in ("categorize", "nl_query", "forecast", "agentic"):
        resolved = resolve_policy(household.ai_policy, None, cap)
        capabilities[cap] = {
            "level": resolved.level,
            "provider": resolved.provider,
            "model": resolved.model,
            "profile": resolved.profile,
        }
    return AIStatusRead(
        default_provider=household.ai_policy.get("default_provider"),
        default_model=household.ai_policy.get("default_model"),
        monthly_cost_cap_usd=resolve_policy(
            household.ai_policy, None, "categorize"
        ).monthly_cost_cap_usd,
        log_prompts=bool(household.ai_policy.get("log_prompts", False)),
        capabilities=capabilities,
        providers_with_keys=sorted(keys.keys()),
    )


@router.post(
    "/preview",
    response_model=AIPreviewResponse,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
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

    policy = resolve_policy(household.ai_policy, None, "categorize")
    payload = build_categorize_prompt(line, chart)
    redactor = PromptRedactor(policy.profile)
    body_dict = redactor.to_message_body(payload)
    return AIPreviewResponse(
        profile=policy.profile,
        provider=policy.provider,
        model=policy.model,
        payload=body_dict,
    )


__all__ = ["router"]

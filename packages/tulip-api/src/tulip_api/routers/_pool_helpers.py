"""Shared helpers for the envelopes / sinking_funds / pools routers.

Centralizes:

- Visibility filtering for pools (mirrors :func:`tulip_api.routers.accounts._filter_for_role`).
- Pool-active assertions and currency-match assertions (used by transfer).
- The ``post_user_initiated_shadow_tx`` helper that builds, validates, saves,
  and audits a stand-alone shadow transaction (refill, transfer,
  budget-inflow). Per ADR-0001, these have ``paired_main_tx_id=None`` since
  no main-ledger transaction triggered them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from tulip_api.errors import (
    ForbiddenError,
    PoolCurrencyMismatchError,
    PoolInactiveError,
    PoolNotFoundError,
)
from tulip_core.allocation import (
    InactivePoolError,
    ShadowPosting,
    ShadowTransaction,
    ShadowTxReason,
    ShadowTxStatus,
    UnknownPoolError,
    post_shadow_transaction,
)
from tulip_core.allocation import (
    Pool as DomainPool,
)
from tulip_core.allocation import (
    PoolCurrencyMismatchError as DomainPoolCurrencyMismatchError,
)
from tulip_core.allocation import (
    PoolType as DomainPoolType,
)
from tulip_core.money import Money
from tulip_storage.repositories import (
    AllocationPoolRepository,
    AuditLogWriter,
    ShadowTransactionRepository,
)

if TYPE_CHECKING:
    from datetime import date as date_type
    from decimal import Decimal

    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims
    from tulip_storage.models import AllocationPool


def filter_for_role(pool: AllocationPool, claims: Claims) -> bool:
    """Return True iff ``claims`` may see ``pool``.

    Mirrors the account visibility rule: shared visible to all; private
    visible to admins and the original creator only. System pools are
    always shared, so this collapses to True for them.
    """
    if pool.visibility == "shared":
        return True
    if claims.role == "admin":
        return True
    return pool.created_by_user_id == claims.user_id


def require_visibility_or_forbid(pool: AllocationPool, claims: Claims) -> None:
    """Raise ``ForbiddenError`` if ``claims`` cannot see / act on this private pool."""
    if pool.visibility == "private" and not filter_for_role(pool, claims):
        raise ForbiddenError(
            "Members can only act on private pools they created themselves. "
            "Ask an admin or the original creator to perform this action."
        )


def resolve_or_lazy_create_system_pool(
    pool_repo: AllocationPoolRepository,
    *,
    pool_type: DomainPoolType,
    currency: str,
) -> AllocationPool:
    """Return the household's system pool of ``pool_type`` for ``currency``.

    Calls ``get_or_create_system_pools`` (idempotent) so the three system
    pools for the currency materialize together if any are missing. The
    domain :class:`PoolType` and the storage :class:`PoolType` share enum
    values; we cross the seam by going through the storage enum here.
    """
    from tulip_storage.models import PoolType as StoragePoolType

    sys_pools = pool_repo.get_or_create_system_pools(currency=currency)
    return sys_pools[StoragePoolType(pool_type.value)]


def post_user_initiated_shadow_tx(
    *,
    session: Session,
    claims: Claims,
    request_id: UUID | None,
    description: str,
    reason: ShadowTxReason,
    tx_date: date_type,
    legs: list[tuple[AllocationPool, Decimal]],
    memo: str | None = None,
) -> ShadowTransaction:
    """Build and persist a stand-alone shadow transaction.

    ``legs`` is a list of ``(AllocationPool, signed_amount)`` tuples. The
    helper:

    1. Constructs a domain ``ShadowTransaction`` (POSTED status — its
       ``__post_init__`` enforces sum-to-zero per currency).
    2. Calls :func:`post_shadow_transaction` with the household's pools so
       active / currency / balance checks all run.
    3. Persists via :class:`ShadowTransactionRepository.save_balanced` (which
       runs the PENDING-then-UPDATE flow that fires the balance trigger).
    4. Writes one audit row with ``entity_type="shadow_transaction"``.

    The caller commits the session — the helper only flushes — so refill /
    transfer / budget-inflow stay atomic with any other work in the
    handler. Engine errors are mapped here to user-facing Problem Details
    (``pool.not_found`` / ``pool.inactive`` / ``pool.currency_mismatch``);
    ``UnbalancedShadowTransactionError`` is re-raised for the caller to
    map per its context (it should not normally surface — ``legs`` should
    sum to zero by construction).

    Args:
        session: SQLAlchemy session (mutated in place).
        claims: caller identity for audit + tenant scope.
        request_id: incoming X-Request-Id (for audit correlation).
        description: human-facing description for the shadow tx.
        reason: ``ShadowTxReason`` (refill / transfer / budget_inflow / …).
        tx_date: transaction date (mirrors main-ledger pattern).
        legs: list of (pool, signed_amount) — must sum to zero per currency.
        memo: optional per-leg memo applied to all legs.

    Returns:
        The persisted shadow transaction.

    Raises:
        PoolNotFoundError / PoolInactiveError / PoolCurrencyMismatchError
            mapped from engine errors.
        UnbalancedShadowTransactionError if ``legs`` don't sum to zero.

    """
    domain_postings = tuple(
        ShadowPosting(
            id=uuid4(),
            pool_id=pool.id,
            amount=Money(amount, pool.currency),
            memo=memo,
        )
        for pool, amount in legs
    )
    shadow_tx = ShadowTransaction(
        id=uuid4(),
        household_id=claims.household_id,
        date=tx_date,
        description=description,
        reason=reason,
        postings=domain_postings,
        status=ShadowTxStatus.POSTED,
        paired_main_tx_id=None,
        created_by_user_id=claims.user_id,
    )

    # Validate against the household's pool set. The pool repo loads them
    # fresh; the repo wraps the same Session so flushes propagate.
    pool_repo = AllocationPoolRepository(session, claims.household_id)
    domain_pools = [
        DomainPool(
            id=p.id,
            household_id=p.household_id,
            pool_type=DomainPoolType(p.pool_type.value),
            name=p.name,
            currency=p.currency,
            visibility=p.visibility,
            is_active=p.is_active,
            is_system=p.is_system,
        )
        for p in pool_repo.list_active()
    ]
    try:
        validated = post_shadow_transaction(shadow_tx, pools=domain_pools)
    except UnknownPoolError as exc:
        # Surface the missing pool's id rather than swallowing the message.
        # The message contains "unknown pool <uuid>"; extract or re-format.
        raise PoolNotFoundError(pool_id=str(_extract_pool_id(str(exc)))) from exc
    except InactivePoolError as exc:
        raise PoolInactiveError(pool_id=str(_extract_pool_id(str(exc)))) from exc
    except DomainPoolCurrencyMismatchError as exc:
        # Engine messages identify the pool + currency mismatch.
        # Surface the pool id; full context is in the engine message.
        # Caller can map this further if it has more context (e.g. transfer).
        raise PoolCurrencyMismatchError(
            pool_id="<unknown>",
            pool_currency="<see detail>",
            posting_currency="<see detail>",
        ) from exc

    repo = ShadowTransactionRepository(session, claims.household_id)
    saved = repo.save_balanced(validated)

    AuditLogWriter(session, claims.household_id).write(
        action="create",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="shadow_transaction",
        entity_id=saved.id,
        after={
            "reason": reason.value,
            "description": description,
            "date": tx_date.isoformat(),
            "status": "posted",
        },
        request_id=request_id,
    )
    # Set posted_at to the wall-clock time at promotion. The repo defaults
    # this to UTC now() when the storage enum is POSTED, so the field is
    # already populated; setting it again here is a no-op for parity.
    if saved.posted_at is None:
        saved.posted_at = datetime.now(tz=UTC)
    return validated


def _extract_pool_id(message: str) -> str:
    """Best-effort scrape of a UUID from an engine error message.

    The engine messages are formatted as "... pool <uuid> ..."; this helper
    keeps the API-side error wording self-contained without forcing the
    engine to expose structured fields. If parsing fails, returns the raw
    message — the caller's PoolNotFoundError will still render the message
    in ``detail``.
    """
    import re

    match = re.search(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        message,
    )
    return match.group(0) if match else message

"""Allocation pools (envelopes + sinking funds) and the shadow-ledger engine.

Per ADR-0001, envelope and sinking-fund balances are tracked in a parallel
double-entry ledger whose accounts are :class:`Pool` instances. This package
holds the pure-domain types. Persistence and repositories live in
``tulip_storage.models`` / ``tulip_storage.repositories``.
"""

from tulip_core.allocation.engine import (
    InactivePoolError,
    PoolCurrencyMismatchError,
    UnbalancedShadowTransactionError,
    UnknownPoolError,
    post_shadow_transaction,
)
from tulip_core.allocation.envelope import BudgetPeriod, Envelope, RolloverPolicy
from tulip_core.allocation.pool import Pool, PoolType
from tulip_core.allocation.refill_rule import RefillRule, RefillStrategy
from tulip_core.allocation.shadow_posting import ShadowPosting
from tulip_core.allocation.shadow_transaction import (
    ShadowTransaction,
    ShadowTxReason,
    ShadowTxStatus,
)
from tulip_core.allocation.sinking_fund import ContributionStrategy, SinkingFund

__all__ = [
    "BudgetPeriod",
    "ContributionStrategy",
    "Envelope",
    "InactivePoolError",
    "Pool",
    "PoolCurrencyMismatchError",
    "PoolType",
    "RefillRule",
    "RefillStrategy",
    "RolloverPolicy",
    "ShadowPosting",
    "ShadowTransaction",
    "ShadowTxReason",
    "ShadowTxStatus",
    "SinkingFund",
    "UnbalancedShadowTransactionError",
    "UnknownPoolError",
    "post_shadow_transaction",
]

"""Accounting engine — the single chokepoint for posting transactions.

Every code path that writes a transaction goes through `post_transaction`.
Direct construction of postings/transactions in storage code is forbidden by
architecture test (Phase 1+).
"""

from tulip_core.accounting.engine import (
    ClosedPeriodError,
    UnbalancedTransactionError,
    balance_with_fx_postings,
    post_transaction,
)

__all__ = [
    "ClosedPeriodError",
    "UnbalancedTransactionError",
    "balance_with_fx_postings",
    "post_transaction",
]

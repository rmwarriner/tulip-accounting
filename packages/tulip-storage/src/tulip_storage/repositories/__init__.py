"""Repository pattern for tulip-storage.

Each repository wraps a Session and a household_id; queries auto-filter
by household_id so callers never need to remember to add it. Cross-tenant
operations require constructing repos with different household_ids
explicitly.

The audit log writer is the single point that mutates `audit_log` rows;
higher layers (the API) call it on every business mutation.
"""

from tulip_storage.repositories.account import AccountRepository
from tulip_storage.repositories.audit_log import AuditLogWriter
from tulip_storage.repositories.period import PeriodRepository
from tulip_storage.repositories.transaction import TransactionRepository, TrialBalanceRow

__all__ = [
    "AccountRepository",
    "AuditLogWriter",
    "PeriodRepository",
    "TransactionRepository",
    "TrialBalanceRow",
]

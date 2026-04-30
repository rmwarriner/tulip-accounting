"""SQLAlchemy 2.0 ORM models for Tulip Accounting.

The models map the schema documented in ARCHITECTURE.md §4. Phase 1 covers
the load-bearing entities for the accounting engine: households, users,
accounts, periods, transactions, postings, and the audit log. Other tables
(envelopes, scheduled transactions, attachments, etc.) land in their
respective phases.

Module-boundary contract: this package may import from `tulip-core` (the
pure-domain layer) but `tulip-core` may not import from here. The
architecture test in tulip-core enforces this.
"""

from tulip_storage.models.account import Account, AccountType
from tulip_storage.models.audit_log import AuditLog
from tulip_storage.models.base import Base
from tulip_storage.models.household import Household, MfaPolicy
from tulip_storage.models.mfa_recovery_code import MfaRecoveryCode
from tulip_storage.models.period import Period, PeriodStatus
from tulip_storage.models.posting import Posting
from tulip_storage.models.session import Session
from tulip_storage.models.transaction import Transaction, TransactionStatus
from tulip_storage.models.user import User, UserRole

__all__ = [
    "Account",
    "AccountType",
    "AuditLog",
    "Base",
    "Household",
    "MfaPolicy",
    "MfaRecoveryCode",
    "Period",
    "PeriodStatus",
    "Posting",
    "Session",
    "Transaction",
    "TransactionStatus",
    "User",
    "UserRole",
]

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
from tulip_storage.models.ai_invocation import AICapability, AIInvocation, AIOutcome
from tulip_storage.models.allocation_pool import AllocationPool, PoolType
from tulip_storage.models.attachment import Attachment
from tulip_storage.models.attachment_link import AttachmentLink
from tulip_storage.models.audit_log import AuditLog
from tulip_storage.models.base import Base
from tulip_storage.models.csv_profile import CsvProfile
from tulip_storage.models.envelope import BudgetPeriod, Envelope, RolloverPolicy
from tulip_storage.models.household import Household, MfaPolicy
from tulip_storage.models.import_batch import (
    ImportBatch,
    ImportBatchStatus,
    SourceFormat,
)
from tulip_storage.models.mfa_recovery_code import MfaRecoveryCode
from tulip_storage.models.notification import (
    Notification,
    NotificationKind,
    NotificationSeverity,
)
from tulip_storage.models.pending_household_erasure import PendingHouseholdErasure
from tulip_storage.models.pending_proposal import (
    PendingProposal,
    ProposalCreatorKind,
    ProposalStatus,
)
from tulip_storage.models.period import Period, PeriodStatus
from tulip_storage.models.posting import Posting
from tulip_storage.models.reconciliation import Reconciliation, ReconciliationStatus
from tulip_storage.models.reconciliation_match import (
    MatchConfidence,
    ReconciliationMatch,
)
from tulip_storage.models.scheduled_job import (
    ScheduledJob,
    ScheduledJobRun,
    ScheduledJobRunStatus,
)
from tulip_storage.models.session import Session
from tulip_storage.models.shadow_posting import ShadowPosting
from tulip_storage.models.shadow_transaction import (
    ShadowTransaction,
    ShadowTxReason,
    ShadowTxStatus,
)
from tulip_storage.models.sinking_fund import ContributionStrategy, SinkingFund
from tulip_storage.models.statement_line import StatementLine
from tulip_storage.models.tag import Tag
from tulip_storage.models.transaction import Transaction, TransactionStatus
from tulip_storage.models.transaction_tag import TransactionTag
from tulip_storage.models.used_mfa_challenge import UsedMfaChallenge
from tulip_storage.models.user import User, UserRole

__all__ = [
    "AICapability",
    "AIInvocation",
    "AIOutcome",
    "Account",
    "AccountType",
    "AllocationPool",
    "Attachment",
    "AttachmentLink",
    "AuditLog",
    "Base",
    "BudgetPeriod",
    "ContributionStrategy",
    "CsvProfile",
    "Envelope",
    "Household",
    "ImportBatch",
    "ImportBatchStatus",
    "MatchConfidence",
    "MfaPolicy",
    "MfaRecoveryCode",
    "Notification",
    "NotificationKind",
    "NotificationSeverity",
    "PendingHouseholdErasure",
    "PendingProposal",
    "Period",
    "PeriodStatus",
    "PoolType",
    "Posting",
    "ProposalCreatorKind",
    "ProposalStatus",
    "Reconciliation",
    "ReconciliationMatch",
    "ReconciliationStatus",
    "RolloverPolicy",
    "ScheduledJob",
    "ScheduledJobRun",
    "ScheduledJobRunStatus",
    "Session",
    "ShadowPosting",
    "ShadowTransaction",
    "ShadowTxReason",
    "ShadowTxStatus",
    "SinkingFund",
    "SourceFormat",
    "StatementLine",
    "Tag",
    "Transaction",
    "TransactionStatus",
    "TransactionTag",
    "UsedMfaChallenge",
    "User",
    "UserRole",
]

"""Repository pattern for tulip-storage.

Each repository wraps a Session and a household_id; queries auto-filter
by household_id so callers never need to remember to add it. Cross-tenant
operations require constructing repos with different household_ids
explicitly.

The audit log writer is the single point that mutates `audit_log` rows;
higher layers (the API) call it on every business mutation.
"""

from tulip_storage.repositories.account import AccountRepository
from tulip_storage.repositories.allocation_pool import AllocationPoolRepository
from tulip_storage.repositories.attachment import AttachmentRepository
from tulip_storage.repositories.attachment_link import AttachmentLinkRepository
from tulip_storage.repositories.audit_log import AuditLogWriter
from tulip_storage.repositories.csv_profile import CsvProfileRepository
from tulip_storage.repositories.envelope import EnvelopeRepository
from tulip_storage.repositories.import_batch import ImportBatchRepository
from tulip_storage.repositories.notification import NotificationRepository
from tulip_storage.repositories.pending_proposal import PendingProposalRepository
from tulip_storage.repositories.period import PeriodRepository
from tulip_storage.repositories.reconciliation import ReconciliationRepository
from tulip_storage.repositories.reconciliation_match import (
    ReconciliationMatchRepository,
)
from tulip_storage.repositories.scheduled_job import ScheduledJobRepository
from tulip_storage.repositories.shadow_transaction import ShadowTransactionRepository
from tulip_storage.repositories.sinking_fund import SinkingFundRepository
from tulip_storage.repositories.statement_line import StatementLineRepository
from tulip_storage.repositories.transaction import (
    MasterKeyRequiredError,
    TransactionRepository,
    TrialBalanceRow,
)

__all__ = [
    "AccountRepository",
    "AllocationPoolRepository",
    "AttachmentLinkRepository",
    "AttachmentRepository",
    "AuditLogWriter",
    "CsvProfileRepository",
    "EnvelopeRepository",
    "ImportBatchRepository",
    "MasterKeyRequiredError",
    "NotificationRepository",
    "PendingProposalRepository",
    "PeriodRepository",
    "ReconciliationMatchRepository",
    "ReconciliationRepository",
    "ScheduledJobRepository",
    "ShadowTransactionRepository",
    "SinkingFundRepository",
    "StatementLineRepository",
    "TransactionRepository",
    "TrialBalanceRow",
]

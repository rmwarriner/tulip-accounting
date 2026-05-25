"""ImportBatch model — one row per uploaded statement file (P5.1).

Per ADR-0004 §Q6 / §"Schema". An import batch carries the source file
attachment, parse status, and per-line counts.

``summary_json_encrypted`` is a format-specific blob for audit-trail
completeness; never read by the matcher. Today every importer writes
only ``{"line_count": int}``, but the field is *intended* to also carry
source-bank identifiers (OFX BANKID/ACCTID, CSV header layout). It is
AES-256-GCM-encrypted at rest (via ``encryption.encrypt_field``, applied
in ``ImportBatchRepository``) so that future content can't sit in
plaintext parallel to the encrypted ``accounts.external_account_number``
column — see #238. Decrypt with ``ImportBatchRepository.decrypt_summary_json``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base


class SourceFormat(Enum):
    """Discriminator for the source-file format of an import batch."""

    OFX = "ofx"
    QIF = "qif"
    CSV = "csv"
    PTA_HLEDGER = "pta_hledger"


class ImportBatchStatus(Enum):
    """Lifecycle of an import batch.

    - ``PARSED``: rows extracted into ``statement_lines`` but not posted.
    - ``APPLIED``: user has confirmed the batch; matched txs reconciled,
      promoted statement lines posted as PENDING transactions.
    - ``REVERTED``: a previously applied batch has been rolled back per
      ADR-0004 §Q9 reversibility.
    """

    PARSED = "parsed"
    APPLIED = "applied"
    REVERTED = "reverted"


class ImportBatch(Base):
    """One imported statement file."""

    __tablename__ = "import_batches"

    household_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    account_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    source_format: Mapped[SourceFormat] = mapped_column(
        SAEnum(
            SourceFormat,
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )
    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    source_file_attachment_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    status: Mapped[ImportBatchStatus] = mapped_column(
        SAEnum(
            ImportBatchStatus,
            native_enum=False,
            length=20,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )
    imported_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary_json_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("household_id", "id", name="pk_import_batches"),
        ForeignKeyConstraint(
            ["household_id", "account_id"],
            ["accounts.household_id", "accounts.id"],
            name="fk_import_batches_account",
        ),
        ForeignKeyConstraint(
            ["household_id", "source_file_attachment_id"],
            ["attachments.household_id", "attachments.id"],
            name="fk_import_batches_attachment",
        ),
        Index(
            "ix_import_batches_idempotency",
            "household_id",
            "account_id",
            "source_file_attachment_id",
            unique=True,
        ),
    )

"""ImportBatchRepository — CRUD for uploaded statement files (P5.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import select

from tulip_storage.models import ImportBatch, ImportBatchStatus, SourceFormat

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class ImportBatchRepository:
    """Persists import batches and queries them within one household."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repository to a session and tenant scope."""
        self._session = session
        self._household_id = household_id

    def get(self, batch_id: UUID) -> ImportBatch | None:
        """Return the ImportBatch header by id, or None."""
        return self._session.execute(
            select(ImportBatch).where(
                ImportBatch.household_id == self._household_id,
                ImportBatch.id == batch_id,
            )
        ).scalar_one_or_none()

    def find_for_attachment(self, *, account_id: UUID, attachment_id: UUID) -> ImportBatch | None:
        """Return the existing batch for this attachment + account, or None.

        Used for idempotency checks: per ADR-0004 §Q6, re-uploading the
        same file to the same account is a 409 ``import.duplicate_file``.
        The unique index ``ix_import_batches_idempotency`` enforces this
        at the DB layer; this method provides the lookup so the API can
        return a typed problem before the IntegrityError fires.
        """
        return self._session.execute(
            select(ImportBatch).where(
                ImportBatch.household_id == self._household_id,
                ImportBatch.account_id == account_id,
                ImportBatch.source_file_attachment_id == attachment_id,
            )
        ).scalar_one_or_none()

    def list_for_account(self, account_id: UUID) -> list[ImportBatch]:
        """Return all import batches for an account, newest first."""
        return list(
            self._session.execute(
                select(ImportBatch)
                .where(
                    ImportBatch.household_id == self._household_id,
                    ImportBatch.account_id == account_id,
                )
                .order_by(ImportBatch.created_at.desc())
            )
            .scalars()
            .all()
        )

    def create(
        self,
        *,
        account_id: UUID,
        source_format: SourceFormat,
        source_filename: str,
        source_file_attachment_id: UUID,
        created_by_user_id: UUID | None = None,
        summary_json: dict[str, Any] | None = None,
    ) -> ImportBatch:
        """Insert a new ImportBatch in PARSED status (default for new uploads)."""
        batch = ImportBatch(
            household_id=self._household_id,
            id=uuid4(),
            account_id=account_id,
            source_format=source_format,
            source_filename=source_filename,
            source_file_attachment_id=source_file_attachment_id,
            status=ImportBatchStatus.PARSED,
            summary_json=summary_json,
            created_by_user_id=created_by_user_id,
            created_at=datetime.now(tz=UTC),
        )
        self._session.add(batch)
        self._session.flush()
        return batch

    def mark_applied(self, batch_id: UUID) -> ImportBatch:
        """Flip an import batch to APPLIED status."""
        batch = self.get(batch_id)
        if batch is None:
            raise LookupError(
                f"import_batch {batch_id} not found in household {self._household_id}"
            )
        batch.status = ImportBatchStatus.APPLIED
        batch.applied_at = datetime.now(tz=UTC)
        self._session.flush()
        return batch

    def mark_reverted(self, batch_id: UUID) -> ImportBatch:
        """Flip an import batch to REVERTED status."""
        batch = self.get(batch_id)
        if batch is None:
            raise LookupError(
                f"import_batch {batch_id} not found in household {self._household_id}"
            )
        batch.status = ImportBatchStatus.REVERTED
        batch.reverted_at = datetime.now(tz=UTC)
        self._session.flush()
        return batch

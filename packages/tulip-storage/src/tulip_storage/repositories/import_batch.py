"""ImportBatchRepository — CRUD for uploaded statement files (P5.1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import select

from tulip_storage.encryption import decrypt_field, encrypt_field, field_aad
from tulip_storage.models import ImportBatch, ImportBatchStatus, SourceFormat
from tulip_storage.repositories.transaction import MasterKeyRequiredError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class ImportBatchRepository:
    """Persists import batches and queries them within one household."""

    def __init__(
        self,
        session: Session,
        household_id: UUID,
        *,
        master_key: bytes | None = None,
    ) -> None:
        """Bind the repository to a session and tenant scope.

        ``master_key`` is required only to write or read ``summary_json``
        (it's AES-256-GCM encrypted at rest — #238). The read / list /
        apply / revert paths all work without one.
        """
        self._session = session
        self._household_id = household_id
        self._master_key = master_key

    def _require_master_key(self) -> bytes:
        if self._master_key is None:
            raise MasterKeyRequiredError(
                "ImportBatchRepository requires a master_key to encrypt or "
                "decrypt summary_json; construct with master_key=..."
            )
        return self._master_key

    def _summary_aad(self, batch_id: UUID) -> bytes:
        return field_aad(
            table="import_batches",
            column="summary_json_encrypted",
            household_id=self._household_id,
            row_id=batch_id,
        )

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

    def list_recent(
        self,
        *,
        status: ImportBatchStatus | None = None,
        account_id: UUID | None = None,
        after: tuple[datetime, UUID] | None = None,
        limit: int = 25,
    ) -> list[ImportBatch]:
        """List import batches in this household, newest first.

        ``limit`` caps the number of rows returned. ``status`` and
        ``account_id`` are optional AND filters. ``after`` is a
        keyset-pagination cursor — pass the ``(created_at, id)`` tuple
        of the last row of the previous page to fetch the next page.
        Ordering is ``(created_at DESC, id DESC)`` so the ``id`` is a
        stable tiebreaker when many batches land at the same timestamp.
        """
        query = select(ImportBatch).where(ImportBatch.household_id == self._household_id)
        if status is not None:
            query = query.where(ImportBatch.status == status)
        if account_id is not None:
            query = query.where(ImportBatch.account_id == account_id)
        if after is not None:
            after_created_at, after_id = after
            # Standard keyset pagination: rows strictly older than the cursor,
            # or with the same timestamp but a lower id.
            query = query.where(
                (ImportBatch.created_at < after_created_at)
                | ((ImportBatch.created_at == after_created_at) & (ImportBatch.id < after_id))
            )
        query = query.order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc()).limit(limit)
        return list(self._session.execute(query).scalars().all())

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
        """Insert a new ImportBatch in PARSED status (default for new uploads).

        ``summary_json`` is encrypted at rest (#238); passing a non-None
        value requires the repository to have a ``master_key``.
        """
        # Allocate the id before encryption so the AAD can bind to the
        # exact row this ciphertext will live on (#338, M-1).
        batch_id = uuid4()
        summary_blob: bytes | None = None
        if summary_json is not None:
            key = self._require_master_key()
            summary_blob = encrypt_field(
                json.dumps(summary_json).encode("utf-8"),
                key,
                aad=self._summary_aad(batch_id),
            )
        batch = ImportBatch(
            household_id=self._household_id,
            id=batch_id,
            account_id=account_id,
            source_format=source_format,
            source_filename=source_filename,
            source_file_attachment_id=source_file_attachment_id,
            status=ImportBatchStatus.PARSED,
            summary_json_encrypted=summary_blob,
            created_by_user_id=created_by_user_id,
            created_at=datetime.now(tz=UTC),
        )
        self._session.add(batch)
        self._session.flush()
        return batch

    def decrypt_summary_json(self, batch: ImportBatch) -> dict[str, Any] | None:
        """Return the decrypted ``summary_json`` dict for ``batch``, or None.

        Requires a configured master key when the column is non-NULL.
        """
        if batch.summary_json_encrypted is None:
            return None
        key = self._require_master_key()
        decoded: dict[str, Any] = json.loads(
            decrypt_field(
                batch.summary_json_encrypted, key, aad=self._summary_aad(batch.id)
            ).decode("utf-8")
        )
        return decoded

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

    def delete(self, batch_id: UUID) -> None:
        """Hard-delete an import batch (#345).

        Cascades ``statement_lines`` via the FK ``ondelete="CASCADE"``.
        Caller MUST first verify no statement_line has a non-NULL
        ``promoted_transaction_id`` — promoted lines have a RESTRICT FK
        from ``transactions`` that would otherwise block the delete.
        See ``DELETE /v1/imports/{batch_id}`` for the application-layer
        guard that surfaces a typed 409 in that case.
        """
        from sqlalchemy import delete as _sa_delete

        self._session.execute(
            _sa_delete(ImportBatch).where(
                ImportBatch.household_id == self._household_id,
                ImportBatch.id == batch_id,
            )
        )
        self._session.flush()

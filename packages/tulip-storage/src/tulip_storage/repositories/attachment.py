"""AttachmentRepository — stores encrypted file bytes + metadata row (P5.1).

Per ADR-0004 §Q9. Plaintext bytes never touch the database; the bytes are
encrypted with the household's master key and written to the filesystem
under ``attachment_root / <content_hash>``. Metadata (filename, content
type, size, hash) lands on the row in clear.

This is the **only** repository that performs filesystem I/O. The
constructor takes ``attachment_root: Path`` so tests can pass a tmp path
without mocking the home directory.

Dedup: the unique index on ``(household_id, content_hash)`` rejects
duplicate uploads at the DB layer. Callers should look up by hash first
when they have one (`find_by_hash`).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import func, select

from tulip_storage.encryption import encrypt_field
from tulip_storage.models import Attachment

log = logging.getLogger("tulip_storage.repositories.attachment")


def _attachment_aad(content_hash: str) -> bytes:
    """Per-content AAD for attachment AEAD (#338, M-1).

    Bound to ``content_hash`` rather than ``(household_id, attachment_id)``
    so the cross-household dedup of content-addressed blob storage stays
    correct. See ``write`` for the full rationale.
    """
    return f"attachments:content:{content_hash}".encode()


if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class AttachmentRepository:
    """Persists encrypted attachments + metadata, scoped to one household."""

    def __init__(
        self,
        session: Session,
        household_id: UUID,
        *,
        master_key: bytes,
        attachment_root: Path,
    ) -> None:
        """Bind the repository to a session, tenant scope, key, and fs root."""
        self._session = session
        self._household_id = household_id
        self._master_key = master_key
        self._attachment_root = attachment_root

    def get(self, attachment_id: UUID) -> Attachment | None:
        """Return the Attachment header by id (within this household)."""
        return self._session.execute(
            select(Attachment).where(
                Attachment.household_id == self._household_id,
                Attachment.id == attachment_id,
            )
        ).scalar_one_or_none()

    def find_by_hash(self, content_hash: str) -> Attachment | None:
        """Return an existing Attachment with this content hash, or None."""
        return self._session.execute(
            select(Attachment).where(
                Attachment.household_id == self._household_id,
                Attachment.content_hash == content_hash,
            )
        ).scalar_one_or_none()

    def create(
        self,
        *,
        filename: str,
        content_type: str,
        raw_bytes: bytes,
        uploaded_by_user_id: UUID | None = None,
    ) -> Attachment:
        """Encrypt and persist a new attachment.

        The plaintext ``raw_bytes`` are SHA-256-hashed (for dedup) then
        encrypted via ``encrypt_field`` and written to the filesystem at
        ``attachment_root / <content_hash>``. Caller must ``session.commit()``.
        """
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        # Per-content AAD (#338, M-1). Attachment storage is content-
        # addressed (dedup by content_hash across households + rows), so
        # the AAD can't bind to (household_id, attachment_id) without
        # breaking dedup — a second household uploading the same plaintext
        # would overwrite the file with a ciphertext authenticated under
        # a different AAD, making the first row undecryptable. Binding
        # the AAD to ``content_hash`` keeps dedup working while still
        # blocking the cross-row swap: any row pointing at a *different*
        # content_hash would carry a different AAD and decrypt would fail.
        encrypted = encrypt_field(
            raw_bytes,
            self._master_key,
            aad=_attachment_aad(content_hash),
        )

        self._attachment_root.mkdir(parents=True, exist_ok=True)
        target = self._attachment_root / content_hash
        target.write_bytes(encrypted)

        att = Attachment(
            household_id=self._household_id,
            id=uuid4(),
            filename=filename,
            content_type=content_type,
            size_bytes=len(raw_bytes),
            content_hash=content_hash,
            storage_uri=f"fs://{content_hash}",
            uploaded_by_user_id=uploaded_by_user_id,
            uploaded_at=datetime.now(tz=UTC),
        )
        self._session.add(att)
        self._session.flush()
        return att

    def read_bytes(self, attachment_id: UUID) -> bytes:
        """Decrypt and return the plaintext bytes for an attachment."""
        from tulip_storage.encryption import decrypt_field

        att = self.get(attachment_id)
        if att is None:
            raise LookupError(
                f"attachment {attachment_id} not found in household {self._household_id}"
            )
        path = self._attachment_root / att.content_hash
        ciphertext = path.read_bytes()
        return decrypt_field(ciphertext, self._master_key, aad=_attachment_aad(att.content_hash))

    def delete(self, attachment_id: UUID) -> bool:
        """Delete an attachment row and unlink its ciphertext if no rows remain.

        Within-household dedup means one blob on disk may back several
        attachment rows (different filenames, same content). The blob is
        unlinked only when the last row referencing its content_hash is
        deleted — refcount check across the whole ``attachments`` table.

        Returns True if the row existed and was deleted; False if no
        such attachment was found in this household. Caller commits.
        """
        att = self.get(attachment_id)
        if att is None:
            return False
        content_hash = att.content_hash
        self._session.delete(att)
        self._session.flush()
        remaining = self._session.execute(
            select(func.count())
            .select_from(Attachment)
            .where(Attachment.content_hash == content_hash)
        ).scalar_one()
        if remaining == 0:
            blob = self._attachment_root / content_hash
            try:
                blob.unlink()
            except FileNotFoundError:
                # Already gone (concurrent GC, partial-write, manual cleanup) —
                # not an error; the row is deleted either way.
                log.info("attachment.blob_already_missing", extra={"content_hash": content_hash})
        return True

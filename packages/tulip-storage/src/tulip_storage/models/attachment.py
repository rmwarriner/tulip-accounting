"""Attachment model — encrypted-at-rest file storage with content-hash dedup.

Per ADR-0004 §Q9, P5.1 introduces the substrate for storing original
import files (and any other household attachments). The bytes are
encrypted using the same field-level helpers as ``transactions.notes_encrypted``
(P1.6); ``content_hash`` is the SHA-256 of the **raw plaintext bytes** and
serves as the dedup key per §Q6.

The on-disk path is constructed by ``AttachmentRepository`` (lands in P5.1's
storage layer) under ``Settings.attachment_root``. Only ``storage_uri``
(``"fs://<content_hash>"``) is stored on the row; the actual ciphertext
lives on the filesystem.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from tulip_storage.models.base import GUID, Base


class Attachment(Base):
    """Header for an encrypted file blob; plaintext lives outside the DB."""

    __tablename__ = "attachments"
    __table_args__ = (
        Index(
            "ix_attachments_hash",
            "household_id",
            "content_hash",
            unique=True,
        ),
    )

    household_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("households.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    data_key_wrapped: Mapped[bytes | None] = mapped_column(nullable=True)
    uploaded_by_user_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

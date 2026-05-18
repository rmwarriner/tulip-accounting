"""ShadowPosting model — one leg of a shadow-ledger transaction."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tulip_storage.models.allocation_pool import AllocationPool
from tulip_storage.models.base import GUID, Base, SqliteDecimal
from tulip_storage.models.shadow_transaction import ShadowTransaction


class ShadowPosting(Base):
    """One leg of a :class:`ShadowTransaction`.

    Composite FKs to ``shadow_transactions`` and ``allocation_pools`` mirror
    the main-ledger ``Posting`` design. Sum-to-zero per shadow_transaction_id
    per currency is enforced by trigger on transitions into ``posted``.
    """

    __tablename__ = "shadow_postings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["household_id", "shadow_transaction_id"],
            ["shadow_transactions.household_id", "shadow_transactions.id"],
            ondelete="CASCADE",
            name="fk_shadow_postings_shadow_transaction",
        ),
        ForeignKeyConstraint(
            ["household_id", "pool_id"],
            ["allocation_pools.household_id", "allocation_pools.id"],
            ondelete="RESTRICT",
            name="fk_shadow_postings_pool",
        ),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    household_id: Mapped[UUID] = mapped_column(GUID(), nullable=False, index=True)
    shadow_transaction_id: Mapped[UUID] = mapped_column(GUID(), nullable=False, index=True)
    pool_id: Mapped[UUID] = mapped_column(GUID(), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(SqliteDecimal(20, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    memo: Mapped[str | None] = mapped_column(String(500), nullable=True)

    shadow_transaction: Mapped[ShadowTransaction] = relationship(
        foreign_keys="[ShadowPosting.household_id, ShadowPosting.shadow_transaction_id]",
        overlaps="pool",
    )
    pool: Mapped[AllocationPool] = relationship(
        foreign_keys="[ShadowPosting.household_id, ShadowPosting.pool_id]",
        overlaps="shadow_transaction",
    )

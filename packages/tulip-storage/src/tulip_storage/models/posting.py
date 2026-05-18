"""Posting model — one line of a double-entry transaction."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tulip_storage.models.account import Account
from tulip_storage.models.base import GUID, Base, SqliteDecimal
from tulip_storage.models.transaction import Transaction


class Posting(Base):
    """One side of a double-entry transaction.

    Composite FKs to `transactions` (household_id, transaction_id) and to
    `accounts` (household_id, account_id) ensure cross-tenant references
    are impossible at the schema level. The balanced-postings invariant
    (sum of amounts per (transaction_id, currency) = 0) is enforced by a
    trigger created in the initial Alembic migration; see ARCHITECTURE §4.2.
    """

    __tablename__ = "postings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["household_id", "transaction_id"],
            ["transactions.household_id", "transactions.id"],
            ondelete="CASCADE",
            name="fk_postings_transaction",
        ),
        ForeignKeyConstraint(
            ["household_id", "account_id"],
            ["accounts.household_id", "accounts.id"],
            ondelete="RESTRICT",
            name="fk_postings_account",
        ),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    household_id: Mapped[UUID] = mapped_column(GUID(), nullable=False, index=True)
    transaction_id: Mapped[UUID] = mapped_column(GUID(), nullable=False, index=True)
    account_id: Mapped[UUID] = mapped_column(GUID(), nullable=False, index=True)
    pool_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    amount: Mapped[Decimal] = mapped_column(SqliteDecimal(20, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    fx_rate: Mapped[Decimal | None] = mapped_column(SqliteDecimal(20, 8), nullable=True)
    fx_amount: Mapped[Decimal | None] = mapped_column(SqliteDecimal(20, 8), nullable=True)
    fx_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    memo: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Relationships exist primarily for unit-of-work flush ordering and
    # ergonomic access. The composite FKs are declared in __table_args__;
    # SA infers the join from the ForeignKeyConstraint.
    transaction: Mapped[Transaction] = relationship(
        foreign_keys="[Posting.household_id, Posting.transaction_id]",
        overlaps="account",
    )
    account: Mapped[Account] = relationship(
        foreign_keys="[Posting.household_id, Posting.account_id]",
        overlaps="transaction",
    )

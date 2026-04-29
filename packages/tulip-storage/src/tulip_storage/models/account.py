"""Account model — chart-of-accounts node, persistence-aware."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tulip_storage.models.base import GUID, Base
from tulip_storage.models.household import Household


class AccountType(Enum):
    """Five canonical accounting types (matches tulip_core.account.AccountType)."""

    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"


class Account(Base):
    """A chart-of-accounts node within a household.

    Composite primary key (household_id, id) — every cross-table FK is
    composite, which makes tenant-scoping a query-builder concern. The
    self-FK to parent_account_id is also composite (parent must be in the
    same household).
    """

    __tablename__ = "accounts"
    __table_args__ = (
        # Composite self-FK: parent must live in the same household. use_alter
        # prevents SQLAlchemy from treating accounts as transitively dependent
        # on itself for unit-of-work ordering (which otherwise inserts accounts
        # before households on a fresh flush).
        ForeignKeyConstraint(
            ["household_id", "parent_account_id"],
            ["accounts.household_id", "accounts.id"],
            ondelete="SET NULL",
            name="fk_accounts_parent",
            use_alter=True,
        ),
    )

    household_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("households.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    parent_account_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[AccountType] = mapped_column(
        SAEnum(AccountType, native_enum=False, length=20), nullable=False
    )
    subtype: Mapped[str | None] = mapped_column(String(50), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    visibility: Mapped[str] = mapped_column(String(10), nullable=False, default="shared")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    external_account_number_encrypted: Mapped[bytes | None] = mapped_column(nullable=True)
    notes_encrypted: Mapped[bytes | None] = mapped_column(nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    household: Mapped[Household] = relationship()

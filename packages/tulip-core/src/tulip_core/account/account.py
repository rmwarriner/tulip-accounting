"""Account value object at the core / structural layer.

This is the pure-domain shape; the persistence-aware Account model lives in
tulip-storage. Equality is by id, mirroring how account references are used
throughout postings and reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from tulip_core.currency import Currency

if TYPE_CHECKING:
    from uuid import UUID


class AccountType(Enum):
    """The five canonical accounting types."""

    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"


@dataclass(frozen=True, slots=True, eq=False)
class Account:
    """A pure-domain Account record.

    Equality is by id only; two Account instances with the same id are
    considered the same account regardless of other fields. The dataclass is
    frozen for immutability and uses eq=False so we can implement id-based
    equality and hashing explicitly.
    """

    id: UUID
    code: str | None
    name: str
    type: AccountType
    currency: str
    parent_id: UUID | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate currency and (when provided) the account code format."""
        Currency.from_code(self.currency)
        if self.code is not None and (not self.code or any(ch.isspace() for ch in self.code)):
            raise ValueError(f"Invalid account code: {self.code!r}")

    def __eq__(self, other: object) -> bool:
        """Two Accounts are equal when their ids match."""
        if not isinstance(other, Account):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        """Hash by id, consistent with equality."""
        return hash(self.id)

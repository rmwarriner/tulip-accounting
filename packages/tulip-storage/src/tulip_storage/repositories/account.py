"""AccountRepository — household-scoped CRUD over the accounts table."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select

from tulip_storage.models import Account, AccountType

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class AccountRepository:
    """CRUD for accounts within a single household."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repository to a session and a tenant scope."""
        self._session = session
        self._household_id = household_id

    def create(
        self,
        *,
        name: str,
        type: AccountType,
        currency: str,
        code: str | None = None,
        subtype: str | None = None,
        parent_account_id: UUID | None = None,
        visibility: str = "shared",
        created_by_user_id: UUID | None = None,
    ) -> Account:
        """Insert a new Account into this repository's household."""
        a = Account(
            household_id=self._household_id,
            id=uuid4(),
            code=code,
            name=name,
            type=type,
            subtype=subtype,
            currency=currency,
            visibility=visibility,
            is_active=True,
            parent_account_id=parent_account_id,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(a)
        self._session.flush()
        return a

    def get(self, account_id: UUID) -> Account | None:
        """Return the Account with the given id within this household, or None."""
        return self._session.execute(
            select(Account).where(
                Account.household_id == self._household_id,
                Account.id == account_id,
            )
        ).scalar_one_or_none()

    def get_by_code(self, code: str) -> Account | None:
        """Return the Account with the given code within this household, or None."""
        return self._session.execute(
            select(Account).where(
                Account.household_id == self._household_id,
                Account.code == code,
            )
        ).scalar_one_or_none()

    def list_active(self) -> list[Account]:
        """Return all active accounts in this household, ordered by code/name."""
        return list(
            self._session.execute(
                select(Account)
                .where(
                    Account.household_id == self._household_id,
                    Account.is_active.is_(True),
                )
                .order_by(Account.code, Account.name)
            )
            .scalars()
            .all()
        )

    def deactivate(self, account_id: UUID) -> Account:
        """Mark an account inactive (soft delete). Raises if the account is missing."""
        a = self.get(account_id)
        if a is None:
            raise LookupError(f"account {account_id} not found in household {self._household_id}")
        a.is_active = False
        self._session.flush()
        return a

    def redact(self, account_id: UUID, *, name: str) -> Account:
        """Null an account's PII columns; replace ``name`` with a placeholder.

        Erases ``name`` (to the caller-supplied non-PII placeholder),
        ``external_account_number_encrypted`` and ``notes_encrypted``.
        Postings keep their FK and amounts — ledger history is preserved.
        The caller (the API redact endpoint) enforces the precondition
        that the account is already deactivated. Raises ``LookupError``
        if the account is missing.
        """
        a = self.get(account_id)
        if a is None:
            raise LookupError(f"account {account_id} not found in household {self._household_id}")
        a.name = name
        a.external_account_number_encrypted = None
        a.notes_encrypted = None
        self._session.flush()
        return a

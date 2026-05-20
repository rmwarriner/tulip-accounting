"""AccountRepository — household-scoped CRUD over the accounts table."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select

from tulip_storage.encryption import decrypt_field, encrypt_field, field_aad
from tulip_storage.models import Account, AccountType

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class AccountMasterKeyRequiredError(RuntimeError):
    """Raised when a notes write is attempted without a master key configured."""


class AccountRepository:
    """CRUD for accounts within a single household."""

    def __init__(
        self,
        session: Session,
        household_id: UUID,
        *,
        master_key: bytes | None = None,
    ) -> None:
        """Bind the repository to a session and a tenant scope.

        ``master_key`` is required only when ``notes`` plaintext is
        passed to ``create`` / ``set_notes`` (or read back via
        :meth:`decrypt_notes`). The pre-existing CRUD surface stays
        intact without it.
        """
        self._session = session
        self._household_id = household_id
        self._master_key = master_key

    def _require_master_key(self) -> bytes:
        if self._master_key is None:
            raise AccountMasterKeyRequiredError(
                "AccountRepository requires a master_key to encrypt or "
                "decrypt account notes; construct with master_key=..."
            )
        return self._master_key

    def _notes_aad(self, account_id: UUID) -> bytes:
        return field_aad(
            table="accounts",
            column="notes_encrypted",
            household_id=self._household_id,
            row_id=account_id,
        )

    def decrypt_notes(self, account: Account) -> str | None:
        """Return the plaintext notes for ``account`` or None when unset.

        Decoded as UTF-8. Requires a configured master key.
        """
        if account.notes_encrypted is None:
            return None
        key = self._require_master_key()
        return decrypt_field(account.notes_encrypted, key, aad=self._notes_aad(account.id)).decode(
            "utf-8"
        )

    def set_notes(self, account_id: UUID, notes: str | None) -> Account:
        """Encrypt + persist a freeform notes string, or clear with None."""
        account = self.get(account_id)
        if account is None:
            raise LookupError(f"account {account_id} not found in household {self._household_id}")
        if notes is None or notes == "":
            account.notes_encrypted = None
        else:
            key = self._require_master_key()
            account.notes_encrypted = encrypt_field(
                notes.encode("utf-8"), key, aad=self._notes_aad(account_id)
            )
        self._session.flush()
        return account

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
        notes: str | None = None,
        created_by_user_id: UUID | None = None,
    ) -> Account:
        """Insert a new Account into this repository's household.

        When ``notes`` is provided, the repository must have been
        constructed with a master key — see :meth:`__init__`.
        """
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
        if notes:
            key = self._require_master_key()
            a.notes_encrypted = encrypt_field(notes.encode("utf-8"), key, aad=self._notes_aad(a.id))
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

    def get_by_parent_and_name(
        self,
        *,
        parent_id: UUID | None,
        name: str,
    ) -> Account | None:
        """Return the Account with this name under the given parent, or None.

        Used by the name-path ``create_parents`` walker (#416) to decide
        whether an intermediate at a given (parent, name) position
        already exists. ``parent_id=None`` matches top-level accounts
        whose ``parent_account_id`` is NULL.

        Names are compared case-sensitively and exactly. Multiple
        accounts under the same parent can share a name today (no
        DB-level uniqueness constraint), so two siblings named "Cash"
        would both match — caller treats that as ambiguous reuse and
        creates a fresh row rather than picking one arbitrarily.
        """
        stmt = select(Account).where(
            Account.household_id == self._household_id,
            Account.name == name,
        )
        if parent_id is None:
            stmt = stmt.where(Account.parent_account_id.is_(None))
        else:
            stmt = stmt.where(Account.parent_account_id == parent_id)
        rows = list(self._session.execute(stmt).scalars().all())
        if len(rows) == 1:
            return rows[0]
        return None

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

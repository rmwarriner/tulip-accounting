"""StatementLineRepository — bulk insert + queries for parsed bank lines."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import select

from tulip_storage.models import StatementLine

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class StatementLineRepository:
    """Persists statement lines and queries them within one household."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repository to a session and tenant scope."""
        self._session = session
        self._household_id = household_id

    def get(self, line_id: UUID) -> StatementLine | None:
        """Return the StatementLine by id, or None."""
        return self._session.execute(
            select(StatementLine).where(
                StatementLine.household_id == self._household_id,
                StatementLine.id == line_id,
            )
        ).scalar_one_or_none()

    def list_for_batch(self, import_batch_id: UUID) -> list[StatementLine]:
        """Return all statement lines for an import batch, in source order."""
        return list(
            self._session.execute(
                select(StatementLine)
                .where(
                    StatementLine.household_id == self._household_id,
                    StatementLine.import_batch_id == import_batch_id,
                )
                .order_by(StatementLine.line_number)
            )
            .scalars()
            .all()
        )

    def list_unmatched(self, import_batch_id: UUID) -> list[StatementLine]:
        """Return statement lines that aren't matched and aren't excluded."""
        return list(
            self._session.execute(
                select(StatementLine)
                .where(
                    StatementLine.household_id == self._household_id,
                    StatementLine.import_batch_id == import_batch_id,
                    StatementLine.reconciliation_match_id.is_(None),
                    StatementLine.is_excluded.is_(False),
                )
                .order_by(StatementLine.line_number)
            )
            .scalars()
            .all()
        )

    def bulk_insert(
        self,
        import_batch_id: UUID,
        rows: Iterable[dict[str, Any]],
    ) -> list[StatementLine]:
        """Insert many statement lines in one go.

        Each ``row`` is a dict with keys: ``line_number``, ``posted_date``,
        ``amount`` (Decimal), ``currency``, ``description``, optional
        ``counterparty``, ``reference``, ``fitid``, and ``raw_json`` (str).
        """
        out: list[StatementLine] = []
        for row in rows:
            line = StatementLine(
                household_id=self._household_id,
                id=uuid4(),
                import_batch_id=import_batch_id,
                line_number=row["line_number"],
                posted_date=row["posted_date"],
                amount=row["amount"],
                currency=row["currency"],
                description=row["description"],
                counterparty=row.get("counterparty"),
                reference=row.get("reference"),
                fitid=row.get("fitid"),
                raw_json=row.get("raw_json", "{}"),
            )
            self._session.add(line)
            out.append(line)
        self._session.flush()
        return out

    def exclude(self, line_id: UUID) -> StatementLine:
        """Mark a statement line as excluded (soft-delete from matching pool)."""
        line = self.get(line_id)
        if line is None:
            raise LookupError(
                f"statement_line {line_id} not found in household {self._household_id}"
            )
        line.is_excluded = True
        self._session.flush()
        return line

    def mark_promoted(self, line_id: UUID, transaction_id: UUID) -> StatementLine:
        """Link a statement line to the ledger Transaction it was promoted into.

        Single chokepoint for setting ``promoted_transaction_id`` —
        keeps the architecture test happy and gives one obvious place to
        layer in audit-log writes if/when promotion needs more than the
        router-level audit row that already covers it.
        """
        line = self.get(line_id)
        if line is None:
            raise LookupError(
                f"statement_line {line_id} not found in household {self._household_id}"
            )
        line.promoted_transaction_id = transaction_id
        self._session.flush()
        return line

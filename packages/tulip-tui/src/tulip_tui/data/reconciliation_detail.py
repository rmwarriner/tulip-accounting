"""Reconciliation-detail / inbox data adapter (P9.6.b).

Wraps ``GET /v1/reconciliations/{id}`` into an immutable
``ReconciliationDetail`` value object. Also provides thin wrappers
around the per-action endpoints (auto-match, reject, manual match,
paper match, carry-forward, complete) so the TUI screen just makes
``Callable`` calls and never touches HTTP plumbing directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from tulip_cli.http import TulipClient


@dataclass(frozen=True, slots=True)
class ReconciliationEnvelope:
    """Top-level reconciliation header — period + balances + status."""

    id: str
    account_id: str
    statement_period_start: str
    statement_period_end: str
    statement_starting_balance: str
    statement_ending_balance: str
    currency: str
    status: str
    source_import_batch_id: str | None
    created_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class MatchSummary:
    """One reconciliation match — line ↔ ledger transaction pair."""

    id: str
    statement_line_id: str | None
    ledger_transaction_id: str
    match_amount: str
    currency: str
    confidence: str | None
    created_by_user_id: str | None
    is_manual: bool


@dataclass(frozen=True, slots=True)
class UnmatchedLine:
    """A statement line that has no match in this reconciliation yet."""

    id: str
    line_number: int
    posted_date: str
    amount_display: str
    currency: str
    description: str
    reference: str | None


@dataclass(frozen=True, slots=True)
class UnmatchedTransaction:
    """A ledger transaction inside the statement period not yet matched."""

    id: str
    date: str
    description: str
    reference: str | None
    status: str


@dataclass(frozen=True, slots=True)
class ReconciliationDetail:
    """Full inbox payload: envelope + matches + two unmatched lists."""

    envelope: ReconciliationEnvelope
    matches: tuple[MatchSummary, ...]
    unmatched_lines: tuple[UnmatchedLine, ...]
    unmatched_transactions: tuple[UnmatchedTransaction, ...]

    @property
    def is_paper(self) -> bool:
        """A paper-statement reconciliation has no imported batch."""
        return self.envelope.source_import_batch_id is None


def load_reconciliation_detail(client: TulipClient, reconciliation_id: str) -> ReconciliationDetail:
    """Fetch and parse ``/v1/reconciliations/{id}`` (the inbox view)."""
    raw = client.get(f"/v1/reconciliations/{reconciliation_id}", authenticated=True).json()
    return ReconciliationDetail(
        envelope=_to_envelope(cast("dict[str, object]", raw.get("reconciliation", {}))),
        matches=tuple(
            _to_match(m) for m in cast("list[dict[str, object]]", raw.get("matches") or [])
        ),
        unmatched_lines=tuple(
            _to_unmatched_line(line)
            for line in cast("list[dict[str, object]]", raw.get("unmatched_statement_lines") or [])
        ),
        unmatched_transactions=tuple(
            _to_unmatched_tx(tx)
            for tx in cast(
                "list[dict[str, object]]",
                raw.get("unmatched_ledger_transactions") or [],
            )
        ),
    )


def auto_match(client: TulipClient, reconciliation_id: str) -> dict[str, object]:
    """Call ``POST /v1/reconciliations/{id}/auto-match``."""
    resp = client.post(
        f"/v1/reconciliations/{reconciliation_id}/auto-match",
        authenticated=True,
    )
    return cast("dict[str, object]", resp.json())


def reject_match(client: TulipClient, reconciliation_id: str, match_id: str) -> None:
    """Call ``POST /v1/reconciliations/{id}/matches/{match_id}/reject``."""
    client.post(
        f"/v1/reconciliations/{reconciliation_id}/matches/{match_id}/reject",
        authenticated=True,
    )


def manual_match(
    client: TulipClient,
    reconciliation_id: str,
    *,
    statement_line_id: str,
    ledger_transaction_id: str,
    match_amount: str,
    currency: str,
) -> dict[str, object]:
    """Call ``POST /v1/reconciliations/{id}/matches``."""
    resp = client.post(
        f"/v1/reconciliations/{reconciliation_id}/matches",
        authenticated=True,
        json={
            "statement_line_id": statement_line_id,
            "ledger_transaction_id": ledger_transaction_id,
            "match_amount": match_amount,
            "currency": currency,
        },
    )
    return cast("dict[str, object]", resp.json())


def paper_match(
    client: TulipClient,
    reconciliation_id: str,
    *,
    ledger_transaction_id: str,
) -> dict[str, object]:
    """Call ``POST /v1/reconciliations/{id}/paper-matches`` (mark-cleared)."""
    resp = client.post(
        f"/v1/reconciliations/{reconciliation_id}/paper-matches",
        authenticated=True,
        json={"ledger_transaction_id": ledger_transaction_id},
    )
    return cast("dict[str, object]", resp.json())


def carry_forward(
    client: TulipClient,
    reconciliation_id: str,
    *,
    transaction_ids: list[str],
) -> dict[str, object]:
    """Call ``POST /v1/reconciliations/{id}/carry-forward``."""
    resp = client.post(
        f"/v1/reconciliations/{reconciliation_id}/carry-forward",
        authenticated=True,
        json={"transaction_ids": transaction_ids},
    )
    return cast("dict[str, object]", resp.json())


def complete(client: TulipClient, reconciliation_id: str) -> dict[str, object]:
    """Call ``POST /v1/reconciliations/{id}/complete``."""
    resp = client.post(
        f"/v1/reconciliations/{reconciliation_id}/complete",
        authenticated=True,
    )
    return cast("dict[str, object]", resp.json())


def _to_envelope(row: dict[str, object]) -> ReconciliationEnvelope:
    return ReconciliationEnvelope(
        id=str(row.get("id", "")),
        account_id=str(row.get("account_id", "")),
        statement_period_start=str(row.get("statement_period_start", "")),
        statement_period_end=str(row.get("statement_period_end", "")),
        statement_starting_balance=str(row.get("statement_starting_balance", "")),
        statement_ending_balance=str(row.get("statement_ending_balance", "")),
        currency=str(row.get("currency", "")),
        status=str(row.get("status", "")),
        source_import_batch_id=_optional_str(row.get("source_import_batch_id")),
        created_at=str(row.get("created_at", "")),
        completed_at=_optional_str(row.get("completed_at")),
    )


def _to_match(row: dict[str, object]) -> MatchSummary:
    confidence = _optional_str(row.get("confidence"))
    created_by = _optional_str(row.get("created_by_user_id"))
    amount = Decimal(str(row.get("match_amount", "0"))).quantize(Decimal("0.01"))
    return MatchSummary(
        id=str(row.get("id", "")),
        statement_line_id=_optional_str(row.get("statement_line_id")),
        ledger_transaction_id=str(row.get("ledger_transaction_id", "")),
        match_amount=f"{amount:,.2f}",
        currency=str(row.get("currency", "")),
        confidence=confidence,
        created_by_user_id=created_by,
        is_manual=confidence is None and created_by is not None,
    )


def _to_unmatched_line(row: dict[str, object]) -> UnmatchedLine:
    amount = Decimal(str(row.get("amount", "0"))).quantize(Decimal("0.01"))
    return UnmatchedLine(
        id=str(row.get("id", "")),
        line_number=_optional_int(row.get("line_number")),
        posted_date=str(row.get("posted_date", "")),
        amount_display=f"{amount:,.2f}",
        currency=str(row.get("currency", "")),
        description=str(row.get("description", "")),
        reference=_optional_str(row.get("reference")),
    )


def _to_unmatched_tx(row: dict[str, object]) -> UnmatchedTransaction:
    return UnmatchedTransaction(
        id=str(row.get("id", "")),
        date=str(row.get("date", "")),
        description=str(row.get("description", "")),
        reference=_optional_str(row.get("reference")),
        status=str(row.get("status", "")),
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value:
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


__all__: list[str] = [
    "MatchSummary",
    "ReconciliationDetail",
    "ReconciliationEnvelope",
    "UnmatchedLine",
    "UnmatchedTransaction",
    "auto_match",
    "carry_forward",
    "complete",
    "load_reconciliation_detail",
    "manual_match",
    "paper_match",
    "reject_match",
]

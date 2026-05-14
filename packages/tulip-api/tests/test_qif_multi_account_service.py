"""Unit tests for the QIF cross-account transfer pairing service (#195b)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import MappingProxyType
from uuid import uuid4

from tulip_api.services.qif_multi_account import pair_transfers
from tulip_core.money import Money
from tulip_core.reconciliation import ParsedStatementLine


def _line(
    line_number: int,
    amount: str,
    *,
    target: str | None,
    currency: str = "USD",
    on: date = date(2026, 1, 10),
) -> ParsedStatementLine:
    """Build a ParsedStatementLine; ``target`` becomes a QIF L[Account] field."""
    raw = {"L": f"[{target}]"} if target is not None else {}
    return ParsedStatementLine(
        line_number=line_number,
        posted_date=on,
        amount=Money(Decimal(amount), currency),
        description=f"line {line_number}",
        counterparty=None,
        reference=None,
        fitid=None,
        raw=MappingProxyType(raw),
    )


_CHECKING = uuid4()
_SAVINGS = uuid4()
_MAP = {"Checking": _CHECKING, "Savings": _SAVINGS}


def test_reciprocal_legs_pair() -> None:
    parsed = {
        "Checking": [_line(1, "-200.00", target="Savings")],
        "Savings": [_line(1, "200.00", target="Checking")],
    }
    pairs, warnings = pair_transfers(parsed, _MAP)
    assert warnings == []
    assert len(pairs) == 1
    pair = pairs[0]
    # from is always the money-out (negative) leg.
    assert pair.from_account == "Checking"
    assert pair.to_account == "Savings"
    assert pair.from_line.amount.amount == Decimal("-200.00")
    assert pair.to_line.amount.amount == Decimal("200.00")


def test_orientation_is_independent_of_account_order() -> None:
    # Savings listed first, but the negative leg still becomes `from`.
    parsed = {
        "Savings": [_line(1, "200.00", target="Checking")],
        "Checking": [_line(1, "-200.00", target="Savings")],
    }
    pairs, warnings = pair_transfers(parsed, _MAP)
    assert warnings == []
    assert [(p.from_account, p.to_account) for p in pairs] == [("Checking", "Savings")]


def test_leg_with_no_reciprocal_warns_and_is_not_paired() -> None:
    parsed = {
        "Checking": [_line(1, "-200.00", target="Savings")],
        "Savings": [_line(1, "50.00", target=None)],  # plain deposit, not a transfer
    }
    pairs, warnings = pair_transfers(parsed, _MAP)
    assert pairs == []
    assert len(warnings) == 1
    assert "no matching reciprocal" in warnings[0]
    assert "Checking" in warnings[0]


def test_transfer_to_unmapped_account_warns() -> None:
    parsed = {"Checking": [_line(1, "-200.00", target="Brokerage")]}
    pairs, warnings = pair_transfers(parsed, _MAP)
    assert pairs == []
    assert len(warnings) == 1
    assert "unmapped account 'Brokerage'" in warnings[0]


def test_non_transfer_lines_are_ignored() -> None:
    parsed = {
        "Checking": [
            _line(1, "-58.99", target=None),
            ParsedStatementLine(
                line_number=2,
                posted_date=date(2026, 1, 11),
                amount=Money(Decimal("-12.00"), "USD"),
                description="groceries",
                counterparty=None,
                reference=None,
                fitid=None,
                raw=MappingProxyType({"L": "Expenses:Groceries"}),
            ),
        ],
        "Savings": [_line(1, "50.00", target=None)],
    }
    pairs, warnings = pair_transfers(parsed, _MAP)
    assert pairs == []
    assert warnings == []


def test_cross_currency_legs_do_not_pair() -> None:
    # Different currencies can't form a balanced transaction; the amount
    # check is currency-aware, so both legs fall through to warnings.
    parsed = {
        "Checking": [_line(1, "-200.00", target="Savings", currency="USD")],
        "Savings": [_line(1, "200.00", target="Checking", currency="EUR")],
    }
    pairs, warnings = pair_transfers(parsed, _MAP)
    assert pairs == []
    assert len(warnings) == 2


def test_date_mismatch_does_not_pair() -> None:
    parsed = {
        "Checking": [_line(1, "-200.00", target="Savings", on=date(2026, 1, 10))],
        "Savings": [_line(1, "200.00", target="Checking", on=date(2026, 1, 12))],
    }
    pairs, warnings = pair_transfers(parsed, _MAP)
    assert pairs == []
    assert len(warnings) == 2


def test_two_transfers_same_amount_pair_greedily() -> None:
    parsed = {
        "Checking": [
            _line(1, "-200.00", target="Savings"),
            _line(2, "-200.00", target="Savings"),
        ],
        "Savings": [
            _line(1, "200.00", target="Checking"),
            _line(2, "200.00", target="Checking"),
        ],
    }
    pairs, warnings = pair_transfers(parsed, _MAP)
    assert warnings == []
    assert len(pairs) == 2

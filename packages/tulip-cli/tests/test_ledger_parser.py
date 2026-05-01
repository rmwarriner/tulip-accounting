"""Unit tests for the ledger-subset parser used by ``tulip add --edit``.

The supported grammar (intentionally a strict subset of hledger syntax):

    [# or ; comment lines are stripped]
    [blank lines are ignored]

    YYYY-MM-DD <description>
      <account>  <amount> [<currency>]
      <account>  <amount> [<currency>]
      ...

A single transaction per buffer; ``tulip add --edit`` posts one. The
parser raises :class:`LedgerParseError` with a line-pointing message
on any malformed input, which the editor loop displays as a banner
on the next reopen.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tulip_cli.commands._ledger import (
    LedgerParseError,
    ParsedLedgerPosting,
    ParsedLedgerTransaction,
    parse_ledger_text,
)


def test_minimal_two_posting_transaction() -> None:
    text = """
    2026-05-01 Lunch
      expenses:food   12.50
      assets:checking -12.50
    """
    parsed = parse_ledger_text(text)
    assert parsed == ParsedLedgerTransaction(
        date=date(2026, 5, 1),
        description="Lunch",
        postings=(
            ParsedLedgerPosting(account="expenses:food", amount=Decimal("12.50"), currency=None),
            ParsedLedgerPosting(account="assets:checking", amount=Decimal("-12.50"), currency=None),
        ),
    )


def test_explicit_currency_per_posting() -> None:
    text = """
    2026-05-01 FX trade
      assets:cash:eur   100.00 EUR
      assets:cash:usd  -107.30 USD
    """
    parsed = parse_ledger_text(text)
    assert parsed.postings[0].currency == "EUR"
    assert parsed.postings[1].currency == "USD"


def test_comments_are_ignored() -> None:
    text = """
    # full-line comment
    ; another full-line
    2026-05-01 Lunch  ; inline ledger-style comment
      expenses:food  12.50  # inline hash comment
      assets:cash   -12.50
    """
    parsed = parse_ledger_text(text)
    assert parsed.description == "Lunch"
    assert len(parsed.postings) == 2
    assert parsed.postings[0].amount == Decimal("12.50")


def test_blank_lines_anywhere_are_fine() -> None:
    text = """


    2026-05-01 Coffee


      expenses:coffee  3.50

      assets:cash     -3.50

    """
    parsed = parse_ledger_text(text)
    assert parsed.description == "Coffee"
    assert len(parsed.postings) == 2


def test_three_or_more_postings() -> None:
    text = """
    2026-05-01 Split bill
      expenses:food            30.00
      assets:checking         -10.00
      assets:partner-owes-me  -20.00
    """
    parsed = parse_ledger_text(text)
    assert len(parsed.postings) == 3


def test_accounts_with_colons_and_dashes() -> None:
    text = """
    2026-05-01 Multi-segment
      assets:bank-of-the-west:joint:checking  10.00
      expenses:utilities:electricity         -10.00
    """
    parsed = parse_ledger_text(text)
    assert parsed.postings[0].account == "assets:bank-of-the-west:joint:checking"
    assert parsed.postings[1].account == "expenses:utilities:electricity"


def test_empty_buffer_raises() -> None:
    with pytest.raises(LedgerParseError):
        parse_ledger_text("")


def test_only_comments_raises() -> None:
    with pytest.raises(LedgerParseError, match="header"):
        parse_ledger_text("# just a comment\n; nothing else\n")


def test_header_missing_date_raises() -> None:
    with pytest.raises(LedgerParseError, match="header"):
        parse_ledger_text("Lunch\n  expenses:food 1.00\n  assets:cash -1.00\n")


def test_header_missing_description_raises() -> None:
    with pytest.raises(LedgerParseError, match="description"):
        parse_ledger_text("2026-05-01\n  expenses:food 1.00\n  assets:cash -1.00\n")


def test_no_postings_after_header_raises() -> None:
    with pytest.raises(LedgerParseError, match="postings"):
        parse_ledger_text("2026-05-01 Lunch\n")


def test_unparseable_amount_raises() -> None:
    with pytest.raises(LedgerParseError, match="amount"):
        parse_ledger_text("2026-05-01 Lunch\n  expenses:food not-a-number\n  assets:cash -1.00\n")


def test_unindented_after_header_raises_or_ignores() -> None:
    """A non-indented, non-blank, non-comment line after the header is invalid."""
    with pytest.raises(LedgerParseError):
        parse_ledger_text("2026-05-01 Lunch\n  expenses:food 1.00\nthis-is-not-a-posting\n")


def test_invalid_currency_raises() -> None:
    with pytest.raises(LedgerParseError, match="currency"):
        parse_ledger_text("2026-05-01 Lunch\n  expenses:food 1.00 LONGER\n  assets:cash -1.00\n")


def test_parse_error_carries_line_number() -> None:
    text = """
    2026-05-01 Lunch
      expenses:food not-a-number
      assets:cash  -1.00
    """
    with pytest.raises(LedgerParseError) as exc_info:
        parse_ledger_text(text)
    # The parse error should reference the offending line so the
    # reopen banner can point the user at it.
    assert "line" in str(exc_info.value).lower()

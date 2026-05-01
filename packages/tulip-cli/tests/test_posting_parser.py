"""Unit tests for the ``--post`` value parser used by ``tulip add``.

Format: ``account=amount[@CURRENCY]``. The account portion may contain
colons (e.g. ``assets:checking``); the parser splits on the **last**
``=`` so codes-with-colons resolve correctly. The optional ``@CURRENCY``
suffix lets the caller override the account's primary currency for FX
postings.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tulip_cli.commands.transactions import ParsedPosting, parse_posting


def test_simple_account_and_amount() -> None:
    p = parse_posting("expenses:food=12.50")
    assert p == ParsedPosting(account="expenses:food", amount=Decimal("12.50"), currency=None)


def test_negative_amount() -> None:
    p = parse_posting("assets:checking=-12.50")
    assert p.amount == Decimal("-12.50")
    assert p.account == "assets:checking"


def test_amount_with_explicit_currency() -> None:
    p = parse_posting("assets:cash=100@EUR")
    assert p.account == "assets:cash"
    assert p.amount == Decimal("100")
    assert p.currency == "EUR"


def test_uuid_account() -> None:
    uuid = "5b9c08a8-1c1c-4f12-9f6a-b6d3a2f4d4b8"
    p = parse_posting(f"{uuid}=42.00")
    assert p.account == uuid


def test_account_with_multiple_colons() -> None:
    p = parse_posting("assets:bank:checking:joint=10.00")
    assert p.account == "assets:bank:checking:joint"


def test_missing_equals_sign_raises() -> None:
    with pytest.raises(ValueError, match="account=amount"):
        parse_posting("just-an-account-name")


def test_empty_account_raises() -> None:
    with pytest.raises(ValueError, match="account"):
        parse_posting("=12.50")


def test_empty_amount_raises() -> None:
    with pytest.raises(ValueError, match="amount"):
        parse_posting("expenses:food=")


def test_non_decimal_amount_raises() -> None:
    with pytest.raises(ValueError, match="amount"):
        parse_posting("expenses:food=not-a-number")


def test_currency_must_be_three_letters() -> None:
    with pytest.raises(ValueError, match="currency"):
        parse_posting("assets:cash=10@LONGER")
    with pytest.raises(ValueError, match="currency"):
        parse_posting("assets:cash=10@US")

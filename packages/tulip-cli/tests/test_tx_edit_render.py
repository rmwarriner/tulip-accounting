"""Round-trip tests for the transactions-edit buffer renderer (#304).

The contract the issue calls out: a transaction rendered to the buffer
and saved *unmodified* must parse back to the identical posting set.
Pre-#304 the renderer fell back from ``code`` straight to the bare
UUID, which round-tripped fine but was unreadable for importer-created
accounts. Post-#304 the renderer falls back ``code → name → UUID`` and
the parser accepts hledger's two-space account/amount separator so
multi-word names also round-trip.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from tulip_cli.commands._ledger import parse_ledger_text
from tulip_cli.commands.transactions import (
    _account_display_for_edit,
    _render_tx_for_edit,
)


def _accounts(*entries: tuple[str, str | None, str]) -> dict[str, dict[str, Any]]:
    """Build an accounts-by-id dict from ``(id, code, name)`` tuples."""
    return {
        account_id: {"id": account_id, "code": code, "name": name}
        for account_id, code, name in entries
    }


def test_display_prefers_code_over_name() -> None:
    """A code-bearing account renders as its code (terser, namespaced)."""
    accounts_by_id = _accounts(("acc-1", "assets:checking", "Checking"))
    assert _account_display_for_edit("acc-1", accounts_by_id) == "assets:checking"


def test_display_falls_back_to_name_when_no_code() -> None:
    """A codeless account renders as its human-readable name (#304)."""
    accounts_by_id = _accounts(("acc-1", None, "Checking"))
    assert _account_display_for_edit("acc-1", accounts_by_id) == "Checking"


def test_display_falls_back_to_uuid_when_account_unknown() -> None:
    """Orphaned references render as the bare UUID — last-resort, but truthful."""
    accounts_by_id: dict[str, dict[str, Any]] = {}
    assert (
        _account_display_for_edit("11111111-1111-1111-1111-111111111111", accounts_by_id)
        == "11111111-1111-1111-1111-111111111111"
    )


def test_display_falls_back_to_uuid_when_neither_code_nor_name() -> None:
    """Both fields empty/null → UUID. Defensive against malformed API responses."""
    accounts_by_id = _accounts(("acc-1", None, ""))
    assert _account_display_for_edit("acc-1", accounts_by_id) == "acc-1"


def test_render_and_parse_round_trip_with_codes() -> None:
    """code-bearing postings render via code and parse back to the same identifiers."""
    accounts_by_id = _accounts(
        ("acc-1", "assets:checking", "Checking"),
        ("acc-2", "expenses:groceries", "Groceries"),
    )
    tx: dict[str, Any] = {
        "date": "2026-05-01",
        "description": "Lunch",
        "postings": [
            {"account_id": "acc-1", "amount": "-12.50", "currency": "USD"},
            {"account_id": "acc-2", "amount": "12.50", "currency": "USD"},
        ],
    }
    buffer = _render_tx_for_edit(tx, accounts_by_id)
    parsed = parse_ledger_text(buffer)

    assert parsed.description == "Lunch"
    assert [p.account for p in parsed.postings] == [
        "assets:checking",
        "expenses:groceries",
    ]
    assert [p.amount for p in parsed.postings] == [
        Decimal("-12.50"),
        Decimal("12.50"),
    ]


def test_render_and_parse_round_trip_with_codeless_single_word_names() -> None:
    """Codeless single-word names (the common QIF/OFX import case) round-trip cleanly."""
    accounts_by_id = _accounts(
        ("acc-1", None, "Checking"),
        ("acc-2", None, "Groceries"),
    )
    tx: dict[str, Any] = {
        "date": "2026-05-01",
        "description": "Lunch",
        "postings": [
            {"account_id": "acc-1", "amount": "-12.50", "currency": "USD"},
            {"account_id": "acc-2", "amount": "12.50", "currency": "USD"},
        ],
    }
    buffer = _render_tx_for_edit(tx, accounts_by_id)
    parsed = parse_ledger_text(buffer)

    assert [p.account for p in parsed.postings] == ["Checking", "Groceries"]
    # Crucially: no UUIDs appear in the buffer or in the parsed accounts.
    for posting in parsed.postings:
        assert "acc-" not in posting.account, (
            f"UUID leaked into buffer for codeless account: {posting.account}"
        )


def test_render_and_parse_round_trip_with_multi_word_name() -> None:
    """Multi-word names round-trip via the hledger two-space separator (#304)."""
    accounts_by_id = _accounts(
        ("acc-1", None, "My Checking Account"),
        ("acc-2", "expenses:groceries", "Groceries"),
    )
    tx: dict[str, Any] = {
        "date": "2026-05-01",
        "description": "Lunch",
        "postings": [
            {"account_id": "acc-1", "amount": "-12.50", "currency": "USD"},
            {"account_id": "acc-2", "amount": "12.50", "currency": "USD"},
        ],
    }
    buffer = _render_tx_for_edit(tx, accounts_by_id)
    parsed = parse_ledger_text(buffer)

    assert [p.account for p in parsed.postings] == [
        "My Checking Account",
        "expenses:groceries",
    ]


def test_render_emits_two_space_separator() -> None:
    """The renderer always emits at least two spaces — that's what the parser needs."""
    accounts_by_id = _accounts(("acc-1", "assets:checking", "Checking"))
    tx: dict[str, Any] = {
        "date": "2026-05-01",
        "description": "x",
        "postings": [{"account_id": "acc-1", "amount": "1.00", "currency": "USD"}],
    }
    buffer = _render_tx_for_edit(tx, accounts_by_id)
    # The buffer's posting line uses "  " (two spaces) between account
    # and amount; the renderer doesn't depend on the account being a
    # single token.
    assert "assets:checking  1.00 USD" in buffer

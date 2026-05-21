"""Unit tests for ``tulip_cli._qif_accounts.list_account_declarations`` (#443).

The CLI-local QIF scanner is a small subset of the full
``tulip_importers.qif`` parser. ARCHITECTURE.md §9 forbids the
CLI from importing ``tulip_importers``, so the scanner lives in
``tulip-cli`` as a self-contained module. These tests mirror the
parser-side tests; both must stay in sync.
"""

from __future__ import annotations

from tulip_cli._qif_accounts import (
    QifAccountDeclaration,
    list_account_declarations,
)


def test_returns_empty_for_no_account_blocks() -> None:
    qif = b"!Type:Bank\nD2026-05-01\nT100\n^\n"
    assert list_account_declarations(qif) == []


def test_extracts_name_and_type() -> None:
    qif = (
        b"!Account\nNChecking\nTBank\n^\n"
        b"!Account\nNVisa\nTCCard\n^\n"
        b"!Type:Bank\nD2026-05-01\nT100\n^\n"
    )
    assert list_account_declarations(qif) == [
        QifAccountDeclaration(name="Checking", qif_type="Bank"),
        QifAccountDeclaration(name="Visa", qif_type="CCard"),
    ]


def test_deduplicates_by_name() -> None:
    qif = (
        b"!Account\nNChecking\nTBank\n^\n"
        b"!Type:Bank\nD2026-05-01\nT100\n^\n"
        b"!Account\nNChecking\nTBank\n^\n"
        b"!Type:Bank\nD2026-06-01\nT200\n^\n"
    )
    declarations = list_account_declarations(qif)
    assert len(declarations) == 1
    assert declarations[0].name == "Checking"


def test_handles_missing_type_line() -> None:
    qif = b"!Account\nNOpening Balances\n^\n"
    decls = list_account_declarations(qif)
    assert len(decls) == 1
    assert decls[0].name == "Opening Balances"
    assert decls[0].qif_type == ""


def test_preserves_first_seen_order() -> None:
    qif = b"!Account\nNZ\nTBank\n^\n!Account\nNA\nTCCard\n^\n!Account\nNM\nTInvst\n^\n"
    names = [d.name for d in list_account_declarations(qif)]
    assert names == ["Z", "A", "M"]

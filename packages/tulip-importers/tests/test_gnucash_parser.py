"""Unit tests for the GnuCash account-tree CSV parser (#432)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tulip_importers.gnucash import (
    GnuCashParseError,
    parse,
    sort_by_depth,
    type_for_gnucash,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "gnucash" / "sample_tree.csv"


def _sample_text() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


def test_parse_full_fixture() -> None:
    accounts = parse(_sample_text())
    # Fixture has 13 rows after the header.
    assert len(accounts) == 13


def test_type_mapping_covers_every_gnucash_type() -> None:
    # Every type in the fixture maps cleanly.
    for gnucash, expected_tulip_type in [
        ("ASSET", "asset"),
        ("BANK", "asset"),
        ("CASH", "asset"),
        ("STOCK", "asset"),
        ("MUTUAL", "asset"),
        ("LIABILITY", "liability"),
        ("CREDIT", "liability"),
        ("EQUITY", "equity"),
        ("INCOME", "income"),
        ("EXPENSE", "expense"),
    ]:
        tulip_type, _ = type_for_gnucash(gnucash)
        assert tulip_type == expected_tulip_type, gnucash


def test_type_mapping_unknown_raises() -> None:
    with pytest.raises(GnuCashParseError):
        type_for_gnucash("FUTURE_TYPE")


def test_subtype_inferred_for_specialised_types() -> None:
    accounts = parse(_sample_text())
    by_name = {a.name: a for a in accounts}
    assert by_name["Checking"].subtype == "bank"
    assert by_name["SPAXX Position"].subtype == "stock"
    assert by_name["Visa"].subtype == "credit_card"
    # A plain ASSET row doesn't get a subtype.
    assert by_name["Assets"].subtype is None


def test_depth_is_count_of_colons() -> None:
    accounts = parse(_sample_text())
    by_path = {a.full_path: a for a in accounts}
    assert by_path["Assets"].depth == 0
    assert by_path["Assets:Current"].depth == 1
    assert by_path["Assets:Current:Checking"].depth == 2
    assert by_path["Expenses:Food:Groceries"].depth == 2


def test_sort_by_depth_puts_parents_first() -> None:
    accounts = parse(_sample_text())
    sorted_ = sort_by_depth(accounts)
    seen_paths: set[str] = set()
    for account in sorted_:
        parent_path = ":".join(account.full_path.split(":")[:-1])
        if parent_path:
            assert parent_path in seen_paths, (
                f"parent {parent_path!r} of {account.full_path!r} "
                "must appear before child in depth-sorted order"
            )
        seen_paths.add(account.full_path)


def test_currency_from_symbol_when_namespace_is_currency() -> None:
    accounts = parse(_sample_text())
    by_name = {a.name: a for a in accounts}
    assert by_name["Checking"].currency == "USD"
    assert by_name["Checking"].warning is None


def test_non_currency_holding_lands_in_default_currency_with_warning() -> None:
    accounts = parse(_sample_text(), default_currency="USD")
    by_name = {a.name: a for a in accounts}
    spaxx = by_name["SPAXX Position"]
    assert spaxx.currency == "USD"
    assert spaxx.warning == "non_currency_holding"
    # Original symbol + namespace get stashed in notes so the operator
    # can find these later when investment tracking lands.
    assert spaxx.notes is not None
    assert "SPAXX" in spaxx.notes
    assert "Fidelity" in spaxx.notes


def test_non_currency_holding_respects_default_currency_override() -> None:
    accounts = parse(_sample_text(), default_currency="EUR")
    spaxx = next(a for a in accounts if a.name == "SPAXX Position")
    assert spaxx.currency == "EUR"


def test_hidden_flag_maps_to_inactive() -> None:
    accounts = parse(_sample_text())
    visa = next(a for a in accounts if a.name == "Visa")
    assert visa.is_active is False
    # Active accounts read T as inactive=False.
    checking = next(a for a in accounts if a.name == "Checking")
    assert checking.is_active is True


def test_placeholder_flag_maps_through() -> None:
    accounts = parse(_sample_text())
    assets_root = next(a for a in accounts if a.full_path == "Assets")
    assert assets_root.is_placeholder is True
    checking = next(a for a in accounts if a.name == "Checking")
    assert checking.is_placeholder is False


def test_description_lands_in_notes_when_notes_field_is_blank() -> None:
    accounts = parse(_sample_text())
    by_name = {a.name: a for a in accounts}
    # Salary has a Description but no Notes — Description wins.
    assert by_name["Salary"].notes == "Day job"


def test_notes_field_wins_over_description() -> None:
    accounts = parse(_sample_text())
    by_name = {a.name: a for a in accounts}
    # Checking has both; Notes wins (it's the explicit field).
    assert by_name["Checking"].notes == "*4218 Big Bank"
    # Visa has Notes='Cancelled 2024' — also explicit.
    assert by_name["Visa"].notes == "Cancelled 2024"


def test_empty_description_and_notes_yields_none() -> None:
    accounts = parse(_sample_text())
    groceries = next(a for a in accounts if a.name == "Groceries")
    assert groceries.notes is None


def test_account_code_round_trips() -> None:
    accounts = parse(_sample_text())
    by_name = {a.name: a for a in accounts}
    assert by_name["Checking"].code == "11100"
    assert by_name["Groceries"].code == "51100"


def test_blank_header_raises() -> None:
    with pytest.raises(GnuCashParseError, match="header"):
        parse("not,a,gnucash,csv\n")


def test_blank_name_raises() -> None:
    bad = (
        "Type,Full Account Name,Account Name,Account Code,Description,"
        "Account Color,Notes,Symbol,Namespace,Hidden,Tax Info,Placeholder\n"
        "ASSET,Assets,,10000,,,,USD,CURRENCY,F,F,T\n"
    )
    with pytest.raises(GnuCashParseError, match="Account Name"):
        parse(bad)


def test_unknown_type_raises_with_row_number() -> None:
    bad = (
        "Type,Full Account Name,Account Name,Account Code,Description,"
        "Account Color,Notes,Symbol,Namespace,Hidden,Tax Info,Placeholder\n"
        "MYSTERY,Mystery,Mystery,99999,,,,USD,CURRENCY,F,F,F\n"
    )
    with pytest.raises(GnuCashParseError, match="row 2"):
        parse(bad)

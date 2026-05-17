"""Unit tests for tulip_importers.qif.parse."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tulip_core.reconciliation import ParsedStatementLine
from tulip_importers.qif import QifParseError, parse, split_accounts, transfer_target

FIXTURES = Path(__file__).parent / "fixtures" / "qif"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class TestParseHappy:
    def test_minimal_returns_three_lines(self):
        lines = parse(_read("minimal.qif"), currency="USD")
        assert len(lines) == 3
        assert all(isinstance(line, ParsedStatementLine) for line in lines)

    def test_field_mapping(self):
        # Source-file order; line_number is 1-based.
        lines = parse(_read("minimal.qif"), currency="USD")
        amazon, paycheck, lunch = lines

        assert amazon.line_number == 1
        assert amazon.posted_date == date(2026, 5, 12)
        assert amazon.amount.amount == Decimal("-42.17")
        assert amazon.amount.currency == "USD"
        # Description = payee + " " + memo (matches OFX convention).
        assert "PAYPAL" in amazon.description
        assert "AMAZON KINDLE" in amazon.description
        # N field → reference.
        assert amazon.reference == "CHECK1234"
        # raw carries the type header + the original field values.
        assert amazon.raw.get("TYPE") == "Bank"

        assert paycheck.line_number == 2
        assert paycheck.amount.amount == Decimal("1500.00")
        # Memo absent → description has only payee.
        assert "PAYROLL" in paycheck.description
        assert paycheck.reference is None

        # ISO date support.
        assert lunch.posted_date == date(2026, 5, 20)

    def test_two_digit_year_rolls_to_2000s(self):
        # MM/DD/YY → 20YY (no banks emitting 19xx files in 2026+).
        lines = parse(_read("two_digit_year.qif"), currency="USD")
        assert lines[0].posted_date == date(2026, 5, 12)

    def test_currency_arg_is_used(self):
        # QIF carries no currency; caller (API) supplies the account's.
        lines = parse(_read("minimal.qif"), currency="EUR")
        assert lines[0].amount.currency == "EUR"

    def test_empty_qif_returns_empty_list(self):
        # Header-only file (no transactions) returns [].
        lines = parse(_read("empty.qif"), currency="USD")
        assert lines == []

    def test_no_type_header_still_parses(self):
        # !Type:Bank header is conventional but optional; some banks omit it.
        lines = parse(_read("no_header.qif"), currency="USD")
        assert len(lines) == 1
        assert lines[0].amount.amount == Decimal("-42.17")


class TestParseSplits:
    """Per #297: one ParsedStatementLine with consolidated total + structured splits.

    QIF encodes a multi-category transaction as a single record with
    ``S`` (split category), ``$`` (split amount), and ``E`` (split memo)
    field-triples. Banktivity's gas-bill export from #270 is the
    canonical 2-split debit case; the BNSF paycheck shape is the
    multi-split credit case (a positive gross + negative withholdings
    netting to the deposited total).

    #270's original shape (N lines per N-way split) broke "one external
    event = one transaction" — a single bank-cleared payment showed up
    as N separate PENDING transactions, and bank-statement reconciliation
    couldn't pair one statement line against N ledger transactions.
    #297 fixes this: the parser emits ONE line with ``amount = T``, and
    the per-category breakdown lives in the new ``splits`` tuple.
    Apply-side promotes this to one transaction with ``1 + len(splits)``
    postings (one bank-side + one per split).
    """

    def test_split_gas_bill_one_line_with_two_splits(self):
        # The exact snippet from #270 / #297: -45.27 + -13.72 = -58.99.
        lines = parse(_read("split_gas_bill.qif"), currency="USD")
        assert len(lines) == 1

        (line,) = lines
        # Consolidated parent total — what hit the bank.
        assert line.amount.amount == Decimal("-58.99")
        # The two splits sit on the line, not as siblings.
        assert len(line.splits) == 2
        gas, warranty = line.splits
        assert gas.amount.amount == Decimal("-45.27")
        assert warranty.amount.amount == Decimal("-13.72")
        # Sanity: parser-enforced sum check.
        assert sum(s.amount.amount for s in line.splits) == line.amount.amount

    def test_split_gas_bill_carries_per_split_category_and_memo(self):
        ((line,)) = parse(_read("split_gas_bill.qif"), currency="USD")
        gas, warranty = line.splits

        # Per-split QIF S-field on the structured ParsedSplit.
        assert gas.category == "Needs:Utilities:Natural Gas/TulipDrive"
        assert warranty.category == "Needs:Insurance:Home Warranty/TulipDrive"
        # Per-split memo (the QIF ``E`` line right before each ``S``).
        assert gas.memo == "Current gas charges"
        assert warranty.memo == "Current home service charges"

    def test_split_gas_bill_inherits_parent_payee_and_date(self):
        ((line,)) = parse(_read("split_gas_bill.qif"), currency="USD")
        # The parent record's date + payee survive on the consolidated line.
        assert line.posted_date == date(2026, 1, 2)
        assert line.counterparty == "CenterPoint Energy"
        # One line, one line_number.
        assert line.line_number == 1
        # The line's currency matches every split's currency (enforced
        # in ParsedStatementLine.__post_init__).
        for split in line.splits:
            assert split.amount.currency == line.amount.currency

    def test_split_paycheck_emits_one_line_with_four_splits(self):
        # BNSF gross-paycheck shape: +3500 wages, -420 fed, -150 state,
        # -115.50 FICA, netting to +2814.50 deposited.
        lines = parse(_read("split_paycheck.qif"), currency="USD")
        assert len(lines) == 1

        (line,) = lines
        assert line.amount.amount == Decimal("2814.50")
        assert len(line.splits) == 4

        amounts = [s.amount.amount for s in line.splits]
        assert amounts == [
            Decimal("3500.00"),
            Decimal("-420.00"),
            Decimal("-150.00"),
            Decimal("-115.50"),
        ]
        # Per-split category survives parsing.
        categories = [s.category for s in line.splits]
        assert categories == [
            "Income:Wages/BNSF",
            "Expenses:Taxes:Federal/BNSF",
            "Expenses:Taxes:State/BNSF",
            "Expenses:Taxes:FICA/BNSF",
        ]

    def test_split_sum_mismatch_raises(self):
        # Per #270 + #297: "If split amounts don't sum to T, the row is
        # rejected with an import error rather than silently dropped."
        with pytest.raises(QifParseError, match="split"):
            parse(_read("split_sum_mismatch.qif"), currency="USD")

    def test_single_line_unsplit_has_empty_splits_tuple(self):
        # Regression: non-split QIF entries continue to produce exactly
        # one ParsedStatementLine each, with an empty ``splits`` tuple
        # — preserving the two-posting promotion shape.
        lines = parse(_read("minimal.qif"), currency="USD")
        assert len(lines) == 3  # three records, no splits, three lines.
        for line in lines:
            # Non-split lines carry no S-field; splits is empty.
            assert line.splits == ()
            assert "L" not in line.raw


class TestParseSectionSkipping:
    """Per #198: skip the preamble desktop apps wrap transactions in.

    Banktivity / Quicken / GnuCash exports open with ``!Option`` /
    ``!Clear`` directives, an ``!Account`` declaration block, a full
    ``!Type:Cat`` category list, then the transaction-bearing
    ``!Type:Bank`` section, then a ``!Type:Security`` list. Only the
    ``!Type:Bank`` records are transactions; everything else must be
    skipped without erroring.
    """

    def test_banktivity_preamble_lands_only_bank_transactions(self):
        lines = parse(_read("banktivity_preamble.qif"), currency="USD")
        # The fixture has exactly two !Type:Bank records; the !Account
        # block, the 3-row !Type:Cat list, and the 2-row !Type:Security
        # list all produce zero statement lines.
        assert len(lines) == 2
        gas, payroll = lines
        assert gas.posted_date == date(2026, 1, 2)
        assert gas.amount.amount == Decimal("-58.99")
        assert "CenterPoint Energy" in gas.description
        assert payroll.posted_date == date(2026, 1, 5)
        assert payroll.amount.amount == Decimal("1500.00")
        # The Bank type header still flows onto the parsed rows.
        assert gas.raw.get("TYPE") == "Bank"

    def test_cat_section_records_are_not_transactions(self):
        # A bare !Type:Cat section with category records and nothing else
        # parses to zero lines — not a "missing date/amount" error.
        cat_only = b"!Type:Cat\nNGroceries\nE\n^\nNSalary\nI\n^\n"
        assert parse(cat_only, currency="USD") == []

    def test_security_section_records_are_not_transactions(self):
        sec_only = b"!Type:Security\nNAcme Corp\nSACME\nTStock\n^\n"
        assert parse(sec_only, currency="USD") == []

    def test_account_block_is_skipped(self):
        # An !Account declaration block followed by a real !Type:Bank
        # section: the block is skipped, the transaction lands.
        qif = b"!Account\nNChecking\nTBank\nB100.00\n^\n!Type:Bank\nD1/2/26\nT-10.00\nPStore\n^\n"
        lines = parse(qif, currency="USD")
        assert len(lines) == 1
        assert lines[0].amount.amount == Decimal("-10.00")

    def test_option_directives_are_skipped(self):
        qif = b"!Option:AutoSwitch\n!Type:Bank\nD1/2/26\nT5.00\nPX\n^\n!Clear:AutoSwitch\n"
        lines = parse(qif, currency="USD")
        assert len(lines) == 1
        assert lines[0].amount.amount == Decimal("5.00")

    def test_non_txn_section_after_bank_section_stops_parsing(self):
        # Records under !Type:Security that follow a !Type:Bank section
        # must not be mis-read as transactions.
        qif = b"!Type:Bank\nD1/2/26\nT5.00\nPX\n^\n!Type:Security\nNAcme\nSACME\n^\n"
        lines = parse(qif, currency="USD")
        assert len(lines) == 1
        assert lines[0].amount.amount == Decimal("5.00")

    def test_unknown_type_is_still_parsed(self):
        # An unrecognised !Type: label is parsed, not skipped — silently
        # dropping a bank's transactions is the worse failure mode.
        qif = b"!Type:SomethingNew\nD1/2/26\nT9.99\nPX\n^\n"
        lines = parse(qif, currency="USD")
        assert len(lines) == 1
        assert lines[0].amount.amount == Decimal("9.99")
        assert lines[0].raw.get("TYPE") == "SomethingNew"


class TestSplitAccounts:
    """Per #195a: split a multi-account QIF into per-account chunks.

    A multi-account QIF interleaves ``!Account`` blocks with
    transaction-bearing ``!Type:`` sections. ``split_accounts`` walks
    that structure and returns one verbatim, independently-parseable
    chunk per distinct account name.
    """

    def test_multi_account_yields_one_chunk_per_account(self):
        chunks = split_accounts(_read("multi_account.qif"))
        assert [c.account_name for c in chunks] == ["Checking", "Savings", "Credit Card"]

    def test_each_chunk_is_independently_parseable(self):
        chunks = split_accounts(_read("multi_account.qif"))
        by_name = {c.account_name: c for c in chunks}

        checking = parse(by_name["Checking"].qif_text.encode("utf-8"), currency="USD")
        assert [line.amount.amount for line in checking] == [
            Decimal("-58.99"),
            Decimal("1500.00"),
        ]
        savings = parse(by_name["Savings"].qif_text.encode("utf-8"), currency="USD")
        assert [line.amount.amount for line in savings] == [Decimal("200.00")]
        credit = parse(by_name["Credit Card"].qif_text.encode("utf-8"), currency="USD")
        assert [line.amount.amount for line in credit] == [Decimal("-42.00")]
        # The Credit Card chunk preserved its own !Type:CCard header.
        assert credit[0].raw.get("TYPE") == "CCard"

    def test_single_account_qif_yields_no_chunks(self):
        # No !Account blocks → empty list → caller uses the --account path.
        assert split_accounts(_read("minimal.qif")) == []

    def test_one_named_account_yields_one_chunk(self):
        # The #198 Banktivity fixture has !Account blocks but only one
        # distinct name — split returns a single chunk; the caller's
        # "2+ distinct accounts" rule still routes it to --account.
        chunks = split_accounts(_read("banktivity_preamble.qif"))
        assert [c.account_name for c in chunks] == ["Checking"]

    def test_non_transaction_sections_are_not_chunked(self):
        # !Type:Cat / !Type:Security records never land in a chunk.
        chunks = split_accounts(_read("banktivity_preamble.qif"))
        checking = parse(chunks[0].qif_text.encode("utf-8"), currency="USD")
        # Only the two real !Type:Bank transactions, no category/security rows.
        assert len(checking) == 2


class TestTransferTarget:
    """Per #195b: a QIF transfer marks its other side as L[Account Name]."""

    def test_bracketed_category_is_a_transfer_target(self):
        assert transfer_target({"L": "[Checking]"}) == "Checking"

    def test_account_name_with_spaces(self):
        assert transfer_target({"L": "[Credit Card]"}) == "Credit Card"

    def test_brackets_and_padding_are_stripped(self):
        assert transfer_target({"L": "  [ Savings ] "}) == "Savings"

    def test_plain_category_is_not_a_transfer(self):
        assert transfer_target({"L": "Expenses:Groceries"}) is None

    def test_missing_l_field_is_not_a_transfer(self):
        assert transfer_target({}) is None
        assert transfer_target({"P": "Some Payee"}) is None


class TestParseErrors:
    def test_empty_bytes_raises(self):
        with pytest.raises(QifParseError):
            parse(b"", currency="USD")

    def test_garbage_bytes_raises(self):
        # No `^` separator + no valid field codes = not QIF.
        with pytest.raises(QifParseError):
            parse(b"this is not a qif file at all", currency="USD")

    def test_record_missing_amount_raises(self):
        # T (amount) is mandatory per ADR §Q8 — without it the line can't
        # produce a Money value object. Surface line context.
        bad = b"!Type:Bank\nD5/12/2026\nPPAYPAL\n^\n"
        with pytest.raises(QifParseError, match="amount"):
            parse(bad, currency="USD")

    def test_record_missing_date_raises(self):
        bad = b"!Type:Bank\nT-12.50\nPPAYPAL\n^\n"
        with pytest.raises(QifParseError, match="date"):
            parse(bad, currency="USD")

    def test_unparseable_amount_raises(self):
        bad = b"!Type:Bank\nD5/12/2026\nTnot-a-number\nPPAYPAL\n^\n"
        with pytest.raises(QifParseError, match="amount"):
            parse(bad, currency="USD")

    def test_unparseable_date_raises(self):
        bad = b"!Type:Bank\nDgarbage\nT-12.50\nPPAYPAL\n^\n"
        with pytest.raises(QifParseError, match="date"):
            parse(bad, currency="USD")


class TestClearedField:
    """#279: the C (cleared) field is captured per-transaction in ``raw``."""

    def test_c_field_appears_in_raw_dict(self):
        """Empty C / `c` / `R` / `*` round-trip through the parser's raw dict."""
        lines = parse(_read("cleared_status.qif"), currency="USD")
        assert len(lines) == 5
        # Coffee: no C field.
        assert "C" not in lines[0].raw
        # Gas: C=c (Banktivity "cleared").
        assert lines[1].raw["C"] == "c"
        # Groceries: C=R (reconciled).
        assert lines[2].raw["C"] == "R"
        # Paycheck: C=R.
        assert lines[3].raw["C"] == "R"
        # Snack: C=* (legacy cleared marker).
        assert lines[4].raw["C"] == "*"

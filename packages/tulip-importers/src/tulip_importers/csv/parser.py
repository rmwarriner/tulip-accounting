"""CSV parser — bytes + profile + currency → list[ParsedStatementLine]."""

from __future__ import annotations

import csv as csv_stdlib
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from tulip_core.money import Money
from tulip_core.reconciliation import ParsedStatementLine

if TYPE_CHECKING:
    from tulip_importers.csv.profile import CsvProfile


class CsvParseError(Exception):
    """The provided bytes could not be parsed as CSV per the supplied profile."""


def _decode(file_bytes: bytes, encoding: str) -> str:
    # When the user picked utf-8, transparently strip a BOM (Excel exports
    # commonly include one). For other encodings we honor what the user
    # said — they configured it on purpose.
    effective = "utf-8-sig" if encoding.lower().replace("_", "-") == "utf-8" else encoding
    try:
        return file_bytes.decode(effective)
    except (UnicodeDecodeError, LookupError) as exc:
        raise CsvParseError(f"could not decode bytes as {encoding!r}: {exc}") from exc


def parse(
    file_bytes: bytes,
    *,
    profile: CsvProfile,
    currency: str,
) -> list[ParsedStatementLine]:
    """Parse CSV bytes into ``ParsedStatementLine`` rows per ``profile``.

    Row numbers in error messages are 1-based on data rows (after
    ``skip_header_rows`` and the one header row consumed by ``DictReader``)
    — what the operator sees as "row N" in the bank's UI.
    """
    if not file_bytes:
        raise CsvParseError("csv file is empty")

    text = _decode(file_bytes, profile.encoding)

    # Drop pre-header metadata rows so DictReader sees the column header on
    # the first line. profile.skip_header_rows is the count *including* the
    # column header line — so we drop (skip_header_rows - 1) rows before
    # handing to DictReader (it consumes one for the header itself).
    lines = text.splitlines(keepends=True)
    drop = max(profile.skip_header_rows - 1, 0)
    if drop:
        lines = lines[drop:]
    if not lines:
        raise CsvParseError(f"csv has fewer rows than skip_header_rows={profile.skip_header_rows}")

    reader = csv_stdlib.DictReader(
        io.StringIO("".join(lines)),
        delimiter=profile.delimiter,
    )

    if reader.fieldnames is None:
        raise CsvParseError("csv has no header row")

    # Validate that the profile's columns exist in the CSV header — the
    # alternative is mysterious KeyErrors on the first data row.
    missing = [
        c
        for c in (
            profile.date_column,
            profile.amount_column,
            profile.description_column,
        )
        if c not in reader.fieldnames
    ]
    if missing:
        raise CsvParseError(
            f"csv header is missing columns required by the profile: "
            f"{missing!r} (header has: {list(reader.fieldnames)!r})"
        )

    sign_flip = profile.amount_negative_means == "credit"
    out: list[ParsedStatementLine] = []
    row_no = 0
    for raw_row in reader:
        # Skip blank rows (DictReader produces None values for them).
        if not any(v and v.strip() for v in raw_row.values() if v is not None):
            continue
        row_no += 1

        date_str = (raw_row.get(profile.date_column) or "").strip()
        if not date_str:
            raise CsvParseError(f"row {row_no}: date column {profile.date_column!r} is empty")
        try:
            posted_date = datetime.strptime(date_str, profile.date_format).date()
        except ValueError as exc:
            raise CsvParseError(
                f"row {row_no}: date {date_str!r} doesn't match "
                f"format {profile.date_format!r}: {exc}"
            ) from exc

        amount_str = (raw_row.get(profile.amount_column) or "").strip().replace(",", "")
        if not amount_str:
            raise CsvParseError(f"row {row_no}: amount column {profile.amount_column!r} is empty")
        try:
            amount = Decimal(amount_str)
        except InvalidOperation as exc:
            raise CsvParseError(
                f"row {row_no}: amount {amount_str!r} isn't a decimal number"
            ) from exc
        if sign_flip:
            amount = -amount

        description = (raw_row.get(profile.description_column) or "").strip()
        if not description:
            description = "<no description>"

        counterparty: str | None = None
        if profile.counterparty_column:
            counterparty = (raw_row.get(profile.counterparty_column) or "").strip() or None

        reference: str | None = None
        if profile.reference_column:
            reference = (raw_row.get(profile.reference_column) or "").strip() or None

        # Stash every original column in `raw` for audit trail.
        raw_dict: dict[str, str] = {
            k: (v if v is not None else "") for k, v in raw_row.items() if k is not None
        }

        out.append(
            ParsedStatementLine(
                line_number=row_no,
                posted_date=posted_date,
                amount=Money(amount, currency),
                description=description,
                counterparty=counterparty,
                reference=reference,
                raw=raw_dict,
            )
        )

    return out

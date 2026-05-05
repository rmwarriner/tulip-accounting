"""QIF parser — bytes in, list[ParsedStatementLine] out (P5.2.b).

Per ADR-0004 §Q8: "QIF — custom parser (small format, public domain)".
QIF (Quicken Interchange Format) is a line-oriented text format. Each
transaction is a block of single-letter-prefixed fields ending with a
``^`` separator. The format predates currency awareness; the API caller
supplies the account's currency since the file itself doesn't carry one.

Field-code mapping (per ADR §Q8):

- ``D`` → ``posted_date`` (multiple date formats supported; see below).
- ``T`` → ``amount.amount``.
- ``P`` → ``counterparty`` (and folded into ``description``).
- ``M`` → ``description`` (concatenated with ``P``).
- ``N`` → ``reference`` (check number).
- ``^`` → record separator.

Date parsing handles three common dialects:

- ISO: ``2026-05-12``.
- US 4-digit: ``5/12/2026``.
- US 2-digit: ``5/12/26`` (rolls to 20YY — no QIF-emitting bank issues
  19xx files in 2026+).

Errors raise :class:`QifParseError` with the failing line number for
debuggability — banks ship malformed QIF often and operators need to
locate the bad row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as date_type
from decimal import Decimal, InvalidOperation

from tulip_core.money import Money
from tulip_core.reconciliation import ParsedStatementLine

#: Each transaction record ends with this single-character line.
_RECORD_TERMINATOR = "^"

#: Header line introduces the account type. Conventional but optional.
_HEADER_RE = re.compile(r"^!Type:(.+)$", re.IGNORECASE)

#: ISO date — try this first; unambiguous.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: US date — accept 1- or 2-digit month/day; year is 2 or 4 digits.
_US_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$")


class QifParseError(Exception):
    """The provided bytes could not be parsed as QIF."""


@dataclass(slots=True)
class _RecordBuilder:
    """Mutable accumulator for a single QIF record (between two ``^`` lines)."""

    line_number: int
    raw: dict[str, str] = field(default_factory=dict)
    payee: str | None = None
    memo: str | None = None
    amount_str: str | None = None
    date_str: str | None = None
    reference: str | None = None

    def has_any_field(self) -> bool:
        return bool(self.amount_str or self.date_str or self.payee or self.memo or self.reference)


def _parse_date(value: str, *, source_line: int) -> date_type:
    """Parse a QIF date string in ISO, US 4-digit, or US 2-digit form."""
    value = value.strip()
    if not value:
        raise QifParseError(f"line {source_line}: date field is empty")
    if _ISO_DATE_RE.match(value):
        try:
            return date_type.fromisoformat(value)
        except ValueError as exc:
            raise QifParseError(
                f"line {source_line}: date {value!r} is not a valid ISO date"
            ) from exc
    m = _US_DATE_RE.match(value)
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return date_type(year, month, day)
        except ValueError as exc:
            raise QifParseError(f"line {source_line}: date {value!r} is out of range") from exc
    raise QifParseError(
        f"line {source_line}: date {value!r} is not in a recognized format "
        "(expected YYYY-MM-DD or M/D/YY[YY])"
    )


def _parse_amount(value: str, *, source_line: int) -> Decimal:
    """Parse a QIF amount string. Strips comma-thousands separators."""
    value = value.strip().replace(",", "")
    if not value:
        raise QifParseError(f"line {source_line}: amount field is empty")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise QifParseError(
            f"line {source_line}: amount {value!r} is not a decimal number"
        ) from exc


def _finalize_record(
    rec: _RecordBuilder,
    *,
    line_number: int,
    currency: str,
    account_type: str | None,
) -> ParsedStatementLine:
    """Convert an accumulated record into a ParsedStatementLine."""
    if not rec.amount_str:
        raise QifParseError(f"line {rec.line_number}: record is missing amount (T) field")
    if not rec.date_str:
        raise QifParseError(f"line {rec.line_number}: record is missing date (D) field")
    posted_date = _parse_date(rec.date_str, source_line=rec.line_number)
    amount = _parse_amount(rec.amount_str, source_line=rec.line_number)

    parts = [(rec.payee or "").strip(), (rec.memo or "").strip()]
    description = " ".join(p for p in parts if p) or "<no description>"

    raw = dict(rec.raw)
    if account_type:
        raw["TYPE"] = account_type

    return ParsedStatementLine(
        line_number=line_number,
        posted_date=posted_date,
        amount=Money(amount, currency),
        description=description,
        counterparty=(rec.payee or None) if rec.payee else None,
        reference=rec.reference,
        raw=raw,
    )


def parse(file_bytes: bytes, *, currency: str) -> list[ParsedStatementLine]:
    """Parse QIF bytes into :class:`ParsedStatementLine` objects.

    Args:
        file_bytes: Raw file content.
        currency: ISO 4217 code applied to every line. QIF doesn't carry
            its own currency; the API supplies the account's.

    Returns:
        One :class:`ParsedStatementLine` per record. Empty list when the
        QIF has a header but no transactions.

    Raises:
        QifParseError: bytes are empty, malformed, or contain a record
            missing its mandatory date / amount field. Errors carry the
            source-line number to help operators locate the bad row.

    """
    if not file_bytes:
        raise QifParseError("qif file is empty")

    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise QifParseError(f"qif file is not valid UTF-8: {exc}") from exc

    out: list[ParsedStatementLine] = []
    record_no = 0
    rec: _RecordBuilder | None = None
    account_type: str | None = None
    saw_anything = False
    saw_qif_marker = False  # ^ separator OR !Type: header OR known D/T field

    for source_line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue
        saw_anything = True

        # Header line — applies to all subsequent records.
        if line.startswith("!"):
            saw_qif_marker = True
            m = _HEADER_RE.match(line)
            if m:
                account_type = m.group(1).strip()
                continue
            # Unknown bang directive — skip with a raw record marker.
            continue

        # Record terminator.
        if line == _RECORD_TERMINATOR:
            saw_qif_marker = True
            if rec is None or not rec.has_any_field():
                # Stray ^ between records; ignore silently.
                rec = None
                continue
            record_no += 1
            out.append(
                _finalize_record(
                    rec,
                    line_number=record_no,
                    currency=currency,
                    account_type=account_type,
                )
            )
            rec = None
            continue

        # Field line: first character is the field code; rest is the value.
        if rec is None:
            rec = _RecordBuilder(line_number=source_line_number)
        code = line[0]
        value = line[1:]
        rec.raw[code] = value
        if code == "D":
            saw_qif_marker = True
            rec.date_str = value
        elif code == "T":
            saw_qif_marker = True
            rec.amount_str = value
        elif code == "P":
            rec.payee = value
        elif code == "M":
            rec.memo = value
        elif code == "N":
            rec.reference = value.strip() or None
        # Other codes (C cleared status, A address, etc.) are stashed in `raw`
        # but don't drive ParsedStatementLine fields directly.

    # Trailing record without `^` (rare but legal in some emitters): finalize.
    if rec is not None and rec.has_any_field():
        record_no += 1
        out.append(
            _finalize_record(
                rec,
                line_number=record_no,
                currency=currency,
                account_type=account_type,
            )
        )

    if not saw_anything:
        raise QifParseError("qif file contained no recognizable content")
    if not saw_qif_marker:
        # Bytes parsed as text but contained no QIF structural markers
        # (^ record separators, !Type: headers, or D/T fields). The user
        # uploaded a different file shape.
        raise QifParseError(
            "qif file contained no recognizable QIF markers "
            "(no ^ record separators, no !Type: header, no D/T fields)"
        )

    return out

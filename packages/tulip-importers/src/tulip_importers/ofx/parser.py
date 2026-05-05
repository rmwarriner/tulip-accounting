"""OFX parser — bytes in, list[ParsedStatementLine] out.

Per ADR-0004 §Q8, the importer's job is to translate format-specific
input into the common ``ParsedStatementLine`` shape. This module wraps
``ofxtools`` for the heavy lifting (OFX 1.x SGML and OFX 2.x XML alike)
and squashes its broad surface to a single typed function.

Mapping (per ADR §Q8):

- ``STMTTRN.DTPOSTED → posted_date``
- ``STMTTRN.TRNAMT  → amount.amount``
- ``CURDEF          → amount.currency``
- ``STMTTRN.NAME + ' ' + STMTTRN.MEMO → description`` (stripped, single-spaced)
- ``STMTTRN.FITID   → reference``  (and stashed in ``raw['FITID']``)
- ``STMTTRN.TRNTYPE → raw['TRNTYPE']``

Errors:

- Bytes that aren't OFX (or can't be parsed) raise :class:`OfxParseError`
  with the underlying exception chained via ``raise … from …``.
- Structurally-valid OFX with zero ``STMTTRN`` rows returns ``[]`` —
  distinguishable from "not OFX" by the absence of an exception.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from ofxtools.Parser import OFXTree

from tulip_core.money import Money
from tulip_core.reconciliation import ParsedStatementLine


class OfxParseError(Exception):
    """The provided bytes could not be parsed as OFX 1.x SGML or OFX 2.x XML."""


def _description(name: str | None, memo: str | None) -> str:
    parts = [(name or "").strip(), (memo or "").strip()]
    joined = " ".join(p for p in parts if p)
    # Defensive: the value-object validator rejects empty descriptions,
    # so banks that emit a STMTTRN with neither NAME nor MEMO would crash
    # the whole parse. Fall back to TRNTYPE-based placeholder; the user
    # can edit afterwards. This mirrors how the matcher handles low-info
    # statement lines downstream.
    return joined or "<no description>"


def parse(file_bytes: bytes) -> list[ParsedStatementLine]:
    """Parse OFX bytes into a list of :class:`ParsedStatementLine`.

    Args:
        file_bytes: Raw file content, OFX 1.x SGML or OFX 2.x XML.

    Returns:
        One :class:`ParsedStatementLine` per ``STMTTRN``, in source-file
        order. Empty list when the OFX is structurally valid but has no
        transactions.

    Raises:
        OfxParseError: ``file_bytes`` is not parseable as OFX.

    """
    if not file_bytes:
        raise OfxParseError("ofx file is empty")

    tree = OFXTree()
    try:
        tree.parse(BytesIO(file_bytes))
        ofx = tree.convert()
    except Exception as exc:
        raise OfxParseError(f"could not parse as OFX: {exc}") from exc

    if not getattr(ofx, "statements", None):
        # ofxtools accepted the bytes but they don't contain a statement.
        # Treat as "not OFX" — the user uploaded a different file shape.
        raise OfxParseError("ofx payload did not contain any statements")

    out: list[ParsedStatementLine] = []
    line_number = 0
    for stmt in ofx.statements:
        # ofxtools always populates curdef on a STMTRS; if absent or empty we
        # treat the file as malformed (downstream Money rejects empty currency).
        currency = (str(stmt.curdef) if stmt.curdef else "").strip().upper()
        for txn in stmt.transactions:
            line_number += 1
            # ofxtools returns datetime; we only carry the date portion in
            # ParsedStatementLine.posted_date per ADR §Q8.
            posted_dt = txn.dtposted
            posted_date = posted_dt.date() if hasattr(posted_dt, "date") else posted_dt

            # Decimal preserves precision; ofxtools returns Decimal already.
            amount_value = txn.trnamt
            if not isinstance(amount_value, Decimal):
                amount_value = Decimal(str(amount_value))

            fitid = (getattr(txn, "fitid", None) or "").strip() or None
            name = getattr(txn, "name", None)
            memo = getattr(txn, "memo", None)
            trntype = getattr(txn, "trntype", None)

            raw: dict[str, str] = {}
            if fitid is not None:
                raw["FITID"] = fitid
            if trntype:
                raw["TRNTYPE"] = str(trntype)
            if currency:
                raw["CURDEF"] = currency

            out.append(
                ParsedStatementLine(
                    line_number=line_number,
                    posted_date=posted_date,
                    amount=Money(amount_value, currency),
                    description=_description(name, memo),
                    counterparty=(name or None) if name else None,
                    reference=fitid,
                    fitid=fitid,
                    raw=raw,
                )
            )
    return out

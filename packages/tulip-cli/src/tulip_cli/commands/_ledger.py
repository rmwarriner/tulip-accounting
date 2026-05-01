"""Ledger-subset parser for ``tulip add --edit``.

Supported grammar (intentionally a strict subset of hledger / beancount
syntax — we'll widen it deliberately, never accidentally):

* Lines starting with ``#`` or ``;`` are comments. Inline ``#`` /
  ``;`` strip the rest of the line.
* Blank lines are ignored anywhere.
* Exactly one transaction per buffer:

    YYYY-MM-DD <description>
      <account>  <amount> [<currency>]
      <account>  <amount> [<currency>]
      ...

The header line is the first non-comment, non-blank line. Postings are
indented (any whitespace prefix). Multi-segment accounts (``a:b:c``) and
dashes within segments are allowed.

A single transaction per buffer matches the ``tulip add`` command; bulk
import is `tulip-importers`' job (Phase 5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_type
from decimal import Decimal, InvalidOperation

_HEADER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\s+(.+))?\s*$")
_POSTING_RE = re.compile(
    r"^(?P<account>\S+)\s+(?P<amount>-?\d+(?:\.\d+)?)\s*(?P<currency>\S+)?\s*$"
)
_CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")


class LedgerParseError(ValueError):
    """A transaction buffer didn't match the ledger-subset grammar.

    The message is intended to be displayed to the user as-is in the
    reopen-on-error banner; line numbers are included where useful.
    """


@dataclass(frozen=True, slots=True)
class ParsedLedgerPosting:
    """One posting extracted from a ledger buffer (not yet resolved to a UUID)."""

    account: str
    amount: Decimal
    currency: str | None


@dataclass(frozen=True, slots=True)
class ParsedLedgerTransaction:
    """A single transaction extracted from a ledger buffer."""

    date: date_type
    description: str
    postings: tuple[ParsedLedgerPosting, ...]


def _strip_comment(line: str) -> str:
    """Return ``line`` with any inline ``#`` or ``;`` comment removed."""
    cut_at = len(line)
    for marker in (";", "#"):
        idx = line.find(marker)
        if idx != -1 and idx < cut_at:
            cut_at = idx
    return line[:cut_at]


def parse_ledger_text(text: str) -> ParsedLedgerTransaction:
    """Parse the ledger buffer into a :class:`ParsedLedgerTransaction`.

    Raises :class:`LedgerParseError` on any malformed shape, with a
    line-number-bearing message where applicable.
    """
    lines_with_idx: list[tuple[int, str]] = []
    for idx, raw in enumerate(text.splitlines(), start=1):
        # Preserve the indentation; only strip trailing whitespace before
        # comment-removal so a posting line's leading spaces survive.
        stripped = _strip_comment(raw).rstrip()
        if not stripped.strip():
            continue
        lines_with_idx.append((idx, stripped))

    if not lines_with_idx:
        raise LedgerParseError("Empty buffer — no header or postings to parse.")

    header_idx, header_line = lines_with_idx[0]
    header_match = _HEADER_RE.match(header_line.lstrip())
    if header_match is None:
        raise LedgerParseError(
            f"Line {header_idx}: header must look like 'YYYY-MM-DD <description>'."
        )

    date_str, description = header_match.group(1), header_match.group(2)
    if not description or not description.strip():
        raise LedgerParseError(f"Line {header_idx}: header is missing a description.")

    try:
        tx_date = date_type.fromisoformat(date_str)
    except ValueError as exc:
        raise LedgerParseError(
            f"Line {header_idx}: date {date_str!r} is not a valid YYYY-MM-DD."
        ) from exc

    postings: list[ParsedLedgerPosting] = []
    for line_idx, raw in lines_with_idx[1:]:
        if not raw.startswith((" ", "\t")):
            raise LedgerParseError(
                f"Line {line_idx}: posting lines must be indented (start with whitespace)."
            )
        body = raw.strip()
        match = _POSTING_RE.match(body)
        if match is None:
            raise LedgerParseError(
                f"Line {line_idx}: could not parse posting "
                f"(expected '<account>  <amount> [<currency>]')."
            )
        account = match.group("account")
        amount_str = match.group("amount")
        currency = match.group("currency")
        try:
            amount = Decimal(amount_str)
        except InvalidOperation as exc:
            raise LedgerParseError(
                f"Line {line_idx}: amount {amount_str!r} is not a decimal number."
            ) from exc
        if currency is not None and not _CURRENCY_RE.fullmatch(currency):
            raise LedgerParseError(
                f"Line {line_idx}: currency {currency!r} must be three ASCII letters."
            )
        postings.append(
            ParsedLedgerPosting(
                account=account,
                amount=amount,
                currency=currency.upper() if currency else None,
            )
        )

    if not postings:
        raise LedgerParseError(f"Line {header_idx}: header has no postings beneath it.")

    return ParsedLedgerTransaction(
        date=tx_date,
        description=description.strip(),
        postings=tuple(postings),
    )

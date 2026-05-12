"""Parse hledger journal text → structured transactions (P7.5).

This is the inverse of :mod:`tulip_reports.journal.export`. The
parser is intentionally restrictive: it accepts the subset of the
hledger language that ``export_journal`` emits, plus a little
forgiveness for hand-edited files (extra blank lines, mixed
indentation, trailing whitespace). The full hledger grammar
(directives, cost / price annotations, virtual postings) is out of
scope for v1.

Format we accept:

    ; comments start with semicolons; ignored
    2026-05-01 description
        Account:Path  12.50 USD
        Other:Path  -12.50 USD

    2026-05-15 (REF-123) description with reference
        ...

The parser is pure — given text it returns a structured result; it
does NOT touch the database. The :mod:`tulip_reports.journal.import_`
side maps account paths to tulip account IDs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as date_type
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class ParsedPosting:
    """One posting line from a parsed hledger entry."""

    account_path: str  # e.g. "Expense:5100:Food"
    amount: Decimal
    currency: str
    line_number: int


@dataclass(frozen=True, slots=True)
class ParsedTransaction:
    """One transaction block from a parsed hledger entry."""

    date: date_type
    description: str
    reference: str | None
    postings: list[ParsedPosting]
    line_number: int  # 1-indexed line of the header


@dataclass(frozen=True, slots=True)
class JournalParseError:
    """One parse-error annotation with a line number for the operator to fix."""

    line_number: int
    message: str


@dataclass(frozen=True, slots=True)
class ParsedJournal:
    """The full result: transactions + any parse errors we encountered."""

    transactions: list[ParsedTransaction]
    errors: list[JournalParseError] = field(default_factory=list)


_HEADER_RE = re.compile(
    r"""
    ^(?P<date>\d{4}-\d{2}-\d{2})    # ISO date
    \s+
    (?:\((?P<ref>[^)]+)\)\s+)?      # optional (reference) prefix
    (?P<description>.+?)            # the rest of the line is the description
    \s*$
    """,
    re.VERBOSE,
)


_POSTING_RE = re.compile(
    r"""
    ^\s+                              # leading whitespace (indented)
    (?P<account>\S+(?:\s\S+)*?)       # account path: tokens joined by single spaces
    \s{2,}                            # at least two spaces separator (hledger spec)
    (?P<amount>-?\d+(?:\.\d+)?)       # decimal amount, optionally negative
    \s+
    (?P<currency>[A-Z]{3,5})          # currency code (3-5 uppercase letters)
    \s*$
    """,
    re.VERBOSE,
)


def parse_journal(text: str) -> ParsedJournal:
    """Parse hledger journal ``text``; return transactions + any errors.

    Errors don't abort parsing — the result carries both whatever was
    successfully parsed and the errors so the user can fix and retry.
    A balanced-posting check is the responsibility of the import side;
    here we just extract structure.
    """
    transactions: list[ParsedTransaction] = []
    errors: list[JournalParseError] = []

    current_header: tuple[int, date_type, str, str | None] | None = None
    current_postings: list[ParsedPosting] = []

    def flush() -> None:
        if current_header is None:
            return
        line_num, dt, desc, ref = current_header
        if not current_postings:
            errors.append(
                JournalParseError(
                    line_number=line_num,
                    message="transaction has no postings",
                )
            )
            return
        transactions.append(
            ParsedTransaction(
                date=dt,
                description=desc,
                reference=ref,
                postings=list(current_postings),
                line_number=line_num,
            )
        )

    for line_num, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(";"):
            # Blank line ends the current transaction; comments are ignored.
            if not stripped and current_header is not None:
                flush()
                current_header = None
                current_postings = []
            continue

        header_match = _HEADER_RE.match(raw_line)
        if header_match and not raw_line.startswith((" ", "\t")):
            # New transaction header. Flush any previous.
            if current_header is not None:
                flush()
                current_postings = []
            try:
                dt = date_type.fromisoformat(header_match.group("date"))
            except ValueError:
                errors.append(
                    JournalParseError(
                        line_number=line_num,
                        message=f"invalid date {header_match.group('date')!r}",
                    )
                )
                current_header = None
                continue
            description = header_match.group("description").strip()
            ref = header_match.group("ref")
            current_header = (line_num, dt, description, ref)
            continue

        # Posting line (must follow a header).
        if current_header is None:
            errors.append(
                JournalParseError(
                    line_number=line_num,
                    message="posting line outside of a transaction block",
                )
            )
            continue

        posting_match = _POSTING_RE.match(raw_line)
        if not posting_match:
            errors.append(
                JournalParseError(
                    line_number=line_num,
                    message="malformed posting line",
                )
            )
            continue

        try:
            amount = Decimal(posting_match.group("amount"))
        except InvalidOperation:
            errors.append(
                JournalParseError(
                    line_number=line_num,
                    message=f"invalid amount {posting_match.group('amount')!r}",
                )
            )
            continue

        current_postings.append(
            ParsedPosting(
                account_path=posting_match.group("account").strip(),
                amount=amount,
                currency=posting_match.group("currency"),
                line_number=line_num,
            )
        )

    # EOF flush.
    if current_header is not None:
        flush()

    return ParsedJournal(transactions=transactions, errors=errors)


__all__ = [
    "JournalParseError",
    "ParsedJournal",
    "ParsedPosting",
    "ParsedTransaction",
    "parse_journal",
]

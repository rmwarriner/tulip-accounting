"""QIF parser — bytes in, list[ParsedStatementLine] out (P5.2.b).

Per ADR-0004 §Q8: "QIF — custom parser (small format, public domain)".
QIF (Quicken Interchange Format) is a line-oriented text format. Each
transaction is a block of single-letter-prefixed fields ending with a
``^`` separator. The format predates currency awareness; the API caller
supplies the account's currency since the file itself doesn't carry one.

Field-code mapping (per ADR §Q8):

- ``D`` → ``posted_date`` (multiple date formats supported; see below).
- ``T`` → ``amount.amount`` (or, when splits are present, the total
  the per-split amounts must sum to).
- ``P`` → ``counterparty`` (and folded into ``description``).
- ``M`` → ``description`` (concatenated with ``P``).
- ``N`` → ``reference`` (check number).
- ``S`` / ``$`` / ``E`` → split-category / split-amount / split-memo
  triple (see "Split records" below).
- ``^`` → record separator.

Date parsing handles three common dialects:

- ISO: ``2026-05-12``.
- US 4-digit: ``5/12/2026``.
- US 2-digit: ``5/12/26`` (rolls to 20YY — no QIF-emitting bank issues
  19xx files in 2026+).

Split records (#270, #297)
--------------------------

Banktivity and most legacy desktop apps (Quicken, Moneydance, …) encode
a multi-category transaction as one record carrying:

- One ``T<total>`` line (the net amount that hit the bank account).
- N ``S<category>`` / ``$<amount>`` / ``E<memo>`` triples — one per
  category. The ``$``-amounts must sum to ``T``.

Per #297, we emit **one ``ParsedStatementLine`` per split-bearing
record** with the consolidated parent total in ``amount`` and the
per-category breakdown in a ``splits`` tuple of :class:`ParsedSplit`.
Each ``ParsedSplit`` carries its category (the ``S`` line) + amount
(the ``$`` line) + optional memo (the ``E`` line). Promoting the line
later yields a single transaction with ``1 + len(splits)`` postings:
one bank-side at ``T`` and one per split — preserving double-entry's
"one external event = one transaction" invariant and keeping the bank
statement reconciliation 1:1.

Splits whose amounts don't sum to ``T`` are rejected with
:class:`QifParseError` — silently dropping or rebalancing would lose
money in either direction. A non-split record (no ``S``/``$`` lines)
still emits exactly one ``ParsedStatementLine`` with an empty
``splits`` tuple, preserving the existing two-posting promotion shape.

Section skipping (#198)
-----------------------

Real desktop-app exports (Banktivity, GnuCash, Moneydance, Quicken)
wrap the transactions in a preamble: ``!Option:*`` / ``!Clear:*``
directives, ``!Account`` declaration blocks, and non-transaction
``!Type:`` sections (``!Type:Cat`` category lists, ``!Type:Security``
security lists, ``!Type:Prices``, ``!Type:Class``, ``!Type:Memorized``).
The parser walks the section state machine and only parses records
inside transaction-bearing ``!Type:`` sections (``Bank``, ``CCard``,
``Cash``, ``Oth A``, ``Oth L``, ``Invst`` — plus any unknown label, on
the principle that silently dropping a bank's transactions is worse
than a parse error). An ``!Account`` block's record is skipped wholesale
— #195 grows that into real multi-account routing.

A header-less QIF (``D``/``T``/``^`` records with no ``!`` directives at
all) still parses — the historical single-account shape.

Errors raise :class:`QifParseError` with the failing line number for
debuggability — banks ship malformed QIF often and operators need to
locate the bad row.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date as date_type
from decimal import Decimal, InvalidOperation

from tulip_core.money import Money
from tulip_core.reconciliation import ParsedSplit, ParsedStatementLine

#: Each transaction record ends with this single-character line.
_RECORD_TERMINATOR = "^"

#: A QIF transfer marks its other side as ``L[Account Name]`` — a
#: bracketed account name in the category field. A plain category
#: (``LExpenses:Food``) doesn't match.
_TRANSFER_TARGET_RE = re.compile(r"^\[(?P<name>.+)\]$")

#: Header line introduces the account type. Conventional but optional.
_HEADER_RE = re.compile(r"^!Type:(.+)$", re.IGNORECASE)

#: ``!Type:`` labels that introduce *non*-transaction sections — category
#: lists, security lists, price history, memorized transactions. Their
#: records have an entirely different field shape; parsing them as
#: transactions is what made multi-section Banktivity / Quicken exports
#: fail (#198). Every record inside these sections is skipped.
#:
#: The complementary transaction-bearing labels (``Bank``, ``CCard``,
#: ``Cash``, ``Oth A``, ``Oth L``, ``Invst``) aren't enumerated: anything
#: *not* in this set is parsed, including unrecognised labels — silently
#: dropping a bank's transactions because it used an unfamiliar type
#: string is a worse failure than a parse error.
_NON_TXN_TYPES = frozenset({"cat", "class", "security", "prices", "memorized"})

#: ISO date — try this first; unambiguous.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: US date — accept 1- or 2-digit month/day; year is 2 or 4 digits.
_US_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$")


class QifParseError(Exception):
    """The provided bytes could not be parsed as QIF."""


def _split_category_and_tags(value: str) -> tuple[str, tuple[str, ...]]:
    """Split a Banktivity-style ``<category>/<tag>:<tag>...`` field (#447).

    The category is everything up to the first ``/``; tags are the
    colon-delimited list after it. Empty tags are dropped. Returns
    the bare value (no slash) as ``(value, ())``.
    """
    if "/" not in value:
        return value, ()
    category, _, tag_string = value.partition("/")
    tags = tuple(t for t in (s.strip() for s in tag_string.split(":")) if t)
    return category, tags


@dataclass(slots=True)
class _Split:
    """One ``S`` / ``$`` / ``E`` triple inside a split QIF record."""

    #: Source-file line of the ``S`` line that opened this split — used in
    #: error messages so operators can locate a malformed split row.
    opened_at: int
    category: str
    amount_str: str | None = None
    memo: str | None = None
    tags: tuple[str, ...] = ()


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
    splits: list[_Split] = field(default_factory=list)
    #: Buffer for an ``E`` line that arrived *before* its ``S`` line.
    #: Banktivity emits the split memo before the split category; the
    #: next ``S`` claims this and clears it. None when no pending memo.
    pending_split_memo: str | None = None
    #: Tags extracted from the ``L`` line's ``<category>/<tag>:<tag>``
    #: suffix (#447). Non-split records get them; split records
    #: typically carry tags per-S line instead.
    line_tags: tuple[str, ...] = ()

    def has_any_field(self) -> bool:
        return bool(
            self.amount_str
            or self.date_str
            or self.payee
            or self.memo
            or self.reference
            or self.splits
        )


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


def _compose_description(payee: str | None, memo: str | None) -> str:
    """Join the record-level payee + memo into a single description string.

    Mirrors the OFX-importer convention so the matcher's counterparty
    heuristic sees the same shape across formats.
    """
    parts = [(payee or "").strip(), (memo or "").strip()]
    return " ".join(p for p in parts if p) or "<no description>"


def _finalize_split_record(
    rec: _RecordBuilder,
    *,
    start_line_number: int,
    currency: str,
    account_type: str | None,
    posted_date: date_type,
    total: Decimal,
) -> list[ParsedStatementLine]:
    """Emit a single ParsedStatementLine carrying every split in ``splits`` (#297).

    Per #297 the parser no longer fans a split record out into N lines —
    that broke "one external event = one transaction" and produced N
    statement-line rows for a single bank-cleared payment. Instead, one
    :class:`ParsedStatementLine` is emitted with ``amount`` equal to the
    parent total (the ``T`` line) and a ``splits`` tuple holding one
    :class:`ParsedSplit` per ``S``/``$``/``E`` triple. The apply path
    promotes this to a transaction with ``1 + len(splits)`` postings:
    one bank-side at the total + one per split.

    Raises:
        QifParseError: a split is missing its ``$`` amount, or the
            per-split amounts don't sum to the parent ``T`` total.

    """
    base_raw = dict(rec.raw)
    if account_type:
        base_raw["TYPE"] = account_type
    # Strip the running ``$``/``S``/``E`` last-write-wins residue from the
    # parent ``raw`` — the per-split values live in the ``splits`` tuple
    # below, so leaving the parent's noise would confuse downstream
    # consumers reading ``raw``.
    for stale in ("$", "S", "E"):
        base_raw.pop(stale, None)

    parsed_splits: list[ParsedSplit] = []
    running = Decimal("0")
    for split in rec.splits:
        if split.amount_str is None:
            raise QifParseError(
                f"line {split.opened_at}: split for category {split.category!r} "
                "is missing its $ amount line"
            )
        split_amount = _parse_amount(split.amount_str, source_line=split.opened_at)
        running += split_amount
        parsed_splits.append(
            ParsedSplit(
                amount=Money(split_amount, currency),
                category=split.category,
                memo=split.memo,
                tags=split.tags,
            )
        )

    if running != total:
        raise QifParseError(
            f"line {rec.line_number}: split amounts sum to {running} "
            f"but record total T is {total}; refusing to import a row whose "
            "splits don't reconcile (one or more $ lines were dropped, or "
            "the file was hand-edited)"
        )

    description = _compose_description(rec.payee, rec.memo)
    # #447: the line-level tags union of every split's tags + any L-line
    # tags. We dedup but preserve first-seen order for deterministic test
    # output.
    seen: set[str] = set()
    line_tags: list[str] = []
    for source in (rec.line_tags, *(s.tags for s in parsed_splits)):
        for t in source:
            if t not in seen:
                seen.add(t)
                line_tags.append(t)
    return [
        ParsedStatementLine(
            line_number=start_line_number,
            posted_date=posted_date,
            amount=Money(total, currency),
            description=description,
            counterparty=(rec.payee or None) if rec.payee else None,
            reference=rec.reference,
            raw=base_raw,
            splits=tuple(parsed_splits),
            tags=tuple(line_tags),
        )
    ]


def _finalize_record(
    rec: _RecordBuilder,
    *,
    start_line_number: int,
    currency: str,
    account_type: str | None,
) -> list[ParsedStatementLine]:
    """Convert an accumulated record into one or more ParsedStatementLine objects.

    A non-split record yields one statement line (the historical shape).
    A split record (one or more ``S`` / ``$`` triples) yields one
    statement line per split — see ``_finalize_split_record``.
    """
    if not rec.amount_str:
        raise QifParseError(f"line {rec.line_number}: record is missing amount (T) field")
    if not rec.date_str:
        raise QifParseError(f"line {rec.line_number}: record is missing date (D) field")
    posted_date = _parse_date(rec.date_str, source_line=rec.line_number)
    total = _parse_amount(rec.amount_str, source_line=rec.line_number)

    if rec.splits:
        return _finalize_split_record(
            rec,
            start_line_number=start_line_number,
            currency=currency,
            account_type=account_type,
            posted_date=posted_date,
            total=total,
        )

    raw = dict(rec.raw)
    if account_type:
        raw["TYPE"] = account_type

    return [
        ParsedStatementLine(
            line_number=start_line_number,
            posted_date=posted_date,
            amount=Money(total, currency),
            description=_compose_description(rec.payee, rec.memo),
            counterparty=(rec.payee or None) if rec.payee else None,
            reference=rec.reference,
            raw=raw,
            tags=rec.line_tags,
        )
    ]


def parse(file_bytes: bytes, *, currency: str) -> list[ParsedStatementLine]:
    """Parse QIF bytes into :class:`ParsedStatementLine` objects.

    Args:
        file_bytes: Raw file content.
        currency: ISO 4217 code applied to every line. QIF doesn't carry
            its own currency; the API supplies the account's.

    Returns:
        One :class:`ParsedStatementLine` per record, or N lines per
        N-split record (see module docstring "Split records"). Empty
        list when the QIF has a header but no transactions.

    Raises:
        QifParseError: bytes are empty, malformed, contain a record
            missing its mandatory date / amount field, or contain a
            split record whose ``$`` amounts don't sum to its ``T``
            total. Errors carry the source-line number to help
            operators locate the bad row.

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

    # Section state (#198). ``parsing`` gates whether the current record
    # stream is transaction-bearing. It defaults True so a header-less
    # QIF (just ``D/T/^`` records) still parses — the historical shape.
    # Once an ``!Account`` block or a non-transaction ``!Type:`` section
    # is seen, ``parsing`` only goes back True on a transaction-bearing
    # ``!Type:`` header. ``in_account_block`` marks the throwaway record
    # that follows an ``!Account`` directive — #195 will grow this into
    # real per-account routing; #198 just skips it cleanly.
    parsing = True
    in_account_block = False

    for source_line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue
        saw_anything = True

        # Directive line — ``!Type:``, ``!Account``, ``!Option:``, etc.
        if line.startswith("!"):
            saw_qif_marker = True
            # A directive ends whatever record was mid-accumulation. In
            # valid QIF a ``^`` always precedes the directive; this is the
            # defensive path for hand-edited / truncated files.
            if rec is not None and rec.has_any_field() and parsing and not in_account_block:
                finalized = _finalize_record(
                    rec,
                    start_line_number=record_no + 1,
                    currency=currency,
                    account_type=account_type,
                )
                out.extend(finalized)
                record_no += len(finalized)
            rec = None

            m = _HEADER_RE.match(line)
            if m:
                label = m.group(1).strip()
                normalized = label.lower()
                in_account_block = False
                if normalized in _NON_TXN_TYPES:
                    # Category / security / price / memorized section —
                    # skip every record inside it.
                    parsing = False
                    account_type = None
                else:
                    # Transaction-bearing (or unknown) type — parse it.
                    parsing = True
                    account_type = label
                continue
            if line.strip().lower() == "!account":
                # The next record (until ``^``) declares an account, not a
                # transaction. Skip it. #195 will route by account name.
                in_account_block = True
                parsing = False
                continue
            # Any other directive (``!Option:*``, ``!Clear:*``, …) — skip
            # the line, leave section state untouched.
            continue

        # Record terminator.
        if line == _RECORD_TERMINATOR:
            saw_qif_marker = True
            if in_account_block:
                # End of the ``!Account`` declaration block — its fields
                # were skipped, so there's nothing to finalize. ``parsing``
                # stays False until the next ``!Type:`` header.
                in_account_block = False
                rec = None
                continue
            if not parsing:
                # Inside a non-transaction section — drop the record.
                rec = None
                continue
            if rec is None or not rec.has_any_field():
                # Stray ^ between records; ignore silently.
                rec = None
                continue
            finalized = _finalize_record(
                rec,
                start_line_number=record_no + 1,
                currency=currency,
                account_type=account_type,
            )
            out.extend(finalized)
            record_no += len(finalized)
            rec = None
            continue

        # Field line: first character is the field code; rest is the value.
        if in_account_block or not parsing:
            # Inside an ``!Account`` declaration block or a non-transaction
            # ``!Type:`` section — skip the field entirely.
            continue
        if rec is None:
            rec = _RecordBuilder(line_number=source_line_number)
        code = line[0]
        value = line[1:]
        # Split fields (S/$/E) are accumulated into rec.splits rather than
        # the parent ``raw`` dict — last-write-wins on a single ``raw[code]``
        # would silently drop all but the final split's category / amount /
        # memo. See #270.
        if code == "S":
            # Opens a new split. Banktivity emits ``E<memo>`` immediately
            # *before* the ``S<category>`` for that split; if a memo is
            # pending we claim it here. Standalone ``S`` lines (no memo)
            # also work — the split just lands with memo=None.
            #
            # #447: ``S`` values can also carry Banktivity-style tags
            # as ``<category>/<tag>:<tag>``. Strip them off before
            # storing the category; the tags ride along on the split.
            split_category, split_tags = _split_category_and_tags(value)
            split = _Split(
                opened_at=source_line_number,
                category=split_category,
                memo=rec.pending_split_memo,
                tags=split_tags,
            )
            rec.pending_split_memo = None
            rec.splits.append(split)
        elif code == "$":
            if rec.splits:
                # Bank-section ``$`` partners the preceding ``S`` (split
                # amount). Standard bookkeeping shape.
                rec.splits[-1].amount_str = value
            else:
                # No preceding ``S``: this isn't a split-amount line at
                # all. Investment sections use ``$`` as the transfer
                # amount on Cash/MiscInc/MiscExp records that move money
                # to/from another account via ``L[<account>]``. Banktivity
                # also emits it on some non-split bank records. Store it
                # in ``raw`` so downstream code can recover the value;
                # rejecting outright (the pre-fix behaviour) lost whole
                # accounts to a single record with an investment shape.
                rec.raw[code] = value
        elif code == "E":
            # ``E`` is the split memo. Banktivity emits it *before* its
            # ``S<category>`` partner; we buffer it on the record and the
            # next ``S`` claims it. (Quicken's older "E after S" order is
            # supported too — see below for that compat branch.) For
            # non-split records the buffered memo is harmless: nothing
            # downstream reads ``pending_split_memo`` once finalize runs.
            if rec.splits and rec.splits[-1].memo is None and rec.pending_split_memo is None:
                # "E after S" compat path: a freshly-opened split with no
                # memo and no pending buffer — attach directly.
                rec.splits[-1].memo = value
            else:
                rec.pending_split_memo = value
            # Keep the last-seen ``E`` in ``raw`` for backward compat with
            # any consumer that reads ``raw["E"]`` from a non-split record.
            rec.raw[code] = value
        elif code == "D":
            saw_qif_marker = True
            rec.date_str = value
            rec.raw[code] = value
        elif code == "T":
            saw_qif_marker = True
            rec.amount_str = value
            rec.raw[code] = value
        elif code == "P":
            rec.payee = value
            rec.raw[code] = value
        elif code == "M":
            rec.memo = value
            rec.raw[code] = value
        elif code == "N":
            rec.reference = value.strip() or None
            rec.raw[code] = value
        elif code == "L":
            # #447: ``L`` can carry a tag suffix
            # (``<category>/<tag>:<tag>...``) for non-split records.
            # Strip the suffix before storing — transfer_target reads
            # raw["L"] looking for ``[Account]`` brackets and would
            # mis-parse a trailing tag list. Tags ride on the record.
            l_category, l_tags = _split_category_and_tags(value)
            rec.raw[code] = l_category
            if l_tags:
                rec.line_tags = tuple(rec.line_tags) + l_tags
        else:
            # Other codes (C cleared status, A address, etc.) are stashed
            # in `raw` but don't drive ParsedStatementLine fields directly.
            rec.raw[code] = value

    # Trailing record without `^` (rare but legal in some emitters):
    # finalize — but only if it's a transaction record. A file that ends
    # mid-``!Account`` block or inside a non-transaction section leaves a
    # ``rec`` that must be discarded, not parsed (#198).
    if rec is not None and rec.has_any_field() and parsing and not in_account_block:
        finalized = _finalize_record(
            rec,
            start_line_number=record_no + 1,
            currency=currency,
            account_type=account_type,
        )
        out.extend(finalized)
        record_no += len(finalized)

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


def transfer_target(raw: Mapping[str, str]) -> str | None:
    """Return the destination account name if ``raw`` is a QIF transfer leg.

    QIF encodes a cross-account transfer's other side as ``L[Account
    Name]`` — a bracketed account name in the category field. A plain
    category (``LExpenses:Groceries``) or a missing ``L`` field returns
    None. The bracket form is the unambiguous transfer marker; #195b
    pairs the two legs of a transfer into one balanced transaction.
    """
    value = raw.get("L")
    if value is None:
        return None
    match = _TRANSFER_TARGET_RE.match(value.strip())
    return match.group("name").strip() if match else None


@dataclass(frozen=True, slots=True)
class QifAccountChunk:
    """One account's slice of a multi-account QIF (#195).

    ``qif_text`` is a self-contained, independently-parseable
    single-account QIF document: a ``!Type:`` header followed by that
    account's transaction records, sliced verbatim from the original
    file. The CLI POSTs each chunk to ``/v1/imports`` against the tulip
    account the ``--account-map`` resolves ``account_name`` to — so the
    server-side parser and import path stay completely unchanged.
    """

    account_name: str
    qif_text: str


@dataclass(frozen=True, slots=True)
class QifAccountDeclaration:
    """One ``!Account`` block, as seen at the top of a QIF file (#443).

    Captures the bookkeeping the auto-create-accounts CLI flow needs:
    the user-visible account name (``N`` line) and the QIF type token
    (``T`` line — ``Bank`` / ``CCard`` / ``Invst`` / etc.). Used by
    the CLI to materialise the chart of accounts before the import
    runs.
    """

    name: str
    qif_type: str


def list_account_declarations(file_bytes: bytes) -> list[QifAccountDeclaration]:
    """Return the ``!Account`` blocks declared at the top of a QIF.

    Each entry pairs the account name (``N``) with the type token
    (``T``). De-duplicated by name (first-seen wins) so a file that
    re-declares an account under multiple ``!Type:`` sections still
    yields one declaration per account.

    Returns an empty list when the file contains no ``!Account``
    blocks (single-account QIF with the bare ``!Type:`` header).
    """
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return []

    out: list[QifAccountDeclaration] = []
    seen: set[str] = set()

    in_account_block = False
    pending_name: str | None = None
    pending_type: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue
        if line.startswith("!"):
            in_account_block = line.strip().lower() == "!account"
            pending_name = None
            pending_type = None
            continue
        if not in_account_block:
            continue
        if line[0] == "N":
            pending_name = line[1:].strip() or None
        elif line[0] == "T":
            pending_type = line[1:].strip() or None
        elif line == _RECORD_TERMINATOR:
            if pending_name and pending_name not in seen:
                out.append(
                    QifAccountDeclaration(
                        name=pending_name,
                        qif_type=pending_type or "",
                    )
                )
                seen.add(pending_name)
            pending_name = None
            pending_type = None
    return out


def split_accounts(file_bytes: bytes) -> list[QifAccountChunk]:
    """Split a multi-account QIF into one parseable chunk per account.

    A multi-account QIF interleaves ``!Account`` declaration blocks with
    transaction-bearing ``!Type:`` sections — each ``!Account`` block's
    ``N`` field names the account the following section belongs to. This
    walks that structure and returns one :class:`QifAccountChunk` per
    distinct account name, concatenating every record run for an account
    that appears more than once under its first-seen ``!Type:`` header.

    Returns an empty list when the file has no ``!Account`` blocks (a
    plain single-account QIF). A file with exactly one ``!Account`` block
    returns a single chunk; the caller applies the "2+ distinct accounts
    ⇒ multi-account" rule — one named account is still imported via the
    ``--account`` path, so #198's single-account Banktivity exports keep
    working unchanged.

    Non-transaction sections (``!Type:Cat`` etc.) and the records inside
    them are skipped, exactly as :func:`parse` skips them.
    """
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        # Not decodable — let the caller's parse() raise the real error.
        return []

    runs: dict[str, list[str]] = {}  # account name -> verbatim record lines
    type_for: dict[str, str] = {}  # account name -> first-seen !Type: label
    order: list[str] = []  # first-seen account order

    pending_name: str | None = None
    in_account_block = False
    collecting_for: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue

        if line.startswith("!"):
            if line.strip().lower() == "!account":
                in_account_block = True
                pending_name = None
                collecting_for = None
                continue
            m = _HEADER_RE.match(line)
            if m:
                in_account_block = False
                label = m.group(1).strip()
                if label.lower() in _NON_TXN_TYPES or pending_name is None:
                    # Non-transaction section, or a !Type: with no
                    # preceding !Account — nothing to attribute.
                    collecting_for = None
                else:
                    collecting_for = pending_name
                    if collecting_for not in runs:
                        runs[collecting_for] = []
                        type_for[collecting_for] = label
                        order.append(collecting_for)
                continue
            # !Option:* / !Clear:* / other directive — ends collection.
            collecting_for = None
            continue

        if in_account_block:
            # Inside an !Account declaration: capture the N name, skip the
            # rest, and let the terminating ^ close the block.
            if line[0] == "N":
                pending_name = line[1:].strip()
            if line == _RECORD_TERMINATOR:
                in_account_block = False
            continue

        if collecting_for is not None:
            # Verbatim transaction-record line for the current account.
            runs[collecting_for].append(line)

    return [
        QifAccountChunk(
            account_name=name,
            qif_text=f"!Type:{type_for[name]}\n" + "\n".join(runs[name]) + "\n",
        )
        for name in order
        if runs[name]  # an account declared but carrying no records is dropped
    ]

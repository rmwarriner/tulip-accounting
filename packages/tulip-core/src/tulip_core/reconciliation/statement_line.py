"""Statement-line value objects for the reconciliation domain.

Per ADR-0004 §Q8, the importer/matcher boundary is a flat ``StatementLine``
shape decoupled from any one bank format. Two value objects:

- :class:`ParsedStatementLine` — what an importer produces. Carries no
  ``id`` or ``import_batch_id`` because those don't exist until the line
  has been persisted.
- :class:`StatementLine` — the persisted-or-about-to-be-persisted form.
  Adds ``id`` and ``import_batch_id``. The API handler converts from
  parsed → persisted via :meth:`ParsedStatementLine.with_persistence_ids`.

The split keeps each value object with a single writer and a single
reader: parsers only touch ``ParsedStatementLine``; everything past the
storage chokepoint touches only ``StatementLine``. Misuse (e.g.,
constructing a parser-only line with placeholder UUIDs) is impossible
by construction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from tulip_core.money import Money

if TYPE_CHECKING:
    from datetime import date
    from uuid import UUID


def _validate_common(
    *,
    line_number: int,
    amount: Money,
    description: str,
) -> None:
    """Raise on invariants common to both parsed and persisted lines."""
    if not isinstance(amount, Money):
        raise TypeError(
            f"amount must be Money (got {type(amount).__name__}); "
            "use Money(Decimal(...), 'USD') at the importer boundary"
        )
    if line_number < 1:
        raise ValueError(
            f"line_number must be >= 1 (got {line_number}); "
            "statement-line numbers are 1-based per ADR-0004 §Q8"
        )
    if not description or not description.strip():
        raise ValueError(
            "description must be non-empty after strip(); "
            "the matcher uses it for the counterparty heuristic"
        )


@dataclass(frozen=True, slots=True)
class ParsedSplit:
    """One leg of a multi-category statement-line (#297).

    A QIF split-record like::

        T-58.99            (parent total)
        SNeeds:Utilities   (split 1 category)
        $-45.27            (split 1 amount)
        SNeeds:Insurance   (split 2 category)
        $-13.72            (split 2 amount)

    produces one :class:`ParsedStatementLine` whose ``amount`` is the
    parent total and whose ``splits`` tuple holds one ``ParsedSplit``
    per category. Promoting the line builds a single transaction with
    one bank-side posting (the total) + one posting per split.

    ``category`` is the format-native category string (e.g. the QIF
    ``L:`` value). The apply path resolves it against the household's
    chart of accounts at promotion time — there's no per-split account
    lookup here.
    """

    amount: Money
    category: str
    memo: str | None = None
    tags: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        """Reject empty category strings."""
        if not isinstance(self.amount, Money):
            raise TypeError(f"split amount must be Money (got {type(self.amount).__name__})")
        if not self.category or not self.category.strip():
            raise ValueError("split.category must be non-empty after strip()")


@dataclass(frozen=True, slots=True)
class ParsedStatementLine:
    """A bank-statement row as produced by an importer (pre-persistence).

    Parsers (``tulip_importers.ofx.parse``, ``…qif.parse``, ``…csv.parse``)
    return ``list[ParsedStatementLine]``. The API handler creates the
    ``ImportBatch`` row first, then converts each parsed line into a
    :class:`StatementLine` via :meth:`with_persistence_ids`.

    ``splits`` (#297) is empty for ordinary two-posting lines. When
    non-empty, ``amount`` is the consolidated parent total and the
    sum of ``splits[i].amount`` must equal ``amount`` (the parser
    enforces this with a ``QifParseError`` on mismatch). The apply
    path promotes a split-bearing line to a single transaction with
    ``1 + len(splits)`` postings (one bank-side, one per split).
    """

    line_number: int
    posted_date: date
    amount: Money
    description: str
    raw: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    counterparty: str | None = field(default=None)
    reference: str | None = field(default=None)
    fitid: str | None = field(default=None)
    splits: tuple[ParsedSplit, ...] = field(default=())
    tags: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        """Validate invariants and freeze the ``raw`` dict against later mutation."""
        _validate_common(
            line_number=self.line_number,
            amount=self.amount,
            description=self.description,
        )
        # Coerce a plain dict argument into MappingProxyType so callers can't
        # mutate it post-construction. Idempotent on existing proxies.
        if not isinstance(self.raw, MappingProxyType):
            object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))
        if self.splits:
            # Every split must share the parent line's currency — otherwise
            # promotion can't produce a per-currency balanced transaction.
            for split in self.splits:
                if split.amount.currency != self.amount.currency:
                    raise ValueError(
                        f"split currency {split.amount.currency!r} does not "
                        f"match parent line currency {self.amount.currency!r}"
                    )

    def with_persistence_ids(
        self,
        *,
        id: UUID,
        import_batch_id: UUID,
    ) -> StatementLine:
        """Materialize this parsed line into a :class:`StatementLine`."""
        return StatementLine(
            id=id,
            import_batch_id=import_batch_id,
            line_number=self.line_number,
            posted_date=self.posted_date,
            amount=self.amount,
            description=self.description,
            raw=self.raw,
            counterparty=self.counterparty,
            reference=self.reference,
            fitid=self.fitid,
        )


@dataclass(frozen=True, slots=True, eq=False)
class StatementLine:
    """A persisted (or about-to-be-persisted) bank-statement row.

    Equality is by ``id`` only; mirrors :class:`tulip_core.allocation.Pool`.
    The matcher and reconciliation flows operate on ``StatementLine`` only;
    parser output uses :class:`ParsedStatementLine`.
    """

    id: UUID
    import_batch_id: UUID
    line_number: int
    posted_date: date
    amount: Money
    description: str
    raw: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    counterparty: str | None = field(default=None)
    reference: str | None = field(default=None)
    fitid: str | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate invariants and freeze the ``raw`` dict against later mutation."""
        _validate_common(
            line_number=self.line_number,
            amount=self.amount,
            description=self.description,
        )
        if not isinstance(self.raw, MappingProxyType):
            object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))

    def __eq__(self, other: object) -> bool:
        """Two StatementLines are equal iff their ids match."""
        if not isinstance(other, StatementLine):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        """Hash by id, consistent with equality."""
        return hash(self.id)

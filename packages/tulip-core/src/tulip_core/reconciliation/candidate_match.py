"""CandidateMatch value object — one proposed reconciliation pairing.

Per ADR-0004 §Q3, the matcher emits ``CandidateMatch`` rows for each
``(statement_line, ledger_transaction)`` pair that passes candidacy
(§Q1). In v1 every match is 1:1 (``match_amount == statement_line.amount``);
the field is stored explicitly so P5.4's split-match work doesn't
change the value-object shape.

Equality is by ``(statement_line_id, ledger_transaction_id)``: two
proposals against the same pair from different matcher passes
(e.g., re-running with different fuzzy thresholds) collapse to the
same identity. The ``confidence`` and ``fuzzy_score`` are diagnostic
data, not identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from tulip_core.money import Money
from tulip_core.reconciliation.match_confidence import MatchConfidence

if TYPE_CHECKING:
    from uuid import UUID


@dataclass(frozen=True, slots=True, eq=False)
class CandidateMatch:
    """One proposed reconciliation pair.

    Equality / hash by ``(statement_line_id, ledger_transaction_id)``.
    """

    statement_line_id: UUID
    ledger_transaction_id: UUID
    match_amount: Money
    confidence: MatchConfidence
    fuzzy_score: float

    def __post_init__(self) -> None:
        """Validate ``match_amount`` type + ``fuzzy_score`` range."""
        if not isinstance(self.match_amount, Money):
            raise TypeError(f"match_amount must be Money (got {type(self.match_amount).__name__})")
        if not 0.0 <= self.fuzzy_score <= 1.0:
            raise ValueError(f"fuzzy_score must be in [0.0, 1.0] (got {self.fuzzy_score})")

    def __eq__(self, other: object) -> bool:
        """Two candidates are equal iff their (line, tx) id pair matches."""
        if not isinstance(other, CandidateMatch):
            return NotImplemented
        return (
            self.statement_line_id == other.statement_line_id
            and self.ledger_transaction_id == other.ledger_transaction_id
        )

    def __hash__(self) -> int:
        """Hash by id-pair, consistent with equality."""
        return hash((self.statement_line_id, self.ledger_transaction_id))

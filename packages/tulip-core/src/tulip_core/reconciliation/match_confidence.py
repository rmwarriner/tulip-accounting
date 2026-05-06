"""MatchConfidence enum for the reconciliation matcher (P5.3).

Per ADR-0004 §Q2, candidate matches are bucketed into three confidence
levels rather than scored on a continuous scale. Bucketed-as-truth keeps
the boundary policy in one place — every consumer (UI table, CLI
review, audit log, auto-apply gate) reads the same enum.

Values match the ``reconciliation_matches.confidence`` CHECK constraint
strings (P5.1 migration §Implementation notes), so JSON / YAML round-trip
needs no converter.
"""

from __future__ import annotations

from enum import Enum
from typing import Final


class MatchConfidence(Enum):
    """Bucketed confidence in a candidate match.

    Per ADR-0004 §Q2:

    - ``HIGH``: exact amount, same date (±0 days), description fuzzy
      match ≥ 0.9. Auto-applies on commit.
    - ``MEDIUM``: exact amount within ±3 days with fuzzy ≥ 0.6 OR exact
      amount + same date + lower fuzzy. Surfaced for user confirmation.
    - ``LOW``: exact amount within ±3 days with fuzzy < 0.6. Surfaced
      as a suggestion next to the unmatched line; user opts in.

    Ordering: ``HIGH > MEDIUM > LOW`` via the ``_RANK`` mapping.
    Comparisons against non-``MatchConfidence`` raise ``TypeError`` —
    we deliberately don't mix in ``str`` because that would let
    ``MatchConfidence.HIGH < "low"`` silently return True via alphabetic
    comparison ("high" < "low"), which is meaningless here. JSON / YAML
    serialization uses ``.value`` at the API boundary.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    def __lt__(self, other: object) -> bool:
        """``MatchConfidence`` ordering is by rank, not value-string."""
        if not isinstance(other, MatchConfidence):
            return NotImplemented
        return _RANK[self] < _RANK[other]

    def __le__(self, other: object) -> bool:
        """``MatchConfidence`` ordering is by rank, not value-string."""
        if not isinstance(other, MatchConfidence):
            return NotImplemented
        return _RANK[self] <= _RANK[other]

    def __gt__(self, other: object) -> bool:
        """``MatchConfidence`` ordering is by rank, not value-string."""
        if not isinstance(other, MatchConfidence):
            return NotImplemented
        return _RANK[self] > _RANK[other]

    def __ge__(self, other: object) -> bool:
        """``MatchConfidence`` ordering is by rank, not value-string."""
        if not isinstance(other, MatchConfidence):
            return NotImplemented
        return _RANK[self] >= _RANK[other]


_RANK: Final[dict[MatchConfidence, int]] = {
    MatchConfidence.LOW: 0,
    MatchConfidence.MEDIUM: 1,
    MatchConfidence.HIGH: 2,
}

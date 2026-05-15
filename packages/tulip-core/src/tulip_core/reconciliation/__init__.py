"""Reconciliation domain types — pure, no I/O.

Per ADR-0004 §Q8, the importer/matcher boundary is a single
``StatementLine`` shape decoupled from any one bank format. Two value
objects model the parser → storage transition:

- :class:`ParsedStatementLine`: parser output (no persistence ids).
- :class:`StatementLine`: persisted form (adds ``id`` + ``import_batch_id``).

The matcher (P5.3) and the categorization seam (Phase 6) consume only
:class:`StatementLine`; importers (`tulip_importers`) produce only
:class:`ParsedStatementLine`. The split makes misuse — placeholder
UUIDs in parser output, format-specific noise in matcher input — a
type error rather than a runtime surprise.
"""

from tulip_core.reconciliation.candidate_match import CandidateMatch
from tulip_core.reconciliation.categorizer import (
    CategorizationResult,
    Categorizer,
    HouseholdContext,
    NullCategorizer,
    get_categorizer,
    register_categorizer,
)
from tulip_core.reconciliation.match_confidence import MatchConfidence
from tulip_core.reconciliation.matcher import find_candidates
from tulip_core.reconciliation.statement_line import (
    ParsedSplit,
    ParsedStatementLine,
    StatementLine,
)

__all__ = [
    "CandidateMatch",
    "CategorizationResult",
    "Categorizer",
    "HouseholdContext",
    "MatchConfidence",
    "NullCategorizer",
    "ParsedSplit",
    "ParsedStatementLine",
    "StatementLine",
    "find_candidates",
    "get_categorizer",
    "register_categorizer",
]

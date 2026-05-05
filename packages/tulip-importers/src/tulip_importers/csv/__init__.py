"""CSV importer — produces ParsedStatementLine from CSV bytes (P5.2.c).

Per ADR-0004 §Q8. CSV files lack a self-describing schema, so each
upload is paired with a :class:`CsvProfile` (column mapping). The
profile is the per-bank configuration; the parser is bank-agnostic.
"""

from tulip_importers.csv.parser import CsvParseError, parse
from tulip_importers.csv.profile import CsvProfile

__all__ = ["CsvParseError", "CsvProfile", "parse"]

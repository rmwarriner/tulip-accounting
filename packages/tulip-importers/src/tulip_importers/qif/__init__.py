"""QIF importer — produces ParsedStatementLine from QIF bytes (P5.2.b)."""

from tulip_importers.qif.parser import QifParseError, parse

__all__ = ["QifParseError", "parse"]

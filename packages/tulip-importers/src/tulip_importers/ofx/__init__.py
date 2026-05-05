"""OFX importer — produces ParsedStatementLine from OFX 1.x or 2.x bytes."""

from tulip_importers.ofx.parser import OfxParseError, parse

__all__ = ["OfxParseError", "parse"]

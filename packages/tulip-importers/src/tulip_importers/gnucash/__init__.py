"""GnuCash account-tree CSV importer (#432).

Parses the CSV produced by GnuCash's *File → Export → Export Account
Tree to CSV* into a list of :class:`ParsedAccount` rows ready to land
through ``POST /v1/accounts``. The parser is pure — no HTTP, no
ordering assumptions about the input file — so the CLI command can
sort by depth, validate, and dry-run without touching the network.

The full migration path (chart-only; transactions are a separate
issue) is documented in #432.
"""

from tulip_importers.gnucash.parser import (
    GnuCashParseError,
    ParsedAccount,
    parse,
    sort_by_depth,
    type_for_gnucash,
)

__all__: list[str] = [
    "GnuCashParseError",
    "ParsedAccount",
    "parse",
    "sort_by_depth",
    "type_for_gnucash",
]

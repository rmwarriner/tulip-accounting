"""Shared Rich-table helpers for the CLI (#289).

Numeric columns — amounts, balances, counts, confidence scores — must
right-justify so digits and decimal points line up vertically when you
scan a column. In a monospaced terminal that vertical alignment *is*
the financial-legibility win; a left-justified amount column is the
single most common reason a printed ledger reads badly.

Routing every numeric column through :func:`add_numeric_column` keeps
new tables from silently drifting back to Rich's left-justified
default — the header text varies, the justification never should.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.table import Table


def add_numeric_column(table: Table, header: str) -> None:
    """Add a right-justified column for a monetary / numeric value.

    Use for amounts, balances, counts, line numbers, percentages, and
    confidence scores. Non-numeric columns (names, codes, dates,
    statuses) keep Rich's default left-justification — call
    ``table.add_column(header)`` directly for those.
    """
    table.add_column(header, justify="right")

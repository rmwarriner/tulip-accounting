"""Tests for the shared Rich-table helpers (#289).

The financial-legibility win is vertical alignment: amounts of
different widths must line up on their right edge so a column of
numbers scans cleanly. ``add_numeric_column`` is the single chokepoint
that guarantees it.
"""

from __future__ import annotations

import io

from rich.console import Console
from rich.table import Table

from tulip_cli._tables import add_numeric_column


def test_add_numeric_column_sets_right_justify() -> None:
    table = Table()
    add_numeric_column(table, "balance")
    col = table.columns[-1]
    assert col.justify == "right"
    assert col.header == "balance"


def test_numeric_column_renders_amounts_right_aligned() -> None:
    """Different-width amounts in a numeric column share a right edge.

    Rendered at a pinned width so the assertion is stable regardless of
    the runner's terminal size (the #285 lesson).
    """
    table = Table(show_edge=False, pad_edge=False)
    table.add_column("name")
    add_numeric_column(table, "amount")
    table.add_row("groceries", "12.20")
    table.add_row("rent", "1450.00")

    console = Console(width=80, file=io.StringIO())
    console.print(table)
    out = console.file.getvalue()

    short_line = next(ln for ln in out.splitlines() if "12.20" in ln)
    long_line = next(ln for ln in out.splitlines() if "1450.00" in ln)
    # Right edge = index just past the last digit of the amount.
    short_end = short_line.index("12.20") + len("12.20")
    long_end = long_line.index("1450.00") + len("1450.00")
    assert short_end == long_end, (short_line, long_line)
    # And the short value is genuinely right-padded, not left-aligned:
    # a space sits immediately before it within the cell.
    assert short_line[short_line.index("12.20") - 1] == " "


def test_left_default_column_is_not_right_aligned() -> None:
    """Control: a plain add_column stays left-justified (Rich default)."""
    table = Table()
    table.add_column("name")
    assert table.columns[-1].justify == "left"

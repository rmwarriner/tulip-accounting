"""Pending transactions browser — P9.5.c of [#399](https://github.com/rmwarriner/tulip-accounting/issues/399).

Read-only browse of uncleared transactions split into two visual
groups: **Stale (>14d)** and **Recent**. Per ``TUI_WIREFRAMES.md
§ Pending`` and the cross-cutting decision on stale thresholds —
stale is strictly older than 14 days; the boundary day (14d) is recent.

Mutating a pending transaction (mark cleared / void / reissue /
match-to-bank) stays on the ``tulip transactions`` /
``tulip reconcile`` CLIs per ADR-0007.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from tulip_tui.data.pending import PendingData, PendingTransaction

PendingLoader = Callable[[], PendingData]


def _row_for(tx: PendingTransaction) -> tuple[str, str, str, str, str, str]:
    return (
        tx.date,
        tx.description,
        tx.account_label,
        tx.reference or "—",
        tx.amount_display or "—",
        f"{tx.age_days}d",
    )


def _detail_for(tx: PendingTransaction) -> str:
    lines = [
        f"[b]{tx.id}[/b]    {tx.description}",
        f"date:         {tx.date}    ({tx.age_days}d outstanding)",
        f"account:      {tx.account_label}",
        f"amount:       {tx.amount_display or '—'}",
    ]
    if tx.reference:
        lines.append(f"reference:    {tx.reference}")
    return "\n".join(lines)


class PendingScreen(Screen[None]):
    """Browse uncleared / pending transactions."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "back", show=True),
        Binding("r", "refresh", "refresh", show=True),
    ]

    DEFAULT_CSS = """
    PendingScreen {
        layout: vertical;
    }

    PendingScreen #pending-status {
        height: auto;
        padding: 0 1;
    }

    PendingScreen .group-header {
        height: auto;
        padding: 0 1;
        color: $accent;
    }

    PendingScreen #pending-stale-table,
    PendingScreen #pending-recent-table {
        height: 1fr;
    }

    PendingScreen #pending-detail {
        height: auto;
        min-height: 5;
        padding: 1 2;
        border-top: solid $accent;
    }
    """

    def __init__(self, loader: PendingLoader) -> None:
        """Store the loader; the screen populates on mount."""
        super().__init__()
        self._loader = loader
        self._rendered_rows: list[str] = []
        self._stale_index: list[PendingTransaction] = []
        self._recent_index: list[PendingTransaction] = []
        self._status: str = ""
        self._detail: str = ""

    def compose(self) -> ComposeResult:
        """Lay out the status strip, two grouped tables, and the detail pane."""
        yield Header()
        with Vertical():
            yield Static("loading pending…", id="pending-status")
            yield Static("[b]Stale (>14d)[/b]", classes="group-header", id="pending-stale-header")
            yield DataTable(id="pending-stale-table", zebra_stripes=True, cursor_type="row")
            yield Static("[b]Recent[/b]", classes="group-header", id="pending-recent-header")
            yield DataTable(id="pending-recent-table", zebra_stripes=True, cursor_type="row")
            yield Static("", id="pending-detail")
        yield Footer()

    def on_mount(self) -> None:
        """Install column headers and trigger the initial load."""
        for table_id in ("#pending-stale-table", "#pending-recent-table"):
            table = self.query_one(table_id, DataTable)
            table.add_columns("Date", "Description", "Account", "Ref", "Amount", "Age")
        self._load()

    def action_refresh(self) -> None:
        """Re-run the loader and rebuild both tables in place."""
        self._load()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Re-render the detail pane for the focused table's cursor.

        Both tables fire ``RowHighlighted`` on populate; without this focus
        guard the recent-table's initial event would clobber the stale-table
        detail at boot. Once the user moves the cursor, focus is on whichever
        table they're in and the right detail follows.
        """
        which = event.data_table.id
        if self.focused is not None and getattr(self.focused, "id", None) != which:
            return
        cursor = max(0, event.cursor_row)
        if which == "pending-stale-table" and cursor < len(self._stale_index):
            self._set_detail(_detail_for(self._stale_index[cursor]))
        elif which == "pending-recent-table" and cursor < len(self._recent_index):
            self._set_detail(_detail_for(self._recent_index[cursor]))

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """When focus shifts between the two tables, re-render detail.

        Tabbing from stale → recent (or vice versa) doesn't fire a fresh
        ``RowHighlighted`` because the cursor was already on row 0. Refresh
        from whatever the focused table's cursor currently points at.
        """
        widget = event.widget
        widget_id = getattr(widget, "id", None)
        if widget_id == "pending-stale-table" and self._stale_index:
            cursor = max(0, getattr(widget, "cursor_row", 0))
            cursor = min(cursor, len(self._stale_index) - 1)
            self._set_detail(_detail_for(self._stale_index[cursor]))
        elif widget_id == "pending-recent-table" and self._recent_index:
            cursor = max(0, getattr(widget, "cursor_row", 0))
            cursor = min(cursor, len(self._recent_index) - 1)
            self._set_detail(_detail_for(self._recent_index[cursor]))

    # -- internals -----------------------------------------------------

    def _load(self) -> None:
        try:
            data = self._loader()
        except Exception as exc:
            self._render_error(exc)
            return
        self._populate(data)

    def _populate(self, data: PendingData) -> None:
        stale_table = self.query_one("#pending-stale-table", DataTable)
        recent_table = self.query_one("#pending-recent-table", DataTable)
        stale_table.clear()
        recent_table.clear()
        self._rendered_rows = []
        self._stale_index = []
        self._recent_index = []

        self._set_status(f"{len(data.stale)} stale (>14d) · {len(data.recent)} recent")

        if not data.stale and not data.recent:
            self._set_detail("No pending transactions.")
            return

        for tx in data.stale:
            cells = _row_for(tx)
            stale_table.add_row(*cells)
            self._rendered_rows.append(" ".join(cells))
            self._stale_index.append(tx)
        for tx in data.recent:
            cells = _row_for(tx)
            recent_table.add_row(*cells)
            self._rendered_rows.append(" ".join(cells))
            self._recent_index.append(tx)

        # Focus the table the user lands on first (stale takes priority).
        # The matching ``RowHighlighted`` event then drives the detail pane;
        # the other table's spurious initial event is suppressed by the
        # focus guard in ``on_data_table_row_highlighted``.
        if data.stale:
            stale_table.focus()
        else:
            recent_table.focus()

    def _render_error(self, exc: BaseException) -> None:
        for table_id in ("#pending-stale-table", "#pending-recent-table"):
            self.query_one(table_id, DataTable).clear()
        self._rendered_rows = []
        self._stale_index = []
        self._recent_index = []
        self._set_status(f"[red]error:[/red] {exc}")
        self._set_detail(f"error: {exc}")

    def _set_status(self, text: str) -> None:
        self._status = text
        self.query_one("#pending-status", Static).update(text)

    def _set_detail(self, text: str) -> None:
        self._detail = text
        self.query_one("#pending-detail", Static).update(text)

    # -- introspection used by tests ----------------------------------

    def rendered_rows(self) -> list[str]:
        """Return a string-only mirror of both tables' rows for assertions."""
        return list(self._rendered_rows)

    def status_text(self) -> str:
        """Return the current status-strip text as a plain string."""
        return self._status

    def detail_text(self) -> str:
        """Return the current detail-pane text as a plain string."""
        return self._detail

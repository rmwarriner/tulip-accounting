"""Reconciliations browser — P9.4 of [#309](https://github.com/rmwarriner/tulip-accounting/issues/309).

Read-only browse of reconciliation envelopes (the four-section state
in `docs/TUI_WIREFRAMES.md`). The v1 TUI does not act on a
reconciliation — apply / match / complete stay on the
``tulip reconcile`` CLI per ADR-0007.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from tulip_tui.data.reconciliations import ReconciliationsData, ReconciliationSummary

ReconciliationsLoader = Callable[[], ReconciliationsData]
OpenReconciliationHandler = Callable[[str], None]


def _noop_open_reconciliation(_reconciliation_id: str) -> None:
    """Default drill-in handler — used when no detail screen is wired."""


def _fmt_balance(value: str) -> str:
    try:
        return f"{Decimal(value).quantize(Decimal('0.01')):,.2f}"
    except (InvalidOperation, ValueError):
        return value or "—"


def _row_for(rec: ReconciliationSummary) -> tuple[str, str, str, str]:
    period = f"{rec.statement_period_start}..{rec.statement_period_end}"
    closing = f"{_fmt_balance(rec.statement_ending_balance)} {rec.currency}"
    return (rec.status, period, closing, rec.id[:8] if rec.id else "—")


def _detail_for(rec: ReconciliationSummary) -> str:
    lines = [
        f"[b]{rec.id}[/b]    [{rec.status}]",
        f"account_id:    {rec.account_id}",
        f"period:        {rec.statement_period_start} .. {rec.statement_period_end}",
        f"starting:      {_fmt_balance(rec.statement_starting_balance)} {rec.currency}",
        f"ending:        {_fmt_balance(rec.statement_ending_balance)} {rec.currency}",
        f"created_at:    {rec.created_at}",
    ]
    if rec.completed_at:
        lines.append(f"completed_at:  {rec.completed_at}")
    if rec.source_import_batch_id:
        lines.append(f"source batch:  {rec.source_import_batch_id}")
    return "\n".join(lines)


class ReconciliationsScreen(Screen[None]):
    """Browse the household's reconciliations."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "back", show=True),
        Binding("r", "refresh", "refresh", show=True),
    ]

    DEFAULT_CSS = """
    ReconciliationsScreen {
        layout: vertical;
    }

    ReconciliationsScreen #rec-status {
        height: auto;
        padding: 0 1;
    }

    ReconciliationsScreen #rec-table {
        height: 2fr;
    }

    ReconciliationsScreen #rec-detail {
        height: 1fr;
        padding: 1 2;
        border-top: solid $accent;
    }
    """

    def __init__(
        self,
        loader: ReconciliationsLoader,
        *,
        on_open_reconciliation: OpenReconciliationHandler = _noop_open_reconciliation,
    ) -> None:
        """Store the loader and the drill-in handler used by ``enter``."""
        super().__init__()
        self._loader = loader
        self._on_open_reconciliation = on_open_reconciliation
        self._rendered_rows: list[str] = []
        self._index: list[ReconciliationSummary] = []
        self._detail: str = ""

    def compose(self) -> ComposeResult:
        """Lay out the status strip, the list table, and the detail pane."""
        yield Header()
        with Vertical():
            yield Static("loading reconciliations…", id="rec-status")
            yield DataTable(id="rec-table", zebra_stripes=True, cursor_type="row")
            yield Static("", id="rec-detail")
        yield Footer()

    def on_mount(self) -> None:
        """Install column headers and trigger the initial load."""
        table = self.query_one("#rec-table", DataTable)
        table.add_columns("Status", "Period", "Closing", "Id")
        self._load()

    def action_refresh(self) -> None:
        """Re-run the loader and rebuild the table in place."""
        self._load()

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        """Re-render the detail pane to match the newly-highlighted row."""
        self._refresh_detail()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """``enter`` drills into the per-reconciliation actioning screen (P9.6.b)."""
        index = event.cursor_row
        if index < 0 or index >= len(self._index):
            return
        rec = self._index[index]
        if not rec.id:
            return
        self._on_open_reconciliation(rec.id)

    # -- internals -----------------------------------------------------

    def _load(self) -> None:
        try:
            data = self._loader()
        except Exception as exc:
            self._render_error(exc)
            return
        self._populate(data)

    def _populate(self, data: ReconciliationsData) -> None:
        table = self.query_one("#rec-table", DataTable)
        table.clear()
        self._rendered_rows = []
        self._index = []
        status = self.query_one("#rec-status", Static)
        status.update(f"{len(data.reconciliations)} reconciliations")
        if not data.reconciliations:
            self._set_detail("No reconciliations yet.")
            return
        for rec in data.reconciliations:
            cells = _row_for(rec)
            table.add_row(*cells)
            self._rendered_rows.append(" ".join(cells))
            self._index.append(rec)
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        if not self._index:
            return
        table = self.query_one("#rec-table", DataTable)
        cursor = max(0, table.cursor_row)
        if cursor >= len(self._index):
            return
        self._set_detail(_detail_for(self._index[cursor]))

    def _render_error(self, exc: BaseException) -> None:
        table = self.query_one("#rec-table", DataTable)
        table.clear()
        self._rendered_rows = []
        self._index = []
        status = self.query_one("#rec-status", Static)
        status.update(f"[red]error:[/red] {exc}")
        self._set_detail(f"error: {exc}")

    def _set_detail(self, text: str) -> None:
        self._detail = text
        self.query_one("#rec-detail", Static).update(text)

    # -- introspection used by tests ----------------------------------

    def rendered_rows(self) -> list[str]:
        """Return a string-only mirror of the table's rows for assertions."""
        return list(self._rendered_rows)

    def detail_text(self) -> str:
        """Return the current detail-pane text as a plain string."""
        return self._detail

"""Import batches browser — P9.4 of [#309](https://github.com/rmwarriner/tulip-accounting/issues/309).

Read-only browse of recent import batches (per
``docs/TUI_WIREFRAMES.md``). The v1 TUI does not act on a batch —
apply / revert stay on the ``tulip imports`` CLI per ADR-0007.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from tulip_tui.data.imports import ImportBatchSummary, ImportsData

ImportsLoader = Callable[[], ImportsData]


def _row_for(batch: ImportBatchSummary) -> tuple[str, str, str, str, str]:
    counts = (
        f"{batch.imported_count} imported · "
        f"{batch.skipped_count} skipped · "
        f"{batch.error_count} errors"
    )
    return (
        batch.source_format,
        batch.source_filename,
        batch.status,
        counts,
        batch.id[:8] if batch.id else "—",
    )


def _detail_for(batch: ImportBatchSummary) -> str:
    lines = [
        f"[b]{batch.id}[/b]    [{batch.status}]",
        f"account_id:    {batch.account_id}",
        f"format:        {batch.source_format}",
        f"filename:      {batch.source_filename}",
        f"created_at:    {batch.created_at}",
        f"imported:      {batch.imported_count}",
        f"skipped:       {batch.skipped_count}",
        f"errors:        {batch.error_count}",
    ]
    if batch.applied_at:
        lines.append(f"applied_at:    {batch.applied_at}")
    if batch.reverted_at:
        lines.append(f"reverted_at:   {batch.reverted_at}")
    return "\n".join(lines)


class ImportsScreen(Screen[None]):
    """Browse the household's import batches."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "back", show=True),
        Binding("r", "refresh", "refresh", show=True),
    ]

    DEFAULT_CSS = """
    ImportsScreen {
        layout: vertical;
    }

    ImportsScreen #imp-status {
        height: auto;
        padding: 0 1;
    }

    ImportsScreen #imp-table {
        height: 2fr;
    }

    ImportsScreen #imp-detail {
        height: 1fr;
        padding: 1 2;
        border-top: solid $accent;
    }
    """

    def __init__(self, loader: ImportsLoader) -> None:
        """Store the loader; the screen populates on mount."""
        super().__init__()
        self._loader = loader
        self._rendered_rows: list[str] = []
        self._index: list[ImportBatchSummary] = []
        self._detail: str = ""

    def compose(self) -> ComposeResult:
        """Lay out the status strip, the list table, and the detail pane."""
        yield Header()
        with Vertical():
            yield Static("loading import batches…", id="imp-status")
            yield DataTable(id="imp-table", zebra_stripes=True, cursor_type="row")
            yield Static("", id="imp-detail")
        yield Footer()

    def on_mount(self) -> None:
        """Install column headers and trigger the initial load."""
        table = self.query_one("#imp-table", DataTable)
        table.add_columns("Format", "Filename", "Status", "Counts", "Id")
        self._load()

    def action_refresh(self) -> None:
        """Re-run the loader and rebuild the table in place."""
        self._load()

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        """Re-render the detail pane to match the newly-highlighted row."""
        self._refresh_detail()

    # -- internals -----------------------------------------------------

    def _load(self) -> None:
        try:
            data = self._loader()
        except Exception as exc:
            self._render_error(exc)
            return
        self._populate(data)

    def _populate(self, data: ImportsData) -> None:
        table = self.query_one("#imp-table", DataTable)
        table.clear()
        self._rendered_rows = []
        self._index = []
        status = self.query_one("#imp-status", Static)
        status.update(f"{len(data.batches)} import batches")
        if not data.batches:
            self._set_detail("No import batches yet.")
            return
        for batch in data.batches:
            cells = _row_for(batch)
            table.add_row(*cells)
            self._rendered_rows.append(" ".join(cells))
            self._index.append(batch)
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        if not self._index:
            return
        table = self.query_one("#imp-table", DataTable)
        cursor = max(0, table.cursor_row)
        if cursor >= len(self._index):
            return
        self._set_detail(_detail_for(self._index[cursor]))

    def _render_error(self, exc: BaseException) -> None:
        table = self.query_one("#imp-table", DataTable)
        table.clear()
        self._rendered_rows = []
        self._index = []
        status = self.query_one("#imp-status", Static)
        status.update(f"[red]error:[/red] {exc}")
        self._set_detail(f"error: {exc}")

    def _set_detail(self, text: str) -> None:
        self._detail = text
        self.query_one("#imp-detail", Static).update(text)

    # -- introspection used by tests ----------------------------------

    def rendered_rows(self) -> list[str]:
        """Return a string-only mirror of the table's rows for assertions."""
        return list(self._rendered_rows)

    def detail_text(self) -> str:
        """Return the current detail-pane text as a plain string."""
        return self._detail

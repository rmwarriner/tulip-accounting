"""Transaction register — P9.2 of [#309](https://github.com/rmwarriner/tulip-accounting/issues/309).

Scrollable list of transactions with a posting-detail pane that
follows the cursor (mirrors `docs/TUI_WIREFRAMES.md § Transactions
list`). Filters (status / date / account) flow through the loader at
construction time; surfacing filter widgets in the UI is a separate
slice.

Reachable via ``Enter`` from the accounts screen (drills into that
account's transactions) and via the back-pop binding ``escape``.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from tulip_tui.data.transactions import (
    PostingSummary,
    TransactionsData,
    TransactionSummary,
)

TransactionsLoader = Callable[[], TransactionsData]


def _row_text(tx: TransactionSummary) -> tuple[str, str, str, str, str]:
    leg_summary = " · ".join(f"{p.account_label} {_signed(p)}" for p in tx.postings[:2])
    if len(tx.postings) > 2:
        leg_summary += f" (+{len(tx.postings) - 2})"
    return (tx.date, tx.description, tx.status, leg_summary, tx.amount_display)


def _signed(posting: PostingSummary) -> str:
    quantised = posting.amount.quantize(Decimal("0.01"))
    return f"{quantised:,.2f}"


def _detail_text(tx: TransactionSummary) -> str:
    lines = [f"[b]{tx.description}[/b]    {tx.date}    [{tx.status}]"]
    if tx.reference:
        lines.append(f"reference: {tx.reference}")
    if tx.notes:
        lines.append(f"notes: {tx.notes}")
    lines.append("")
    for posting in tx.postings:
        memo = f"  ({posting.memo})" if posting.memo else ""
        lines.append(f"  {posting.account_label:<24} {_signed(posting)} {posting.currency}{memo}")
    return "\n".join(lines)


class TransactionsScreen(Screen[None]):
    """Scrollable transaction list with a posting-detail pane."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "back", show=True),
        Binding("r", "refresh", "refresh", show=True),
    ]

    DEFAULT_CSS = """
    TransactionsScreen {
        layout: vertical;
    }

    TransactionsScreen #tx-status {
        height: auto;
        padding: 0 1;
    }

    TransactionsScreen #tx-table {
        height: 2fr;
    }

    TransactionsScreen #tx-detail {
        height: 1fr;
        padding: 1 2;
        border-top: solid $accent;
    }
    """

    def __init__(self, loader: TransactionsLoader) -> None:
        """Store the loader; the screen populates on mount."""
        super().__init__()
        self._loader = loader
        self._data: TransactionsData = TransactionsData(transactions=())
        self._rendered_rows: list[str] = []
        self._empty = False
        self._error: str | None = None
        self._row_index_to_tx: list[TransactionSummary] = []
        self._detail_text: str = ""

    def compose(self) -> ComposeResult:
        """Lay out the status strip, table, and posting-detail pane."""
        yield Header()
        with Vertical():
            yield Static("loading transactions…", id="tx-status")
            yield DataTable(id="tx-table", zebra_stripes=True, cursor_type="row")
            yield Static("", id="tx-detail")
        yield Footer()

    def on_mount(self) -> None:
        """Install column headers and run the initial load."""
        table = self.query_one("#tx-table", DataTable)
        table.add_columns("Date", "Description", "Status", "Postings", "Amount")
        self._load()

    def action_refresh(self) -> None:
        """Re-run the loader and repopulate the table in place."""
        self._load()

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        """Re-render the detail pane to match the newly-highlighted row."""
        self._refresh_detail()

    # -- internals -----------------------------------------------------

    def _load(self) -> None:
        self._error = None
        try:
            self._data = self._loader()
        except Exception as exc:
            self._error = str(exc)
            self._render_error(exc)
            return
        self._populate(self._data)

    def _populate(self, data: TransactionsData) -> None:
        table = self.query_one("#tx-table", DataTable)
        table.clear()
        self._rendered_rows = []
        self._row_index_to_tx = []

        status = self.query_one("#tx-status", Static)
        status.update(f"{len(data.transactions)} transactions")

        if not data.transactions:
            self._empty = True
            self._set_detail("No transactions match.")
            return

        self._empty = False
        for tx in data.transactions:
            cells = _row_text(tx)
            table.add_row(*cells)
            self._rendered_rows.append(" ".join(cells))
            self._row_index_to_tx.append(tx)
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        if not self._row_index_to_tx:
            return
        table = self.query_one("#tx-table", DataTable)
        cursor = table.cursor_row
        # DataTable.cursor_row is -1 before any row exists; clamp to 0.
        index = max(0, cursor)
        if index >= len(self._row_index_to_tx):
            return
        tx = self._row_index_to_tx[index]
        self._set_detail(_detail_text(tx))

    def _render_error(self, exc: BaseException) -> None:
        table = self.query_one("#tx-table", DataTable)
        table.clear()
        self._rendered_rows = []
        self._row_index_to_tx = []
        self._empty = False
        status = self.query_one("#tx-status", Static)
        status.update(f"[red]error:[/red] {exc}")
        self._set_detail(f"error: {exc}")

    def _set_detail(self, text: str) -> None:
        self._detail_text = text
        detail = self.query_one("#tx-detail", Static)
        detail.update(text)

    # -- introspection used by tests ----------------------------------

    def rendered_rows(self) -> list[str]:
        """Return a string-only mirror of the table's rows for assertions."""
        return list(self._rendered_rows)

    def has_no_transactions(self) -> bool:
        """True when the most-recent load returned zero rows."""
        return self._empty

    def detail_text(self) -> str:
        """Return the current detail-pane text as a plain string."""
        return self._detail_text

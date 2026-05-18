"""Sinking funds browser — P9.5.b of [#399](https://github.com/rmwarriner/tulip-accounting/issues/399).

Read-only browse of the household's sinking funds with goal frame
(target / target date), contribution schedule, and live balance.
Mutating a fund (contribute / edit / deactivate) stays on the
``tulip sinking-funds`` CLI per ADR-0007.
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

from tulip_tui.data.sinking_funds import SinkingFundsData, SinkingFundSummary

SinkingFundsLoader = Callable[[], SinkingFundsData]


def _fmt_amount(value: str | None) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{Decimal(value).quantize(Decimal('0.01')):,.2f}"
    except (InvalidOperation, ValueError):
        return value


def _row_for(fund: SinkingFundSummary) -> tuple[str, str, str, str, str, str, str]:
    return (
        fund.name,
        fund.currency,
        _fmt_amount(fund.target_amount),
        fund.target_date,
        fund.contribution_strategy,
        _fmt_amount(fund.contribution_amount),
        _fmt_amount(fund.balance),
    )


def _detail_for(fund: SinkingFundSummary) -> str:
    lines = [
        f"[b]{fund.id}[/b]    {fund.name}",
        f"currency:              {fund.currency}",
        f"visibility:            {fund.visibility}",
        f"is_active:             {fund.is_active}",
        f"target_amount:         {_fmt_amount(fund.target_amount)}",
        f"target_date:           {fund.target_date}",
        f"contribution_strategy: {fund.contribution_strategy}",
        f"contribution_amount:   {_fmt_amount(fund.contribution_amount)}",
        f"balance:               {_fmt_amount(fund.balance)}",
    ]
    return "\n".join(lines)


class SinkingFundsScreen(Screen[None]):
    """Browse the household's sinking funds."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "back", show=True),
        Binding("r", "refresh", "refresh", show=True),
    ]

    DEFAULT_CSS = """
    SinkingFundsScreen {
        layout: vertical;
    }

    SinkingFundsScreen #sf-status {
        height: auto;
        padding: 0 1;
    }

    SinkingFundsScreen #sf-table {
        height: 2fr;
    }

    SinkingFundsScreen #sf-detail {
        height: 1fr;
        padding: 1 2;
        border-top: solid $accent;
    }
    """

    def __init__(self, loader: SinkingFundsLoader) -> None:
        """Store the loader; the screen populates on mount."""
        super().__init__()
        self._loader = loader
        self._rendered_rows: list[str] = []
        self._index: list[SinkingFundSummary] = []
        self._detail: str = ""

    def compose(self) -> ComposeResult:
        """Lay out the status strip, the sinking-fund table, and the detail pane."""
        yield Header()
        with Vertical():
            yield Static("loading sinking funds…", id="sf-status")
            yield DataTable(id="sf-table", zebra_stripes=True, cursor_type="row")
            yield Static("", id="sf-detail")
        yield Footer()

    def on_mount(self) -> None:
        """Install column headers and trigger the initial load."""
        table = self.query_one("#sf-table", DataTable)
        table.add_columns(
            "Name",
            "Currency",
            "Target",
            "Target date",
            "Strategy",
            "Contribution",
            "Balance",
        )
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

    def _populate(self, data: SinkingFundsData) -> None:
        table = self.query_one("#sf-table", DataTable)
        table.clear()
        self._rendered_rows = []
        self._index = []
        status = self.query_one("#sf-status", Static)
        status.update(f"{len(data.sinking_funds)} sinking funds")
        if not data.sinking_funds:
            self._set_detail("No sinking funds yet.")
            return
        for fund in data.sinking_funds:
            cells = _row_for(fund)
            table.add_row(*cells)
            self._rendered_rows.append(" ".join(cells))
            self._index.append(fund)
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        if not self._index:
            return
        table = self.query_one("#sf-table", DataTable)
        cursor = max(0, table.cursor_row)
        if cursor >= len(self._index):
            return
        self._set_detail(_detail_for(self._index[cursor]))

    def _render_error(self, exc: BaseException) -> None:
        table = self.query_one("#sf-table", DataTable)
        table.clear()
        self._rendered_rows = []
        self._index = []
        status = self.query_one("#sf-status", Static)
        status.update(f"[red]error:[/red] {exc}")
        self._set_detail(f"error: {exc}")

    def _set_detail(self, text: str) -> None:
        self._detail = text
        self.query_one("#sf-detail", Static).update(text)

    # -- introspection used by tests ----------------------------------

    def rendered_rows(self) -> list[str]:
        """Return a string-only mirror of the table's rows for assertions."""
        return list(self._rendered_rows)

    def detail_text(self) -> str:
        """Return the current detail-pane text as a plain string."""
        return self._detail

"""Envelopes browser — P9.5.a of [#399](https://github.com/rmwarriner/tulip-accounting/issues/399).

Read-only browse of the household's envelopes with live balances and
a one-line refill-rule summary. Mutating an envelope (fund / move /
edit) stays on the ``tulip envelopes`` CLI per ADR-0007.
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

from tulip_tui.data.envelopes import EnvelopesData, EnvelopeSummary

EnvelopesLoader = Callable[[], EnvelopesData]


def _fmt_amount(value: str | None) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{Decimal(value).quantize(Decimal('0.01')):,.2f}"
    except (InvalidOperation, ValueError):
        return value


def _row_for(env: EnvelopeSummary) -> tuple[str, str, str, str, str, str, str]:
    return (
        env.name,
        env.currency,
        env.budget_period,
        env.rollover_policy,
        _fmt_amount(env.budget_amount),
        _fmt_amount(env.balance),
        env.refill_summary,
    )


def _detail_for(env: EnvelopeSummary) -> str:
    lines = [
        f"[b]{env.id}[/b]    {env.name}",
        f"currency:         {env.currency}",
        f"visibility:       {env.visibility}",
        f"is_active:        {env.is_active}",
        f"budget_period:    {env.budget_period}",
        f"rollover_policy:  {env.rollover_policy}",
        f"budget_amount:    {_fmt_amount(env.budget_amount)}",
        f"balance:          {_fmt_amount(env.balance)}",
        f"refill:           {env.refill_summary}",
    ]
    return "\n".join(lines)


class EnvelopesScreen(Screen[None]):
    """Browse the household's envelopes."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "back", show=True),
        Binding("r", "refresh", "refresh", show=True),
    ]

    DEFAULT_CSS = """
    EnvelopesScreen {
        layout: vertical;
    }

    EnvelopesScreen #env-status {
        height: auto;
        padding: 0 1;
    }

    EnvelopesScreen #env-table {
        height: 2fr;
    }

    EnvelopesScreen #env-detail {
        height: 1fr;
        padding: 1 2;
        border-top: solid $accent;
    }
    """

    def __init__(self, loader: EnvelopesLoader) -> None:
        """Store the loader; the screen populates on mount."""
        super().__init__()
        self._loader = loader
        self._rendered_rows: list[str] = []
        self._index: list[EnvelopeSummary] = []
        self._detail: str = ""

    def compose(self) -> ComposeResult:
        """Lay out the status strip, the envelope table, and the detail pane."""
        yield Header()
        with Vertical():
            yield Static("loading envelopes…", id="env-status")
            yield DataTable(id="env-table", zebra_stripes=True, cursor_type="row")
            yield Static("", id="env-detail")
        yield Footer()

    def on_mount(self) -> None:
        """Install column headers and trigger the initial load."""
        table = self.query_one("#env-table", DataTable)
        table.add_columns("Name", "Currency", "Period", "Rollover", "Budget", "Balance", "Refill")
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

    def _populate(self, data: EnvelopesData) -> None:
        table = self.query_one("#env-table", DataTable)
        table.clear()
        self._rendered_rows = []
        self._index = []
        status = self.query_one("#env-status", Static)
        status.update(f"{len(data.envelopes)} envelopes")
        if not data.envelopes:
            self._set_detail("No envelopes yet.")
            return
        for env in data.envelopes:
            cells = _row_for(env)
            table.add_row(*cells)
            self._rendered_rows.append(" ".join(cells))
            self._index.append(env)
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        if not self._index:
            return
        table = self.query_one("#env-table", DataTable)
        cursor = max(0, table.cursor_row)
        if cursor >= len(self._index):
            return
        self._set_detail(_detail_for(self._index[cursor]))

    def _render_error(self, exc: BaseException) -> None:
        table = self.query_one("#env-table", DataTable)
        table.clear()
        self._rendered_rows = []
        self._index = []
        status = self.query_one("#env-status", Static)
        status.update(f"[red]error:[/red] {exc}")
        self._set_detail(f"error: {exc}")

    def _set_detail(self, text: str) -> None:
        self._detail = text
        self.query_one("#env-detail", Static).update(text)

    # -- introspection used by tests ----------------------------------

    def rendered_rows(self) -> list[str]:
        """Return a string-only mirror of the table's rows for assertions."""
        return list(self._rendered_rows)

    def detail_text(self) -> str:
        """Return the current detail-pane text as a plain string."""
        return self._detail

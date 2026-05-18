"""Accounts browser — the v1 TUI's default screen.

Renders the chart of accounts grouped by type with per-currency
subtotals (mirrors the wireframe in ``docs/TUI_WIREFRAMES.md §
Accounts``). The screen is built with a *loader* callable so production
wiring (a real ``TulipClient`` round-trip) and tests (an in-memory
fixture) flow through the same seam.

A loader exception is rendered inline rather than crashing the app —
the TUI is the user's whole working environment for the session, so a
network blip shouldn't pull them out of it.
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

from tulip_tui.data.accounts import AccountsData, AccountSummary, CurrencyTotal

AccountsLoader = Callable[[], AccountsData]
OpenAccountHandler = Callable[[str | None], None]


def _noop_open_account(_account_id: str | None) -> None:
    """Default drill-in handler — used when no transactions screen is wired."""


def _fmt_balance(balance: Decimal | None) -> str:
    """Render a balance with sign preserved; ``—`` when no postings exist."""
    if balance is None:
        return "—"
    quantised = balance.quantize(Decimal("0.01"))
    return f"{quantised:,.2f}"


def _fmt_subtotal(total: CurrencyTotal) -> str:
    return f"{_fmt_balance(total.amount)} {total.currency}"


def _group_header_text(group_type: str) -> str:
    return f"── {group_type.upper()} ──"


def _subtotal_row_text(totals: tuple[CurrencyTotal, ...]) -> str:
    if not totals:
        return "subtotal: —"
    return "subtotal: " + ", ".join(_fmt_subtotal(t) for t in totals)


class AccountsScreen(Screen[None]):
    """List accounts grouped by type with per-currency subtotals."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("r", "refresh", "refresh", show=True),
    ]

    DEFAULT_CSS = """
    AccountsScreen {
        layout: vertical;
    }

    AccountsScreen #status {
        height: auto;
        padding: 0 1;
    }

    AccountsScreen #empty {
        padding: 1 2;
    }

    AccountsScreen DataTable {
        height: 1fr;
    }
    """

    def __init__(
        self,
        loader: AccountsLoader,
        *,
        on_open_account: OpenAccountHandler = _noop_open_account,
    ) -> None:
        """Store the loader and the drill-in callback used by ``enter``."""
        super().__init__()
        self._loader = loader
        self._on_open_account = on_open_account
        self._rendered_rows: list[str] = []
        self._row_index_to_account_id: list[str | None] = []
        self.last_error: str | None = None
        self._empty: bool = False

    def compose(self) -> ComposeResult:
        """Lay out the header, status strip, data table, and footer."""
        yield Header()
        with Vertical():
            yield Static("loading accounts…", id="status")
            yield DataTable(id="accounts", zebra_stripes=True, cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        """Install column headers, then run the initial load."""
        table = self.query_one("#accounts", DataTable)
        table.add_columns("Code", "Account", "Type", "Currency", "Balance")
        self._load()

    def action_refresh(self) -> None:
        """Re-run the loader and rebuild the table in place."""
        self._load()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Drill into the selected row's account, if it maps to one.

        DataTable fires ``RowSelected`` on ``enter``. Group-header and
        subtotal rows have no associated account id — those are no-ops
        so the cursor stays free to traverse the whole table.
        """
        index = event.cursor_row
        if index < 0 or index >= len(self._row_index_to_account_id):
            return
        account_id = self._row_index_to_account_id[index]
        if account_id is None:
            return
        self._on_open_account(account_id)

    # -- internals -----------------------------------------------------

    def _load(self) -> None:
        """Run the loader synchronously and rebuild the table.

        The loader is sync today (production wraps a blocking
        ``httpx.Client``). The accounts read is small enough that the
        UI doesn't notice; moving the call onto a worker thread is the
        right answer once a screen actually starts blocking on
        large responses.
        """
        self.last_error = None
        try:
            data = self._loader()
        except Exception as exc:
            # Loader failures (network blip, expired token, server 5xx)
            # must surface inline rather than crashing the TUI.
            self.last_error = str(exc)
            self._render_error(exc)
            return
        self._populate(data)

    def _populate(self, data: AccountsData) -> None:
        table = self.query_one("#accounts", DataTable)
        table.clear()
        self._rendered_rows = []
        self._row_index_to_account_id = []

        status = self.query_one("#status", Static)
        status.update(f"as of {data.as_of} · {len(data.accounts)} accounts")

        if not data.accounts:
            self._empty = True
            # An empty table is jarring without context; surface the
            # condition in the status strip and skip row emission.
            table.add_row("—", "No accounts yet.", "—", "—", "—")
            self._rendered_rows.append("No accounts yet.")
            self._row_index_to_account_id.append(None)
            return

        self._empty = False
        for group in data.groups:
            header_text = _group_header_text(group.type)
            table.add_row(header_text, "", "", "", "")
            self._rendered_rows.append(header_text)
            self._row_index_to_account_id.append(None)
            for account in group.accounts:
                self._add_account_row(table, account)
            subtotal_text = _subtotal_row_text(group.totals)
            table.add_row("", subtotal_text, "", "", "")
            self._rendered_rows.append(subtotal_text)
            self._row_index_to_account_id.append(None)

    def _add_account_row(self, table: DataTable[str], account: AccountSummary) -> None:
        code = account.code or "—"
        balance = _fmt_balance(account.balance)
        table.add_row(code, account.name, account.type, account.currency, balance)
        # Maintain a string-only mirror so tests can assert content
        # without depending on DataTable internals.
        self._rendered_rows.append(
            " ".join([code, account.name, account.type, account.currency, balance])
        )
        self._row_index_to_account_id.append(account.id)

    def _render_error(self, exc: BaseException) -> None:
        table = self.query_one("#accounts", DataTable)
        table.clear()
        self._rendered_rows = []
        self._row_index_to_account_id = []
        self._empty = False
        status = self.query_one("#status", Static)
        status.update(f"[red]error:[/red] {exc}")

    # -- introspection used by tests ----------------------------------

    def rendered_rows(self) -> list[str]:
        """Return a string-only mirror of the table's rows for assertions."""
        return list(self._rendered_rows)

    def has_no_accounts(self) -> bool:
        """True when the most-recent load returned zero accounts."""
        return self._empty

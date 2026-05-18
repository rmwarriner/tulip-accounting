"""The top-level Textual ``App`` for tulip-tui.

The app boots into the accounts browser and supports drill-in to the
transactions register from there, plus an app-wide ``p`` binding to
push the reports browser. Every screen consumes a *loader* callable
so tests inject in-memory fixtures through the same seam the
production wiring uses.

``transactions_loader_factory`` is parameterised by ``account_id`` so
the drill-in passes the selected account through to the API filter; a
top-level transactions view (no account constraint) calls it with
``None``. ``reports_loader`` is the per-spec fetcher the reports
screen calls when the user moves their cursor over a menu row.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from textual.app import App
from textual.binding import Binding, BindingType

from tulip_tui.data.reports import ReportPayload, ReportSpec
from tulip_tui.screens.accounts import AccountsLoader, AccountsScreen
from tulip_tui.screens.reports import ReportsScreen
from tulip_tui.screens.transactions import TransactionsLoader, TransactionsScreen

TransactionsLoaderFactory = Callable[[str | None], TransactionsLoader]
ReportLoader = Callable[[ReportSpec], ReportPayload]


def _no_op_transactions_factory(_account_id: str | None) -> TransactionsLoader:
    """Default factory used when the caller didn't wire transactions yet.

    Returning a loader that raises is the right shape so the screen's
    inline error path kicks in (instead of crashing the app).
    """

    def _raise() -> object:
        raise RuntimeError("transactions loader not configured")

    return _raise  # type: ignore[return-value]


def _no_op_reports_loader(_spec: ReportSpec) -> ReportPayload:
    raise RuntimeError("reports loader not configured")


class TulipTuiApp(App[None]):
    """Tulip TUI shell — boots into the accounts browser."""

    TITLE = "tulip"
    SUB_TITLE = "terminal UI"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "quit", show=True),
        Binding("p", "open_reports", "reports", show=True),
    ]

    def __init__(
        self,
        *,
        loader: AccountsLoader,
        transactions_loader_factory: TransactionsLoaderFactory = _no_op_transactions_factory,
        reports_loader: ReportLoader = _no_op_reports_loader,
    ) -> None:
        """Store the per-screen loaders / factories used at mount and drill-in."""
        super().__init__()
        self._loader = loader
        self._transactions_factory = transactions_loader_factory
        self._reports_loader = reports_loader

    def on_mount(self) -> None:
        """Push the accounts browser as the initial screen."""
        self.push_screen(AccountsScreen(self._loader, on_open_account=self.open_transactions))

    def open_transactions(self, account_id: str | None) -> None:
        """Push the transactions screen filtered to ``account_id`` (or all)."""
        loader = self._transactions_factory(account_id)
        self.push_screen(TransactionsScreen(loader=loader))

    def action_open_reports(self) -> None:
        """Push the reports browser onto the screen stack."""
        self.push_screen(ReportsScreen(loader=self._reports_loader))

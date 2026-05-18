"""The top-level Textual ``App`` for tulip-tui.

The app boots into the accounts browser and exposes app-wide bindings
to push the reports / reconciliations / imports browsers. Every
screen consumes a *loader* callable so tests inject in-memory
fixtures through the same seam the production wiring uses.

``transactions_loader_factory`` is parameterised by ``account_id`` so
the drill-in passes the selected account through to the API filter; a
top-level transactions view (no account constraint) calls it with
``None``. ``reports_loader`` / ``reconciliations_loader`` /
``imports_loader`` are the per-screen fetchers their screens call on
mount and on refresh.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from textual.app import App
from textual.binding import Binding, BindingType

from tulip_tui.data.envelopes import EnvelopesData
from tulip_tui.data.imports import ImportsData
from tulip_tui.data.reconciliations import ReconciliationsData
from tulip_tui.data.reports import ReportPayload, ReportSpec
from tulip_tui.screens.accounts import AccountsLoader, AccountsScreen
from tulip_tui.screens.envelopes import EnvelopesScreen
from tulip_tui.screens.imports import ImportsScreen
from tulip_tui.screens.reconciliations import ReconciliationsScreen
from tulip_tui.screens.reports import ReportsScreen
from tulip_tui.screens.transactions import TransactionsLoader, TransactionsScreen

TransactionsLoaderFactory = Callable[[str | None], TransactionsLoader]
ReportLoader = Callable[[ReportSpec], ReportPayload]
ReconciliationsLoader = Callable[[], ReconciliationsData]
ImportsLoader = Callable[[], ImportsData]
EnvelopesLoader = Callable[[], EnvelopesData]


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


def _no_op_reconciliations_loader() -> ReconciliationsData:
    raise RuntimeError("reconciliations loader not configured")


def _no_op_imports_loader() -> ImportsData:
    raise RuntimeError("imports loader not configured")


def _no_op_envelopes_loader() -> EnvelopesData:
    raise RuntimeError("envelopes loader not configured")


class TulipTuiApp(App[None]):
    """Tulip TUI shell — boots into the accounts browser."""

    TITLE = "tulip"
    SUB_TITLE = "terminal UI"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "quit", show=True),
        Binding("p", "open_reports", "reports", show=True),
        Binding("c", "open_reconciliations", "reconcile", show=True),
        Binding("i", "open_imports", "imports", show=True),
        Binding("e", "open_envelopes", "envelopes", show=True),
    ]

    def __init__(
        self,
        *,
        loader: AccountsLoader,
        transactions_loader_factory: TransactionsLoaderFactory = _no_op_transactions_factory,
        reports_loader: ReportLoader = _no_op_reports_loader,
        reconciliations_loader: ReconciliationsLoader = _no_op_reconciliations_loader,
        imports_loader: ImportsLoader = _no_op_imports_loader,
        envelopes_loader: EnvelopesLoader = _no_op_envelopes_loader,
    ) -> None:
        """Store the per-screen loaders / factories used at mount and drill-in."""
        super().__init__()
        self._loader = loader
        self._transactions_factory = transactions_loader_factory
        self._reports_loader = reports_loader
        self._reconciliations_loader = reconciliations_loader
        self._imports_loader = imports_loader
        self._envelopes_loader = envelopes_loader

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

    def action_open_reconciliations(self) -> None:
        """Push the reconciliations browser onto the screen stack."""
        self.push_screen(ReconciliationsScreen(loader=self._reconciliations_loader))

    def action_open_imports(self) -> None:
        """Push the import batches browser onto the screen stack."""
        self.push_screen(ImportsScreen(loader=self._imports_loader))

    def action_open_envelopes(self) -> None:
        """Push the envelopes browser onto the screen stack."""
        self.push_screen(EnvelopesScreen(loader=self._envelopes_loader))

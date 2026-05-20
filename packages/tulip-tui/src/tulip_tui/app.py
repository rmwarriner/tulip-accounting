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
from tulip_tui.data.import_batch_detail import ImportBatchDetail
from tulip_tui.data.imports import ImportsData
from tulip_tui.data.pending import PendingData
from tulip_tui.data.reconciliation_detail import ReconciliationDetail
from tulip_tui.data.reconciliations import ReconciliationsData
from tulip_tui.data.reports import ReportPayload, ReportSpec
from tulip_tui.data.sinking_funds import SinkingFundsData
from tulip_tui.screens.accounts import AccountsLoader, AccountsScreen
from tulip_tui.screens.envelopes import EnvelopesScreen
from tulip_tui.screens.import_batch_detail import ImportBatchDetailScreen
from tulip_tui.screens.imports import ImportsScreen
from tulip_tui.screens.pending import PendingScreen
from tulip_tui.screens.reconciliation_detail import ReconciliationDetailScreen
from tulip_tui.screens.reconciliations import ReconciliationsScreen
from tulip_tui.screens.reports import ReportsScreen
from tulip_tui.screens.sinking_funds import SinkingFundsScreen
from tulip_tui.screens.transactions import TransactionsLoader, TransactionsScreen

TransactionsLoaderFactory = Callable[[str | None], TransactionsLoader]
ReportLoader = Callable[[ReportSpec], ReportPayload]
ReconciliationsLoader = Callable[[], ReconciliationsData]
ImportsLoader = Callable[[], ImportsData]
ImportBatchDetailLoaderFactory = Callable[[str], Callable[[], ImportBatchDetail]]
LineExcludeAction = Callable[[str, str, bool], None]
LinePromoteAction = Callable[[str, str], None]
BatchApplyAction = Callable[[str, bool, bool, bool], object]
EnvelopesLoader = Callable[[], EnvelopesData]
SinkingFundsLoader = Callable[[], SinkingFundsData]
PendingLoader = Callable[[], PendingData]

ReconciliationDetailLoaderFactory = Callable[[str], Callable[[], ReconciliationDetail]]
ReconciliationAutoMatchAction = Callable[[str], object]
ReconciliationRejectAction = Callable[[str, str], None]
ReconciliationManualMatchAction = Callable[[str, str, str, str, str], object]
ReconciliationPaperMatchAction = Callable[[str, str], object]
ReconciliationCarryForwardAction = Callable[[str, str], object]
ReconciliationCompleteAction = Callable[[str], object]


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


def _no_op_import_batch_detail_factory(_batch_id: str) -> Callable[[], ImportBatchDetail]:
    def _raise() -> ImportBatchDetail:
        raise RuntimeError("import batch detail loader not configured")

    return _raise


def _no_op_line_exclude(_batch_id: str, _line_id: str, _is_excluded: bool) -> None:
    raise RuntimeError("line exclude action not configured")


def _no_op_line_promote(_batch_id: str, _line_id: str) -> None:
    raise RuntimeError("line promote action not configured")


def _no_op_batch_apply(
    _batch_id: str,
    _as_posted: bool,
    _no_categorize: bool,
    _treat_cleared_as_pending: bool,
) -> object:
    raise RuntimeError("batch apply action not configured")


def _no_op_envelopes_loader() -> EnvelopesData:
    raise RuntimeError("envelopes loader not configured")


def _no_op_sinking_funds_loader() -> SinkingFundsData:
    raise RuntimeError("sinking funds loader not configured")


def _no_op_pending_loader() -> PendingData:
    raise RuntimeError("pending loader not configured")


def _no_op_recon_detail_factory(
    _reconciliation_id: str,
) -> Callable[[], ReconciliationDetail]:
    def _raise() -> ReconciliationDetail:
        raise RuntimeError("reconciliation detail loader not configured")

    return _raise


def _no_op_recon_auto_match(_reconciliation_id: str) -> object:
    raise RuntimeError("auto-match action not configured")


def _no_op_recon_reject(_reconciliation_id: str, _match_id: str) -> None:
    raise RuntimeError("reject action not configured")


def _no_op_recon_manual_match(
    _reconciliation_id: str,
    _line_id: str,
    _tx_id: str,
    _amount: str,
    _currency: str,
) -> object:
    raise RuntimeError("manual match action not configured")


def _no_op_recon_paper_match(_reconciliation_id: str, _tx_id: str) -> object:
    raise RuntimeError("paper match action not configured")


def _no_op_recon_carry_forward(_reconciliation_id: str, _tx_id: str) -> object:
    raise RuntimeError("carry-forward action not configured")


def _no_op_recon_complete(_reconciliation_id: str) -> object:
    raise RuntimeError("complete action not configured")


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
        Binding("s", "open_sinking_funds", "sinking funds", show=True),
        Binding("n", "open_pending", "pending", show=True),
    ]

    def __init__(
        self,
        *,
        loader: AccountsLoader,
        transactions_loader_factory: TransactionsLoaderFactory = _no_op_transactions_factory,
        reports_loader: ReportLoader = _no_op_reports_loader,
        reconciliations_loader: ReconciliationsLoader = _no_op_reconciliations_loader,
        imports_loader: ImportsLoader = _no_op_imports_loader,
        import_batch_detail_factory: ImportBatchDetailLoaderFactory = (
            _no_op_import_batch_detail_factory
        ),
        line_exclude_action: LineExcludeAction = _no_op_line_exclude,
        line_promote_action: LinePromoteAction = _no_op_line_promote,
        batch_apply_action: BatchApplyAction = _no_op_batch_apply,
        envelopes_loader: EnvelopesLoader = _no_op_envelopes_loader,
        sinking_funds_loader: SinkingFundsLoader = _no_op_sinking_funds_loader,
        pending_loader: PendingLoader = _no_op_pending_loader,
        reconciliation_detail_factory: ReconciliationDetailLoaderFactory = (
            _no_op_recon_detail_factory
        ),
        reconciliation_auto_match: ReconciliationAutoMatchAction = _no_op_recon_auto_match,
        reconciliation_reject: ReconciliationRejectAction = _no_op_recon_reject,
        reconciliation_manual_match: ReconciliationManualMatchAction = (_no_op_recon_manual_match),
        reconciliation_paper_match: ReconciliationPaperMatchAction = _no_op_recon_paper_match,
        reconciliation_carry_forward: ReconciliationCarryForwardAction = (
            _no_op_recon_carry_forward
        ),
        reconciliation_complete: ReconciliationCompleteAction = _no_op_recon_complete,
    ) -> None:
        """Store the per-screen loaders / factories used at mount and drill-in."""
        super().__init__()
        self._loader = loader
        self._transactions_factory = transactions_loader_factory
        self._reports_loader = reports_loader
        self._reconciliations_loader = reconciliations_loader
        self._imports_loader = imports_loader
        self._import_batch_detail_factory = import_batch_detail_factory
        self._line_exclude_action = line_exclude_action
        self._line_promote_action = line_promote_action
        self._batch_apply_action = batch_apply_action
        self._envelopes_loader = envelopes_loader
        self._sinking_funds_loader = sinking_funds_loader
        self._pending_loader = pending_loader
        self._reconciliation_detail_factory = reconciliation_detail_factory
        self._reconciliation_auto_match = reconciliation_auto_match
        self._reconciliation_reject = reconciliation_reject
        self._reconciliation_manual_match = reconciliation_manual_match
        self._reconciliation_paper_match = reconciliation_paper_match
        self._reconciliation_carry_forward = reconciliation_carry_forward
        self._reconciliation_complete = reconciliation_complete

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
        self.push_screen(
            ReconciliationsScreen(
                loader=self._reconciliations_loader,
                on_open_reconciliation=self.open_reconciliation_detail,
            )
        )

    def open_reconciliation_detail(self, reconciliation_id: str) -> None:
        """Push the per-reconciliation actioning screen (P9.6.b)."""
        loader = self._reconciliation_detail_factory(reconciliation_id)
        rid = reconciliation_id
        self.push_screen(
            ReconciliationDetailScreen(
                loader=loader,
                on_auto_match=lambda: self._reconciliation_auto_match(rid),
                on_reject=lambda match_id: self._reconciliation_reject(rid, match_id),
                on_manual_match=lambda line_id, tx_id, amt, cur: self._reconciliation_manual_match(
                    rid, line_id, tx_id, amt, cur
                ),
                on_paper_match=lambda tx_id: self._reconciliation_paper_match(rid, tx_id),
                on_carry_forward=lambda tx_id: self._reconciliation_carry_forward(rid, tx_id),
                on_complete=lambda: self._reconciliation_complete(rid),
            )
        )

    def action_open_imports(self) -> None:
        """Push the import batches browser onto the screen stack."""
        self.push_screen(
            ImportsScreen(
                loader=self._imports_loader,
                on_open_batch=self.open_import_batch_detail,
            )
        )

    def open_import_batch_detail(self, batch_id: str) -> None:
        """Push the per-batch detail / apply screen (P9.6.a)."""
        loader = self._import_batch_detail_factory(batch_id)
        self.push_screen(
            ImportBatchDetailScreen(
                loader=loader,
                on_toggle_exclude=lambda line_id, is_excluded: self._line_exclude_action(
                    batch_id, line_id, is_excluded
                ),
                on_promote=lambda line_id: self._line_promote_action(batch_id, line_id),
                on_apply=lambda as_posted, no_categorize, treat_cleared: self._batch_apply_action(
                    batch_id, as_posted, no_categorize, treat_cleared
                ),
            )
        )

    def action_open_envelopes(self) -> None:
        """Push the envelopes browser onto the screen stack."""
        self.push_screen(EnvelopesScreen(loader=self._envelopes_loader))

    def action_open_sinking_funds(self) -> None:
        """Push the sinking funds browser onto the screen stack."""
        self.push_screen(SinkingFundsScreen(loader=self._sinking_funds_loader))

    def action_open_pending(self) -> None:
        """Push the pending transactions browser onto the screen stack."""
        self.push_screen(PendingScreen(loader=self._pending_loader))

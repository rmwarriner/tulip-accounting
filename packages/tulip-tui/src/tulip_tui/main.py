"""Entry point for the ``tulip-tui`` console script.

Resolves the CLI's stored ``api_url`` + on-disk token store, builds
loaders for every screen, and hands them to ``TulipTuiApp``. Tests
bypass this path entirely by constructing ``TulipTuiApp`` directly
with their own loaders.
"""

from __future__ import annotations

from collections.abc import Callable

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.config import load_config
from tulip_cli.http import TulipClient
from tulip_tui.app import TulipTuiApp
from tulip_tui.data.accounts import AccountsData, load_accounts
from tulip_tui.data.envelopes import EnvelopesData, load_envelopes
from tulip_tui.data.import_batch_detail import (
    ImportBatchDetail,
    load_import_batch_detail,
    patch_line_excluded,
    promote_line,
)
from tulip_tui.data.import_batch_detail import apply_batch as _apply_batch_call
from tulip_tui.data.imports import ImportsData, load_import_batches
from tulip_tui.data.pending import PendingData, load_pending
from tulip_tui.data.reconciliation_detail import (
    ReconciliationDetail,
    auto_match,
    carry_forward,
    complete,
    load_reconciliation_detail,
    manual_match,
    paper_match,
    reject_match,
)
from tulip_tui.data.reconciliations import ReconciliationsData, load_reconciliations
from tulip_tui.data.reports import ReportPayload, ReportSpec, load_report
from tulip_tui.data.sinking_funds import SinkingFundsData, load_sinking_funds
from tulip_tui.data.transactions import TransactionsData, load_transactions
from tulip_tui.screens.transactions import TransactionsLoader


def _accounts_loader() -> AccountsData:
    """Open a fresh ``TulipClient`` per load and round-trip ``load_accounts``."""
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return load_accounts(client)


def _transactions_loader_factory(account_id: str | None) -> TransactionsLoader:
    """Build a loader that pulls transactions filtered by ``account_id``."""

    def _load() -> TransactionsData:
        config = load_config()
        with TulipClient(config, token_store=default_token_store()) as client:
            return load_transactions(client, account_id=account_id)

    return _load


def _reports_loader(spec: ReportSpec) -> ReportPayload:
    """Open a fresh ``TulipClient`` per fetch and round-trip ``load_report``."""
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return load_report(client, spec)


def _reconciliations_loader() -> ReconciliationsData:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return load_reconciliations(client)


def _imports_loader() -> ImportsData:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return load_import_batches(client)


def _import_batch_detail_factory(
    batch_id: str,
) -> Callable[[], ImportBatchDetail]:
    """Build a loader that pulls one import batch's parsed lines."""

    def _load() -> ImportBatchDetail:
        config = load_config()
        with TulipClient(config, token_store=default_token_store()) as client:
            return load_import_batch_detail(client, batch_id)

    return _load


def _line_exclude_action(batch_id: str, line_id: str, is_excluded: bool) -> None:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        patch_line_excluded(client, batch_id, line_id, is_excluded=is_excluded)


def _line_promote_action(batch_id: str, line_id: str) -> None:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        promote_line(client, batch_id, line_id)


def _batch_apply_action(
    batch_id: str,
    as_posted: bool,
    no_categorize: bool,
    treat_cleared_as_pending: bool,
) -> object:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return _apply_batch_call(
            client,
            batch_id,
            as_posted=as_posted,
            no_categorize=no_categorize,
            treat_cleared_as_pending=treat_cleared_as_pending,
        )


def _envelopes_loader() -> EnvelopesData:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return load_envelopes(client)


def _sinking_funds_loader() -> SinkingFundsData:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return load_sinking_funds(client)


def _pending_loader() -> PendingData:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return load_pending(client)


def _reconciliation_detail_factory(
    reconciliation_id: str,
) -> Callable[[], ReconciliationDetail]:
    def _load() -> ReconciliationDetail:
        config = load_config()
        with TulipClient(config, token_store=default_token_store()) as client:
            return load_reconciliation_detail(client, reconciliation_id)

    return _load


def _reconciliation_auto_match(reconciliation_id: str) -> object:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return auto_match(client, reconciliation_id)


def _reconciliation_reject(reconciliation_id: str, match_id: str) -> None:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        reject_match(client, reconciliation_id, match_id)


def _reconciliation_manual_match(
    reconciliation_id: str,
    statement_line_id: str,
    ledger_transaction_id: str,
    match_amount: str,
    currency: str,
) -> object:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return manual_match(
            client,
            reconciliation_id,
            statement_line_id=statement_line_id,
            ledger_transaction_id=ledger_transaction_id,
            match_amount=match_amount,
            currency=currency,
        )


def _reconciliation_paper_match(reconciliation_id: str, ledger_transaction_id: str) -> object:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return paper_match(client, reconciliation_id, ledger_transaction_id=ledger_transaction_id)


def _reconciliation_carry_forward(reconciliation_id: str, transaction_id: str) -> object:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return carry_forward(client, reconciliation_id, transaction_ids=[transaction_id])


def _reconciliation_complete(reconciliation_id: str) -> object:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return complete(client, reconciliation_id)


def run() -> None:
    """Launch the Tulip TUI against the configured API."""
    TulipTuiApp(
        loader=_accounts_loader,
        transactions_loader_factory=_transactions_loader_factory,
        reports_loader=_reports_loader,
        reconciliations_loader=_reconciliations_loader,
        imports_loader=_imports_loader,
        import_batch_detail_factory=_import_batch_detail_factory,
        line_exclude_action=_line_exclude_action,
        line_promote_action=_line_promote_action,
        batch_apply_action=_batch_apply_action,
        envelopes_loader=_envelopes_loader,
        sinking_funds_loader=_sinking_funds_loader,
        pending_loader=_pending_loader,
        reconciliation_detail_factory=_reconciliation_detail_factory,
        reconciliation_auto_match=_reconciliation_auto_match,
        reconciliation_reject=_reconciliation_reject,
        reconciliation_manual_match=_reconciliation_manual_match,
        reconciliation_paper_match=_reconciliation_paper_match,
        reconciliation_carry_forward=_reconciliation_carry_forward,
        reconciliation_complete=_reconciliation_complete,
    ).run()

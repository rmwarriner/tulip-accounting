"""Entry point for the ``tulip-tui`` console script.

Resolves the CLI's stored ``api_url`` + on-disk token store, builds
loaders for every screen, and hands them to ``TulipTuiApp``. Tests
bypass this path entirely by constructing ``TulipTuiApp`` directly
with their own loaders.
"""

from __future__ import annotations

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.config import load_config
from tulip_cli.http import TulipClient
from tulip_tui.app import TulipTuiApp
from tulip_tui.data.accounts import AccountsData, load_accounts
from tulip_tui.data.reports import ReportPayload, ReportSpec, load_report
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


def run() -> None:
    """Launch the Tulip TUI against the configured API."""
    TulipTuiApp(
        loader=_accounts_loader,
        transactions_loader_factory=_transactions_loader_factory,
        reports_loader=_reports_loader,
    ).run()

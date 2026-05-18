"""Entry point for the ``tulip-tui`` console script.

Resolves the CLI's stored ``api_url`` + on-disk token store, then
hands a closure that round-trips ``load_accounts`` against that
client into ``TulipTuiApp``. Tests bypass this path entirely by
constructing ``TulipTuiApp(loader=...)`` directly.
"""

from __future__ import annotations

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.config import load_config
from tulip_cli.http import TulipClient
from tulip_tui.app import TulipTuiApp
from tulip_tui.data.accounts import AccountsData, load_accounts


def _production_loader() -> AccountsData:
    """Open a fresh ``TulipClient`` per load and round-trip ``load_accounts``."""
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return load_accounts(client)


def run() -> None:
    """Launch the Tulip TUI against the configured API."""
    TulipTuiApp(loader=_production_loader).run()

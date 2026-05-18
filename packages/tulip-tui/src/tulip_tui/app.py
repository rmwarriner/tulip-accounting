"""The top-level Textual ``App`` for tulip-tui.

P9.1 swaps the placeholder shell for the accounts browser as the
default screen. The app accepts an optional ``loader`` callable so
tests can inject in-memory ``AccountsData`` without spinning up a
``TulipClient``; production wiring (``main.run``) installs the loader
that round-trips through the API.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import App
from textual.binding import Binding, BindingType

from tulip_tui.screens.accounts import AccountsLoader, AccountsScreen


class TulipTuiApp(App[None]):
    """Tulip TUI shell — boots into the accounts browser."""

    TITLE = "tulip"
    SUB_TITLE = "terminal UI"
    BINDINGS: ClassVar[list[BindingType]] = [Binding("q", "quit", "quit", show=True)]

    def __init__(self, *, loader: AccountsLoader) -> None:
        """Store the accounts loader the default screen will use on mount."""
        super().__init__()
        self._loader = loader

    def on_mount(self) -> None:
        """Push the accounts browser as the initial screen."""
        self.push_screen(AccountsScreen(self._loader))

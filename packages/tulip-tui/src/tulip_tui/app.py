"""The top-level Textual ``App`` for tulip-tui.

P9.0 ships only the shell - a mounted app that reaches a runnable state
and quits cleanly on ``q``. Real screens (account browser, transaction
register, reports, reconciliation/import status) arrive in P9.1-P9.4 per
[#309](https://github.com/rmwarriner/tulip-accounting/issues/309).
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.widgets import Footer, Header, Static


class TulipTuiApp(App[None]):
    """Tulip TUI shell - placeholder until P9.1 lands the first real screen."""

    TITLE = "tulip"
    SUB_TITLE = "terminal UI"
    BINDINGS: ClassVar[list[BindingType]] = [Binding("q", "quit", "quit", show=True)]

    def compose(self) -> ComposeResult:
        """Yield the static placeholder layout for the P9.0 shell."""
        yield Header()
        yield Static(
            "tulip-tui - Phase 9 shell.\nScreens land in subsequent slices (see #309).",
            id="welcome",
        )
        yield Footer()

"""Entry point for the ``tulip-tui`` console script."""

from __future__ import annotations

from tulip_tui.app import TulipTuiApp


def run() -> None:
    """Launch the Tulip TUI under the user's terminal."""
    TulipTuiApp().run()

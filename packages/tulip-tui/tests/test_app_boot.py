"""Pilot-mode boot-and-quit smoke test for the Tulip TUI app shell.

The first slice (P9.0 of [#309](https://github.com/rmwarriner/tulip-accounting/issues/309))
ships a non-functional app shell — no screens yet, no API calls — but
asserting boot-and-quit under Textual's headless ``run_test`` harness
proves the package is wired correctly and gives subsequent slices a
test scaffold to build on.
"""

from __future__ import annotations

import pytest

from tulip_tui.app import TulipTuiApp


@pytest.mark.asyncio
async def test_app_boots_and_quits_cleanly() -> None:
    app = TulipTuiApp()
    async with app.run_test() as pilot:
        assert app.is_running
        await pilot.press("q")
    assert app.return_code == 0

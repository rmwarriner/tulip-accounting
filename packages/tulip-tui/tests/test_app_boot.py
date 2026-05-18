"""Pilot-mode boot-and-quit smoke test for the Tulip TUI app shell.

The check shifted from a placeholder-only shell to one that mounts the
``AccountsScreen`` on startup (P9.1 of [#309](https://github.com/rmwarriner/tulip-accounting/issues/309)).
We inject an empty ``AccountsData`` loader so the boot path is fully
in-memory — no API client, no thread pool.
"""

from __future__ import annotations

import pytest

from tulip_tui.app import TulipTuiApp
from tulip_tui.data.accounts import AccountsData


@pytest.mark.asyncio
async def test_app_boots_and_quits_cleanly() -> None:
    empty = AccountsData(as_of="2026-05-17", accounts=(), groups=())
    app = TulipTuiApp(loader=lambda: empty)
    async with app.run_test() as pilot:
        assert app.is_running
        await pilot.press("q")
    assert app.return_code == 0

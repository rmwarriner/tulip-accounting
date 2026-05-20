"""Pilot-mode tests for ``PendingScreen`` + the app-wide ``n`` binding.

Mirrors the test shape of ``test_envelopes_screen.py`` /
``test_sinking_funds_screen.py``: the screen takes an injected loader,
no API call is involved. Two table groups (Stale / Recent) make the
rendered-row assertions look slightly different — both groups are
flattened with a marker into one ``rendered_rows()`` list.
"""

from __future__ import annotations

import pytest

from tulip_tui.app import TulipTuiApp
from tulip_tui.data.accounts import AccountsData
from tulip_tui.data.pending import PendingData, PendingTransaction
from tulip_tui.screens.pending import PendingScreen


def _accounts_loader() -> AccountsData:
    return AccountsData(as_of="2026-05-15", accounts=(), groups=())


def _sample_pending() -> PendingData:
    return PendingData(
        stale=(
            PendingTransaction(
                id="tx-old-check",
                date="2026-04-22",
                age_days=23,
                description="Check #1042 — Smith",
                reference="1042",
                account_label="Checking",
                amount_display="-240.00 USD",
            ),
        ),
        recent=(
            PendingTransaction(
                id="tx-boundary",
                date="2026-05-01",
                age_days=14,
                description="ACH out — IRA",
                reference=None,
                account_label="Checking",
                amount_display="-500.00 USD",
            ),
            PendingTransaction(
                id="tx-card-hold",
                date="2026-05-14",
                age_days=1,
                description="Card hold — Shell",
                reference=None,
                account_label="Visa",
                amount_display="-42.00 USD",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_pending_screen_lists_both_groups() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = PendingScreen(loader=lambda: _sample_pending())
        await app.push_screen(screen)
        await pilot.pause()
        rendered = screen.rendered_rows()

    assert any(
        "Check #1042" in row and "Checking" in row and "23" in row and "-240.00" in row
        for row in rendered
    )
    assert any(
        "Card hold" in row and "Visa" in row and "1" in row and "-42.00" in row for row in rendered
    )
    assert any("ACH out" in row and "14" in row and "-500.00" in row for row in rendered)


@pytest.mark.asyncio
async def test_pending_screen_group_headers_present() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = PendingScreen(loader=lambda: _sample_pending())
        await app.push_screen(screen)
        await pilot.pause()
        status = screen.status_text()

    # Status strip names the count totals from both groups.
    assert "1" in status  # stale count
    assert "2" in status  # recent count


@pytest.mark.asyncio
async def test_pending_screen_detail_follows_cursor() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = PendingScreen(loader=lambda: _sample_pending())
        await app.push_screen(screen)
        await pilot.pause()
        first = screen.detail_text()
        # Initial focus is on the stale table → first row is Check #1042.
        assert "tx-old-check" in first
        assert "1042" in first
        assert "Checking" in first
        # ``tab`` moves focus to the recent table (Textual default focus
        # traversal); its highlighted row becomes the detail.
        await pilot.press("tab")
        await pilot.pause()
        second = screen.detail_text()
        # Recent table's first row in the fixture is the boundary ACH txn.
        assert "tx-boundary" in second
        assert "ACH out" in second
        # Move down inside the recent table → second recent row (card hold).
        await pilot.press("down")
        await pilot.pause()
        third = screen.detail_text()
        assert "tx-card-hold" in third
        assert "Card hold" in third


@pytest.mark.asyncio
async def test_pending_screen_empty_state() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = PendingScreen(loader=lambda: PendingData(stale=(), recent=()))
        await app.push_screen(screen)
        await pilot.pause()
        assert "no pending" in screen.detail_text().lower()


@pytest.mark.asyncio
async def test_pending_screen_renders_error_inline() -> None:
    def boom() -> PendingData:
        raise RuntimeError("api unreachable")

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = PendingScreen(loader=boom)
        await app.push_screen(screen)
        await pilot.pause()
        assert "api unreachable" in screen.detail_text()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("q")
    assert app.return_code == 0


@pytest.mark.asyncio
async def test_app_binding_n_pushes_pending_screen() -> None:
    # AccountsScreen claims `n` for "new account" (#431), so the
    # app-level `n` only fires from screens that don't shadow it.
    # Open ReportsScreen first (via `p`), then the app-level `n`
    # falls through to open_pending.
    app = TulipTuiApp(
        loader=_accounts_loader,
        pending_loader=lambda: _sample_pending(),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")  # leave AccountsScreen via the reports binding
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, PendingScreen)

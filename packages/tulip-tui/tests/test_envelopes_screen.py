"""Pilot-mode tests for ``EnvelopesScreen`` + the app-wide ``e`` binding.

Mirrors the test shape of ``test_recon_imports_screens.py``: the screen
takes an injected loader, so no API call is involved.
"""

from __future__ import annotations

import pytest

from tulip_tui.app import TulipTuiApp
from tulip_tui.data.accounts import AccountsData
from tulip_tui.data.envelopes import EnvelopesData, EnvelopeSummary
from tulip_tui.screens.envelopes import EnvelopesScreen


def _accounts_loader() -> AccountsData:
    return AccountsData(as_of="2026-05-17", accounts=(), groups=())


def _sample_envelopes() -> EnvelopesData:
    return EnvelopesData(
        envelopes=(
            EnvelopeSummary(
                id="env-groceries",
                name="Groceries",
                currency="USD",
                visibility="shared",
                is_active=True,
                budget_period="monthly",
                rollover_policy="reset",
                budget_amount="600.00",
                balance="187.45",
                refill_summary="fixed: 600.00 USD",
            ),
            EnvelopeSummary(
                id="env-dining",
                name="Dining out",
                currency="USD",
                visibility="shared",
                is_active=True,
                budget_period="monthly",
                rollover_policy="cap_at_budget",
                budget_amount=None,
                balance="34.90",
                refill_summary="pct-inflow: 5%",
            ),
            EnvelopeSummary(
                id="env-new",
                name="Brand new",
                currency="USD",
                visibility="shared",
                is_active=True,
                budget_period="monthly",
                rollover_policy="reset",
                budget_amount="100.00",
                balance=None,
                refill_summary="—",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_envelopes_screen_lists_rows_with_balance_and_refill() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = EnvelopesScreen(loader=lambda: _sample_envelopes())
        await app.push_screen(screen)
        await pilot.pause()
        rendered = screen.rendered_rows()

    assert len(rendered) == 3
    # Comma-grouped balance formatting + refill summary land in the row text.
    assert any("Groceries" in row and "187.45" in row and "fixed" in row for row in rendered)
    assert any("Dining out" in row and "34.90" in row and "pct-inflow" in row for row in rendered)
    # Missing balance renders as "—" (and missing budget too).
    assert any("Brand new" in row and "—" in row for row in rendered)


@pytest.mark.asyncio
async def test_envelopes_screen_detail_follows_cursor() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = EnvelopesScreen(loader=lambda: _sample_envelopes())
        await app.push_screen(screen)
        await pilot.pause()
        first = screen.detail_text()
        assert "env-groceries" in first
        assert "monthly" in first
        assert "600.00" in first
        await pilot.press("down")
        await pilot.pause()
        second = screen.detail_text()
        assert "env-dining" in second
        assert "pct-inflow" in second


@pytest.mark.asyncio
async def test_envelopes_screen_empty_state() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = EnvelopesScreen(loader=lambda: EnvelopesData(envelopes=()))
        await app.push_screen(screen)
        await pilot.pause()
        assert "no envelopes" in screen.detail_text().lower()


@pytest.mark.asyncio
async def test_envelopes_screen_renders_error_inline() -> None:
    def boom() -> EnvelopesData:
        raise RuntimeError("api unreachable")

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = EnvelopesScreen(loader=boom)
        await app.push_screen(screen)
        await pilot.pause()
        assert "api unreachable" in screen.detail_text()
        # Inline error should not lock the screen — escape + quit still work.
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("q")
    assert app.return_code == 0


@pytest.mark.asyncio
async def test_app_binding_e_pushes_envelopes_screen() -> None:
    app = TulipTuiApp(
        loader=_accounts_loader,
        envelopes_loader=lambda: _sample_envelopes(),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, EnvelopesScreen)

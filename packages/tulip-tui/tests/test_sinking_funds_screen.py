"""Pilot-mode tests for ``SinkingFundsScreen`` + the app-wide ``s`` binding.

Mirrors the test shape of ``test_envelopes_screen.py``: the screen takes
an injected loader, so no API call is involved.
"""

from __future__ import annotations

import pytest

from tulip_tui.app import TulipTuiApp
from tulip_tui.data.accounts import AccountsData
from tulip_tui.data.sinking_funds import SinkingFundsData, SinkingFundSummary
from tulip_tui.screens.sinking_funds import SinkingFundsScreen


def _accounts_loader() -> AccountsData:
    return AccountsData(as_of="2026-05-18", accounts=(), groups=())


def _sample_sinking_funds() -> SinkingFundsData:
    return SinkingFundsData(
        sinking_funds=(
            SinkingFundSummary(
                id="sf-car",
                name="Car repair",
                currency="USD",
                visibility="shared",
                is_active=True,
                target_amount="3000.00",
                target_date="2027-01-01",
                contribution_strategy="manual",
                contribution_amount=None,
                balance="1200.00",
            ),
            SinkingFundSummary(
                id="sf-vacation",
                name="Vacation",
                currency="USD",
                visibility="shared",
                is_active=True,
                target_amount="5000.00",
                target_date="2026-12-15",
                contribution_strategy="even_split",
                contribution_amount="250.00",
                balance="650.00",
            ),
            SinkingFundSummary(
                id="sf-new",
                name="Brand new fund",
                currency="USD",
                visibility="shared",
                is_active=True,
                target_amount="1000.00",
                target_date="2027-06-01",
                contribution_strategy="manual",
                contribution_amount=None,
                balance=None,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_sinking_funds_screen_lists_rows() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SinkingFundsScreen(loader=lambda: _sample_sinking_funds())
        await app.push_screen(screen)
        await pilot.pause()
        rendered = screen.rendered_rows()

    assert len(rendered) == 3
    # Comma-grouped amount formatting and strategy land in the row text.
    assert any(
        "Car repair" in row and "3,000.00" in row and "1,200.00" in row and "manual" in row
        for row in rendered
    )
    assert any(
        "Vacation" in row and "5,000.00" in row and "650.00" in row and "even_split" in row
        for row in rendered
    )
    # Missing balance renders as "—".
    assert any("Brand new fund" in row and "—" in row for row in rendered)


@pytest.mark.asyncio
async def test_sinking_funds_screen_detail_follows_cursor() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SinkingFundsScreen(loader=lambda: _sample_sinking_funds())
        await app.push_screen(screen)
        await pilot.pause()
        first = screen.detail_text()
        assert "sf-car" in first
        assert "2027-01-01" in first
        assert "1,200.00" in first
        await pilot.press("down")
        await pilot.pause()
        second = screen.detail_text()
        assert "sf-vacation" in second
        assert "even_split" in second
        assert "250.00" in second


@pytest.mark.asyncio
async def test_sinking_funds_screen_empty_state() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SinkingFundsScreen(loader=lambda: SinkingFundsData(sinking_funds=()))
        await app.push_screen(screen)
        await pilot.pause()
        assert "no sinking funds" in screen.detail_text().lower()


@pytest.mark.asyncio
async def test_sinking_funds_screen_renders_error_inline() -> None:
    def boom() -> SinkingFundsData:
        raise RuntimeError("api unreachable")

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SinkingFundsScreen(loader=boom)
        await app.push_screen(screen)
        await pilot.pause()
        assert "api unreachable" in screen.detail_text()
        # Inline error should not lock the screen — escape + quit still work.
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("q")
    assert app.return_code == 0


@pytest.mark.asyncio
async def test_app_binding_s_pushes_sinking_funds_screen() -> None:
    app = TulipTuiApp(
        loader=_accounts_loader,
        sinking_funds_loader=lambda: _sample_sinking_funds(),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, SinkingFundsScreen)

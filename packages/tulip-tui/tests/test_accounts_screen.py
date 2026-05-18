"""Pilot-mode tests for ``AccountsScreen``.

The screen is the v1 TUI's default surface (per #309 / P9.1). These
tests boot the full ``TulipTuiApp`` with a synchronous fake loader so
no API call or thread pool is involved — we only need to assert the
screen renders the rows it was given. Async-load + error paths are
covered separately.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tulip_tui.app import TulipTuiApp
from tulip_tui.data.accounts import (
    AccountGroup,
    AccountsData,
    AccountSummary,
    CurrencyTotal,
)
from tulip_tui.screens.accounts import AccountsScreen


def _sample_data() -> AccountsData:
    checking = AccountSummary(
        id="acc-1",
        code="assets:checking",
        name="Checking",
        type="asset",
        currency="USD",
        balance=Decimal("3241.18"),
    )
    savings = AccountSummary(
        id="acc-2",
        code="assets:savings",
        name="Savings",
        type="asset",
        currency="USD",
        balance=Decimal("12500.00"),
    )
    visa = AccountSummary(
        id="acc-3",
        code="liabilities:visa",
        name="Visa",
        type="liability",
        currency="USD",
        balance=Decimal("-842.55"),
    )
    return AccountsData(
        as_of="2026-05-17",
        accounts=(checking, savings, visa),
        groups=(
            AccountGroup(
                type="asset",
                accounts=(checking, savings),
                totals=(CurrencyTotal(currency="USD", amount=Decimal("15741.18")),),
            ),
            AccountGroup(
                type="liability",
                accounts=(visa,),
                totals=(CurrencyTotal(currency="USD", amount=Decimal("-842.55")),),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_accounts_screen_renders_groups_subtotals_and_accounts() -> None:
    """Each group emits a header + member rows + subtotal row."""
    data = _sample_data()
    app = TulipTuiApp(loader=lambda: data)
    async with app.run_test() as pilot:
        # The loader is synchronous + injected, so the screen has its
        # data by the time the first paint settles.
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, AccountsScreen)
        rendered = screen.rendered_rows()

    # Two groups; each emits (1 header + N member rows + 1 subtotal).
    # Assets has 2 members, liabilities has 1, so 4 + 3 = 7 rows total.
    assert len(rendered) == 7

    asset_header = rendered[0]
    assert "asset" in asset_header.lower()

    # Account rows include code + name + balance text. Balances are
    # comma-grouped for readability ("3,241.18" not "3241.18").
    assert any("Checking" in row and "3,241.18" in row for row in rendered)
    assert any("Savings" in row and "12,500.00" in row for row in rendered)
    # Negative liability balance is rendered with the sign preserved.
    assert any("Visa" in row and "-842.55" in row for row in rendered)

    # Each group's subtotal row carries the per-currency rollup.
    assert any("15,741.18" in row for row in rendered)
    assert any("-842.55" in row for row in rendered if "Visa" not in row)


@pytest.mark.asyncio
async def test_accounts_screen_shows_empty_state_when_no_accounts() -> None:
    """Zero accounts → an explanatory ``Static`` instead of the table."""
    empty = AccountsData(as_of="2026-05-17", accounts=(), groups=())
    app = TulipTuiApp(loader=lambda: empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, AccountsScreen)
        assert screen.has_no_accounts()


@pytest.mark.asyncio
async def test_accounts_screen_renders_error_when_loader_raises() -> None:
    """A loader exception surfaces inline; the app stays interactive."""

    def boom() -> AccountsData:
        raise RuntimeError("api unreachable")

    app = TulipTuiApp(loader=boom)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, AccountsScreen)
        assert screen.last_error == "api unreachable"
        # Still runnable: quit binding must continue to work.
        await pilot.press("q")
    assert app.return_code == 0


@pytest.mark.asyncio
async def test_accounts_screen_shows_balance_dash_when_no_postings() -> None:
    """An account with ``balance=None`` renders as ``—`` (not ``0.00``)."""
    fresh = AccountSummary(
        id="acc-4",
        code="assets:fresh",
        name="Fresh",
        type="asset",
        currency="USD",
        balance=None,
    )
    data = AccountsData(
        as_of="2026-05-17",
        accounts=(fresh,),
        groups=(AccountGroup(type="asset", accounts=(fresh,), totals=()),),
    )
    app = TulipTuiApp(loader=lambda: data)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, AccountsScreen)
        rendered = screen.rendered_rows()

    fresh_row = next(row for row in rendered if "Fresh" in row)
    assert "—" in fresh_row
    assert "0.00" not in fresh_row

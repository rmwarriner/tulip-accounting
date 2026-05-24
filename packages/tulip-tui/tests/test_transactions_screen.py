"""Pilot-mode tests for ``TransactionsScreen``.

The transactions register is the v1 TUI's second screen (P9.2 of
[#309](https://github.com/rmwarriner/tulip-accounting/issues/309)).
Tests inject ``TransactionsData`` directly through the screen's loader
seam, mirroring the pattern in ``test_accounts_screen.py``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tulip_tui.app import TulipTuiApp
from tulip_tui.data.accounts import AccountsData
from tulip_tui.data.transactions import (
    PostingSummary,
    TransactionsData,
    TransactionSummary,
)
from tulip_tui.screens.transactions import TransactionsScreen


def _sample_data() -> TransactionsData:
    first = TransactionSummary(
        id="tx-1",
        date="2026-05-14",
        description="Trader Joe's",
        reference=None,
        notes=None,
        status="posted",
        postings=(
            PostingSummary(
                account_id="acc-1",
                account_label="Checking",
                amount=Decimal("-67.21"),
                currency="USD",
                memo=None,
            ),
            PostingSummary(
                account_id="acc-2",
                account_label="Groceries",
                amount=Decimal("67.21"),
                currency="USD",
                memo=None,
            ),
        ),
        amount_display="-67.21 USD",
    )
    second = TransactionSummary(
        id="tx-2",
        date="2026-05-12",
        description="Netflix",
        reference="INV-12345",
        notes="annual sub",
        status="pending",
        postings=(
            PostingSummary(
                account_id="acc-3",
                account_label="Visa",
                amount=Decimal("-15.49"),
                currency="USD",
                memo=None,
            ),
            PostingSummary(
                account_id="acc-4",
                account_label="Subscriptions",
                amount=Decimal("15.49"),
                currency="USD",
                memo="annual",
            ),
        ),
        amount_display="-15.49 USD",
    )
    return TransactionsData(transactions=(first, second))


async def _push_transactions(app: TulipTuiApp, data: TransactionsData) -> TransactionsScreen:
    screen = TransactionsScreen(loader=lambda: data)
    await app.push_screen(screen)
    return screen


@pytest.mark.asyncio
async def test_transactions_screen_renders_rows() -> None:
    """Each transaction becomes a row with date, description, status, amount."""
    accounts_loader = lambda: AccountsData(  # noqa: E731
        as_of="2026-05-17", accounts=(), groups=()
    )
    app = TulipTuiApp(loader=accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_transactions(app, _sample_data())
        await pilot.pause()
        rendered = screen.rendered_rows()

    assert len(rendered) == 2
    assert any("Trader Joe's" in row and "-67.21" in row for row in rendered)
    assert any("Netflix" in row and "-15.49" in row for row in rendered)
    assert any("posted" in row for row in rendered)
    assert any("pending" in row for row in rendered)


@pytest.mark.asyncio
async def test_transactions_screen_detail_pane_follows_cursor() -> None:
    """Detail pane shows the cursor row's postings and updates on move."""
    accounts_loader = lambda: AccountsData(  # noqa: E731
        as_of="2026-05-17", accounts=(), groups=()
    )
    app = TulipTuiApp(loader=accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_transactions(app, _sample_data())
        await pilot.pause()

        detail = screen.detail_text()
        assert "Trader Joe's" in detail
        assert "Checking" in detail
        assert "Groceries" in detail
        assert "-67.21" in detail
        assert "67.21 USD" in detail  # both legs

        # Move the cursor down → the detail pane now reflects the second row.
        await pilot.press("down")
        await pilot.pause()
        detail = screen.detail_text()
        assert "Netflix" in detail
        assert "Visa" in detail
        assert "Subscriptions" in detail
        assert "INV-12345" in detail  # reference surfaces in detail
        assert "annual sub" in detail  # notes surface in detail


@pytest.mark.asyncio
async def test_transactions_screen_empty_state() -> None:
    """Zero transactions → an explanatory message, not a confusing empty table."""
    accounts_loader = lambda: AccountsData(  # noqa: E731
        as_of="2026-05-17", accounts=(), groups=()
    )
    app = TulipTuiApp(loader=accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_transactions(app, TransactionsData(transactions=()))
        await pilot.pause()
        assert screen.has_no_transactions()
        assert "no transactions" in screen.detail_text().lower()


@pytest.mark.asyncio
async def test_transactions_screen_renders_loader_error_inline() -> None:
    """A loader exception surfaces in the detail pane; the app stays interactive."""

    def boom() -> TransactionsData:
        raise RuntimeError("network blip")

    accounts_loader = lambda: AccountsData(  # noqa: E731
        as_of="2026-05-17", accounts=(), groups=()
    )
    app = TulipTuiApp(loader=accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = TransactionsScreen(loader=boom)
        await app.push_screen(screen)
        await pilot.pause()
        assert "network blip" in screen.detail_text()
        # The screen pops back to accounts cleanly on escape.
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("q")
    assert app.return_code == 0


@pytest.mark.asyncio
async def test_accounts_screen_enter_pushes_transactions_screen() -> None:
    """Pressing ``enter`` on an account row drills into the transactions screen."""
    from tulip_tui.data.accounts import (
        AccountGroup,
        AccountSummary,
        CurrencyTotal,
    )

    checking = AccountSummary(
        id="acc-1",
        code="assets:checking",
        name="Checking",
        type="asset",
        currency="USD",
        balance=Decimal("100.00"),
    )
    accounts_data = AccountsData(
        as_of="2026-05-17",
        accounts=(checking,),
        groups=(
            AccountGroup(
                type="asset",
                accounts=(checking,),
                totals=(CurrencyTotal(currency="USD", amount=Decimal("100.00")),),
            ),
        ),
    )

    captured: dict[str, str | None] = {}

    def tx_loader_factory(account_id: str | None) -> object:
        captured["account_id"] = account_id

        def _load() -> TransactionsData:
            return TransactionsData(transactions=())

        return _load

    app = TulipTuiApp(
        loader=lambda: accounts_data,
        transactions_loader_factory=tx_loader_factory,  # type: ignore[arg-type]
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        # Cursor starts at row 0 (the group header); one ``down`` moves
        # it onto the first real account row. ``enter`` drills in.
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        from tulip_tui.screens.transactions import TransactionsScreen as _TS

        assert isinstance(app.screen, _TS)

    # The drill-in passes the selected account's id into the factory.
    assert captured["account_id"] == "acc-1"


@pytest.mark.asyncio
async def test_detail_pane_shows_tags() -> None:
    """Tags appear in the detail pane when the transaction has them."""
    tagged_tx = TransactionSummary(
        id="tx-tag",
        date="2026-05-14",
        description="Groceries",
        reference=None,
        notes=None,
        status="posted",
        postings=(
            PostingSummary(
                account_id="acc-1",
                account_label="Checking",
                amount=Decimal("-20.00"),
                currency="USD",
                memo=None,
            ),
            PostingSummary(
                account_id="acc-2",
                account_label="Food",
                amount=Decimal("20.00"),
                currency="USD",
                memo=None,
            ),
        ),
        amount_display="-20.00 USD",
        tags=("food", "grocery"),
    )
    accounts_loader = lambda: AccountsData(  # noqa: E731
        as_of="2026-05-14", accounts=(), groups=()
    )
    app = TulipTuiApp(loader=accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_transactions(app, TransactionsData(transactions=(tagged_tx,)))
        await pilot.pause()
        detail = screen.detail_text()
        assert "food" in detail
        assert "grocery" in detail

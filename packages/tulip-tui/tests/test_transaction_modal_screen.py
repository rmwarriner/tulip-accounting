"""Pilot-mode tests for the transaction add/edit/void modals (P9.6.c)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tulip_tui.app import TulipTuiApp
from tulip_tui.data.accounts import AccountsData
from tulip_tui.data.transaction_write import (
    ParsedPosting,
    TransactionDraft,
)
from tulip_tui.data.transactions import (
    PostingSummary,
    TransactionsData,
    TransactionSummary,
)
from tulip_tui.screens.transaction_modal import (
    TransactionEditModal,
    VoidConfirmModal,
)
from tulip_tui.screens.transactions import TransactionsScreen


def _accounts_loader() -> AccountsData:
    return AccountsData(as_of="2026-05-20", accounts=(), groups=())


def _tx(*, status: str = "pending", tx_id: str = "tx-1") -> TransactionSummary:
    return TransactionSummary(
        id=tx_id,
        date="2026-05-20",
        description="Lunch",
        status=status,
        reference=None,
        notes=None,
        amount_display="12.50 USD",
        postings=(
            PostingSummary(
                account_id="acc-1",
                account_label="1110",
                amount=Decimal("-12.50"),
                currency="USD",
                memo=None,
            ),
            PostingSummary(
                account_id="acc-2",
                account_label="5100",
                amount=Decimal("12.50"),
                currency="USD",
                memo=None,
            ),
        ),
    )


def _txs_data(*txs: TransactionSummary) -> TransactionsData:
    return TransactionsData(transactions=txs)


# ---- modal-only renders ------------------------------------------------


@pytest.mark.asyncio
async def test_modal_renders_with_defaults() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = TransactionEditModal()
        await app.push_screen(modal)
        await pilot.pause()
        date_widget = modal.query_one("#tx-date")
        date_value = date_widget.value  # type: ignore[attr-defined]
        # Default is today (UTC, ISO).
        assert len(date_value) == 10
        assert date_value[4] == "-" and date_value[7] == "-"


@pytest.mark.asyncio
async def test_modal_dismisses_with_none_on_cancel() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = TransactionEditModal()
        await app.push_screen(modal)
        await pilot.pause()
        modal.dismiss(None)
        await pilot.pause()
        assert all(not isinstance(s, TransactionEditModal) for s in app.screen_stack)


@pytest.mark.asyncio
async def test_modal_validates_blank_description() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = TransactionEditModal(
            initial_postings="1110=-1.00\n5100=1.00",
        )
        await app.push_screen(modal)
        await pilot.pause()
        snap = modal.snapshot()
        assert snap is None
        assert "description" in modal.error_text()


@pytest.mark.asyncio
async def test_modal_validates_bad_date() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = TransactionEditModal(
            initial_date="not-a-date",
            initial_description="x",
            initial_postings="1110=-1.00\n5100=1.00",
        )
        await app.push_screen(modal)
        await pilot.pause()
        snap = modal.snapshot()
        assert snap is None
        assert "YYYY-MM-DD" in modal.error_text()


@pytest.mark.asyncio
async def test_modal_builds_draft_on_valid_input() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = TransactionEditModal(
            initial_date="2026-05-20",
            initial_description="Lunch",
            initial_postings="1110=-12.50\n5100=12.50",
        )
        await app.push_screen(modal)
        await pilot.pause()
        draft = modal.snapshot()
        assert draft is not None
        assert draft.date == "2026-05-20"
        assert draft.description == "Lunch"
        assert len(draft.postings) == 2


@pytest.mark.asyncio
async def test_modal_validates_unbalanced_or_single_posting() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = TransactionEditModal(
            initial_date="2026-05-20",
            initial_description="x",
            initial_postings="1110=-1.00",  # only one posting
        )
        await app.push_screen(modal)
        await pilot.pause()
        snap = modal.snapshot()
        assert snap is None
        assert "two postings" in modal.error_text()


# ---- void modal --------------------------------------------------------


@pytest.mark.asyncio
async def test_void_modal_dismisses_with_none_on_cancel() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = VoidConfirmModal(tx_id="tx-1", description="Lunch")
        await app.push_screen(modal)
        await pilot.pause()
        modal.dismiss(None)
        await pilot.pause()


@pytest.mark.asyncio
async def test_void_modal_requires_reason() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = VoidConfirmModal(tx_id="tx-1", description="Lunch")
        await app.push_screen(modal)
        await pilot.pause()
        # Click confirm without entering a reason.
        modal.query_one("#void-confirm").press()  # type: ignore[attr-defined]
        await pilot.pause()
        assert "reason is required" in modal.error_text()
        # Modal still on top — not dismissed.
        modal_present = any(isinstance(s, VoidConfirmModal) for s in app.screen_stack)
        assert modal_present


# ---- bindings on transactions screen -----------------------------------


@pytest.mark.asyncio
async def test_n_opens_add_modal() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = TransactionsScreen(loader=lambda: _txs_data())
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        modal_present = any(isinstance(s, TransactionEditModal) for s in app.screen_stack)

    assert modal_present


@pytest.mark.asyncio
async def test_n_modal_confirm_fires_create_action() -> None:
    calls: list[TransactionDraft] = []

    def on_create(draft: TransactionDraft) -> object:
        calls.append(draft)
        return {"id": "tx-new"}

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = TransactionsScreen(
            loader=lambda: _txs_data(),
            on_create=on_create,
        )
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        modal = next(s for s in app.screen_stack if isinstance(s, TransactionEditModal))
        draft = TransactionDraft(
            date="2026-05-20",
            description="Lunch",
            reference=None,
            postings=(
                ParsedPosting(account="1110", amount=Decimal("-12.50"), currency=None),
                ParsedPosting(account="5100", amount=Decimal("12.50"), currency=None),
            ),
        )
        modal.dismiss(draft)
        await pilot.pause()

    assert len(calls) == 1
    assert calls[0].description == "Lunch"


@pytest.mark.asyncio
async def test_e_on_pending_tx_opens_edit_modal() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = TransactionsScreen(loader=lambda: _txs_data(_tx(status="pending")))
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        modals = [s for s in app.screen_stack if isinstance(s, TransactionEditModal)]
        assert len(modals) == 1


@pytest.mark.asyncio
async def test_e_on_posted_tx_refuses() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = TransactionsScreen(loader=lambda: _txs_data(_tx(status="posted")))
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        modals = [s for s in app.screen_stack if isinstance(s, TransactionEditModal)]

    assert modals == []
    assert "PENDING" in screen.notice()


@pytest.mark.asyncio
async def test_x_opens_void_modal() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = TransactionsScreen(loader=lambda: _txs_data(_tx(status="posted")))
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        modal_present = any(isinstance(s, VoidConfirmModal) for s in app.screen_stack)

    assert modal_present


@pytest.mark.asyncio
async def test_x_modal_confirm_fires_void_action() -> None:
    calls: list[tuple[str, str]] = []

    def on_void(tx_id: str, reason: str) -> object:
        calls.append((tx_id, reason))
        return {"reversal_id": "tx-rev"}

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = TransactionsScreen(
            loader=lambda: _txs_data(_tx(status="posted", tx_id="tx-99")),
            on_void=on_void,
        )
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        modal = next(s for s in app.screen_stack if isinstance(s, VoidConfirmModal))
        modal.dismiss("test reason")
        await pilot.pause()

    assert calls == [("tx-99", "test reason")]
    assert "voided" in screen.notice()


@pytest.mark.asyncio
async def test_n_with_no_create_action_surfaces_error() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = TransactionsScreen(loader=lambda: _txs_data())
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        modal = next(s for s in app.screen_stack if isinstance(s, TransactionEditModal))
        # Force a valid draft and dismiss.
        draft = TransactionDraft(
            date="2026-05-20",
            description="x",
            reference=None,
            postings=(
                ParsedPosting(account="1", amount=Decimal("-1"), currency=None),
                ParsedPosting(account="2", amount=Decimal("1"), currency=None),
            ),
        )
        modal.dismiss(draft)
        await pilot.pause()

    assert "create failed" in screen.notice()

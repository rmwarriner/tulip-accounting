"""Pilot-mode tests for the account add/edit modal (#431)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tulip_tui.app import TulipTuiApp
from tulip_tui.data.account_write import AccountDraft
from tulip_tui.data.accounts import (
    AccountGroup,
    AccountsData,
    AccountSummary,
    CurrencyTotal,
)
from tulip_tui.screens.account_modal import AccountEditModal
from tulip_tui.screens.accounts import AccountsScreen


def _accounts_loader_with_seed() -> AccountsData:
    """Loader returning a single seed account so the screen has something
    to focus for `e`-binding tests."""
    a = AccountSummary(
        id="acc-1",
        code="1110",
        name="Checking",
        type="asset",
        currency="USD",
        balance=Decimal("100.00"),
    )
    totals = (CurrencyTotal("USD", Decimal("100.00")),)
    return AccountsData(
        as_of="2026-05-20",
        accounts=(a,),
        groups=(AccountGroup(type="asset", accounts=(a,), totals=totals),),
    )


def _accounts_loader_empty() -> AccountsData:
    return AccountsData(as_of="2026-05-20", accounts=(), groups=())


# --- modal-only renders + validation ------------------------------------


@pytest.mark.asyncio
async def test_modal_renders_with_defaults() -> None:
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = AccountEditModal()
        await app.push_screen(modal)
        await pilot.pause()
        # Default type is "asset", currency is "USD", visibility is "shared".
        type_widget = modal.query_one("#acct-type")
        assert type_widget.value == "asset"  # type: ignore[attr-defined]
        cur_widget = modal.query_one("#acct-currency")
        assert cur_widget.value == "USD"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_modal_dismisses_none_on_cancel() -> None:
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = AccountEditModal()
        await app.push_screen(modal)
        await pilot.pause()
        modal.dismiss(None)
        await pilot.pause()


@pytest.mark.asyncio
async def test_modal_validates_blank_name() -> None:
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = AccountEditModal(initial_name="")
        await app.push_screen(modal)
        await pilot.pause()
        snap = modal.snapshot()
    assert snap is None
    assert "name" in modal.error_text()


@pytest.mark.asyncio
async def test_modal_validates_unknown_type() -> None:
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = AccountEditModal(initial_name="X", initial_type="garbage")
        await app.push_screen(modal)
        await pilot.pause()
        snap = modal.snapshot()
    assert snap is None
    assert "type" in modal.error_text()


@pytest.mark.asyncio
async def test_modal_validates_bad_currency() -> None:
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = AccountEditModal(initial_name="X", initial_currency="dollars")
        await app.push_screen(modal)
        await pilot.pause()
        snap = modal.snapshot()
    assert snap is None
    assert "currency" in modal.error_text()


@pytest.mark.asyncio
async def test_modal_builds_draft_on_valid_input() -> None:
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = AccountEditModal(
            initial_name="Checking",
            initial_type="asset",
            initial_currency="USD",
            initial_code="1110",
        )
        await app.push_screen(modal)
        await pilot.pause()
        draft = modal.snapshot()
    assert draft is not None
    assert draft.name == "Checking"
    assert draft.type == "asset"
    assert draft.currency == "USD"
    assert draft.code == "1110"
    assert draft.visibility == "shared"


@pytest.mark.asyncio
async def test_modal_carries_notes_and_placeholder_in_draft() -> None:
    """#50 + #52: notes and is_placeholder flow through the modal."""
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = AccountEditModal(
            initial_name="Current Assets",
            initial_type="asset",
            initial_currency="USD",
            initial_notes="Pre-filled note",
            initial_placeholder=True,
        )
        await app.push_screen(modal)
        await pilot.pause()
        draft = modal.snapshot()
    assert draft is not None
    assert draft.notes == "Pre-filled note"
    assert draft.is_placeholder is True


@pytest.mark.asyncio
async def test_modal_blank_notes_become_none() -> None:
    """Empty notes input → ``None`` in the draft (omitted from PATCH)."""
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = AccountEditModal(initial_name="X")
        await app.push_screen(modal)
        await pilot.pause()
        draft = modal.snapshot()
    assert draft is not None
    assert draft.notes is None
    assert draft.is_placeholder is False


@pytest.mark.asyncio
async def test_modal_currency_is_uppercased() -> None:
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = AccountEditModal(initial_name="X", initial_currency="usd")
        await app.push_screen(modal)
        await pilot.pause()
        draft = modal.snapshot()
    assert draft is not None
    assert draft.currency == "USD"


# --- bindings on the accounts screen ------------------------------------


@pytest.mark.asyncio
async def test_n_opens_add_modal() -> None:
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = AccountsScreen(loader=_accounts_loader_empty)
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        modals = [s for s in app.screen_stack if isinstance(s, AccountEditModal)]
    assert len(modals) == 1


@pytest.mark.asyncio
async def test_n_modal_confirm_fires_create_action() -> None:
    calls: list[AccountDraft] = []

    def on_create(draft: AccountDraft) -> object:
        calls.append(draft)
        return {"id": "acc-new", "name": draft.name}

    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = AccountsScreen(
            loader=_accounts_loader_empty,
            on_create_account=on_create,
        )
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        modal = next(s for s in app.screen_stack if isinstance(s, AccountEditModal))
        draft = AccountDraft(
            name="Checking",
            type="asset",
            currency="USD",
            code="1110",
            subtype=None,
            visibility="shared",
            parent_account_id=None,
        )
        modal.dismiss(draft)
        await pilot.pause()
    assert len(calls) == 1
    assert calls[0].name == "Checking"
    assert "created" in screen.notice()


@pytest.mark.asyncio
async def test_n_seeds_currency_from_first_existing_account() -> None:
    app = TulipTuiApp(loader=_accounts_loader_with_seed)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = AccountsScreen(loader=_accounts_loader_with_seed)
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        modal = next(s for s in app.screen_stack if isinstance(s, AccountEditModal))
        cur_value = modal.query_one("#acct-currency").value  # type: ignore[attr-defined]
    assert cur_value == "USD"


@pytest.mark.asyncio
async def test_e_on_focused_account_opens_modal_prefilled() -> None:
    app = TulipTuiApp(loader=_accounts_loader_with_seed)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = AccountsScreen(loader=_accounts_loader_with_seed)
        await app.push_screen(screen)
        await pilot.pause()
        # Move cursor onto the seed account (skipping the group header row).
        table = screen.query_one("#accounts")
        table.move_cursor(row=1)  # type: ignore[attr-defined]
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        modals = [s for s in app.screen_stack if isinstance(s, AccountEditModal)]
        assert len(modals) == 1
        modal = modals[0]
        name_value = modal.query_one("#acct-name").value  # type: ignore[attr-defined]
        code_value = modal.query_one("#acct-code").value  # type: ignore[attr-defined]
    assert name_value == "Checking"
    assert code_value == "1110"


@pytest.mark.asyncio
async def test_e_with_no_focused_account_surfaces_notice() -> None:
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = AccountsScreen(loader=_accounts_loader_empty)
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        modals = [s for s in app.screen_stack if isinstance(s, AccountEditModal)]
    assert modals == []
    assert "focus" in screen.notice()


@pytest.mark.asyncio
async def test_e_modal_confirm_fires_edit_action() -> None:
    calls: list[tuple[str, AccountDraft]] = []

    def on_edit(aid: str, draft: AccountDraft) -> object:
        calls.append((aid, draft))
        return {"id": aid, "name": draft.name}

    app = TulipTuiApp(loader=_accounts_loader_with_seed)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = AccountsScreen(
            loader=_accounts_loader_with_seed,
            on_edit_account=on_edit,
        )
        await app.push_screen(screen)
        await pilot.pause()
        table = screen.query_one("#accounts")
        table.move_cursor(row=1)  # type: ignore[attr-defined]
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        modal = next(s for s in app.screen_stack if isinstance(s, AccountEditModal))
        draft = AccountDraft(
            name="Renamed",
            type="asset",
            currency="USD",
            code="1110",
            subtype=None,
            visibility="shared",
            parent_account_id=None,
        )
        modal.dismiss(draft)
        await pilot.pause()
    assert len(calls) == 1
    assert calls[0][0] == "acc-1"
    assert calls[0][1].name == "Renamed"


@pytest.mark.asyncio
async def test_n_with_no_create_action_surfaces_error() -> None:
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = AccountsScreen(loader=_accounts_loader_empty)
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        modal = next(s for s in app.screen_stack if isinstance(s, AccountEditModal))
        draft = AccountDraft(
            name="X",
            type="asset",
            currency="USD",
            code=None,
            subtype=None,
            visibility="shared",
            parent_account_id=None,
        )
        modal.dismiss(draft)
        await pilot.pause()
    assert "create failed" in screen.notice()

"""Pilot-mode tests for the reconciliation detail screen (P9.6.b).

Covers row rendering, every binding (`a`/`x`/`m`/`k`/`f`/`c`), the
manual-match picker modal, the drill-in from the reconciliations list,
and the error rendering path.
"""

from __future__ import annotations

import pytest

from tulip_tui.app import TulipTuiApp
from tulip_tui.data.accounts import AccountsData
from tulip_tui.data.reconciliation_detail import (
    MatchSummary,
    ReconciliationDetail,
    ReconciliationEnvelope,
    UnmatchedLine,
    UnmatchedTransaction,
)
from tulip_tui.data.reconciliations import (
    ReconciliationsData,
    ReconciliationSummary,
)
from tulip_tui.screens.reconciliation_detail import (
    ManualMatchPickerModal,
    ReconciliationDetailScreen,
)


def _accounts_loader() -> AccountsData:
    return AccountsData(as_of="2026-05-17", accounts=(), groups=())


def _envelope(
    *,
    source_batch: str | None = "batch-9",
    status: str = "open",
) -> ReconciliationEnvelope:
    return ReconciliationEnvelope(
        id="rec-1",
        account_id="acc-1",
        statement_period_start="2026-04-01",
        statement_period_end="2026-04-30",
        statement_starting_balance="1000.00",
        statement_ending_balance="1234.56",
        currency="USD",
        status=status,
        source_import_batch_id=source_batch,
        created_at="2026-05-01T12:00:00Z",
        completed_at=None,
    )


def _detail(
    *,
    matches: tuple[MatchSummary, ...] = (),
    lines: tuple[UnmatchedLine, ...] = (),
    txs: tuple[UnmatchedTransaction, ...] = (),
    source_batch: str | None = "batch-9",
    status: str = "open",
) -> ReconciliationDetail:
    return ReconciliationDetail(
        envelope=_envelope(source_batch=source_batch, status=status),
        matches=matches,
        unmatched_lines=lines,
        unmatched_transactions=txs,
    )


def _match(idx: int = 1, *, manual: bool = False) -> MatchSummary:
    return MatchSummary(
        id=f"match-{idx}",
        statement_line_id=f"line-{idx}",
        ledger_transaction_id=f"tx-{idx}",
        match_amount="100.00",
        currency="USD",
        confidence=None if manual else "HIGH",
        created_by_user_id="user-1" if manual else None,
        is_manual=manual,
    )


def _line(idx: int = 3) -> UnmatchedLine:
    return UnmatchedLine(
        id=f"line-{idx}",
        line_number=idx,
        posted_date="2026-04-15",
        amount_display="-25.50",
        currency="USD",
        description=f"VENDOR {idx}",
        reference=None,
    )


def _tx(idx: int = 3) -> UnmatchedTransaction:
    return UnmatchedTransaction(
        id=f"tx-{idx}",
        date="2026-04-15",
        description=f"Tx desc {idx}",
        reference=None,
        status="posted",
    )


# --- happy-path renders --------------------------------------------------


@pytest.mark.asyncio
async def test_renders_all_three_tables() -> None:
    detail = _detail(
        matches=(_match(1),),
        lines=(_line(2),),
        txs=(_tx(3),),
    )
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationDetailScreen(
            loader=lambda: detail,
            on_auto_match=lambda: {"matches_created": 0},
            on_reject=lambda _m: None,
            on_manual_match=lambda *_a: {"id": "m"},
            on_paper_match=lambda _t: {"id": "m"},
            on_carry_forward=lambda _t: {"transaction_ids": ["tx-3"]},
            on_complete=lambda: {"affected_transaction_count": 0},
        )
        await app.push_screen(screen)
        await pilot.pause()
        status = screen.status_text()

    assert "1 matches" in status
    assert "1 unmatched lines" in status
    assert "1 unmatched txs" in status


@pytest.mark.asyncio
async def test_header_shows_period_and_balances() -> None:
    detail = _detail()
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationDetailScreen(
            loader=lambda: detail,
            on_auto_match=lambda: {},
            on_reject=lambda _m: None,
            on_manual_match=lambda *_a: {},
            on_paper_match=lambda _t: {},
            on_carry_forward=lambda _t: {},
            on_complete=lambda: {},
        )
        await app.push_screen(screen)
        await pilot.pause()
        header = screen.header_text()

    assert "2026-04-01" in header
    assert "2026-04-30" in header
    assert "1000.00" in header
    assert "1234.56" in header
    assert "imported" in header  # source_batch is set → "imported"


@pytest.mark.asyncio
async def test_paper_recon_shows_paper_in_header() -> None:
    detail = _detail(source_batch=None)
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationDetailScreen(
            loader=lambda: detail,
            on_auto_match=lambda: {},
            on_reject=lambda _m: None,
            on_manual_match=lambda *_a: {},
            on_paper_match=lambda _t: {},
            on_carry_forward=lambda _t: {},
            on_complete=lambda: {},
        )
        await app.push_screen(screen)
        await pilot.pause()
        assert "paper" in screen.header_text()


# --- bindings ------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_match_binding_calls_action() -> None:
    calls: list[str] = []
    state = {"data": _detail(lines=(_line(),), txs=(_tx(),))}

    def on_auto_match() -> dict[str, object]:
        calls.append("auto")
        state["data"] = _detail(matches=(_match(1),), lines=(_line(),), txs=(_tx(),))
        return {"matches_created": 1}

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationDetailScreen(
            loader=lambda: state["data"],
            on_auto_match=on_auto_match,
            on_reject=lambda _m: None,
            on_manual_match=lambda *_a: {},
            on_paper_match=lambda _t: {},
            on_carry_forward=lambda _t: {},
            on_complete=lambda: {},
        )
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()

    assert calls == ["auto"]
    assert "auto-matched" in screen.notice()


@pytest.mark.asyncio
async def test_auto_match_refuses_when_matches_exist() -> None:
    calls: list[str] = []
    detail = _detail(matches=(_match(1),), lines=(_line(),), txs=(_tx(),))

    def on_auto_match() -> dict[str, object]:
        calls.append("auto")
        return {}

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationDetailScreen(
            loader=lambda: detail,
            on_auto_match=on_auto_match,
            on_reject=lambda _m: None,
            on_manual_match=lambda *_a: {},
            on_paper_match=lambda _t: {},
            on_carry_forward=lambda _t: {},
            on_complete=lambda: {},
        )
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()

    assert calls == []
    assert "matches already exist" in screen.notice()


@pytest.mark.asyncio
async def test_reject_binding_calls_action_with_focused_match() -> None:
    calls: list[str] = []
    detail = _detail(matches=(_match(1), _match(2)), lines=(_line(),))

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationDetailScreen(
            loader=lambda: detail,
            on_auto_match=lambda: {},
            on_reject=lambda match_id: calls.append(match_id),
            on_manual_match=lambda *_a: {},
            on_paper_match=lambda _t: {},
            on_carry_forward=lambda _t: {},
            on_complete=lambda: {},
        )
        await app.push_screen(screen)
        await pilot.pause()
        # Move focus into matches table (it's the first one).
        screen.query_one("#rcd-matches").focus()
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()

    assert calls == ["match-1"]


@pytest.mark.asyncio
async def test_reject_refuses_without_match_focus() -> None:
    calls: list[str] = []
    detail = _detail(matches=(_match(1),), lines=(_line(),))

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationDetailScreen(
            loader=lambda: detail,
            on_auto_match=lambda: {},
            on_reject=lambda match_id: calls.append(match_id),
            on_manual_match=lambda *_a: {},
            on_paper_match=lambda _t: {},
            on_carry_forward=lambda _t: {},
            on_complete=lambda: {},
        )
        await app.push_screen(screen)
        await pilot.pause()
        # Default focus is on the unmatched-lines table.
        await pilot.press("x")
        await pilot.pause()

    assert calls == []
    assert "focus a match row" in screen.notice()


@pytest.mark.asyncio
async def test_manual_match_opens_picker_modal() -> None:
    detail = _detail(lines=(_line(3),), txs=(_tx(7),))
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationDetailScreen(
            loader=lambda: detail,
            on_auto_match=lambda: {},
            on_reject=lambda _m: None,
            on_manual_match=lambda *_a: {"id": "m"},
            on_paper_match=lambda _t: {},
            on_carry_forward=lambda _t: {},
            on_complete=lambda: {},
        )
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        modal_present = any(isinstance(s, ManualMatchPickerModal) for s in app.screen_stack)

    assert modal_present


@pytest.mark.asyncio
async def test_manual_match_picker_dismiss_fires_action() -> None:
    calls: list[tuple[str, str, str, str]] = []
    detail = _detail(lines=(_line(3),), txs=(_tx(7),))

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationDetailScreen(
            loader=lambda: detail,
            on_auto_match=lambda: {},
            on_reject=lambda _m: None,
            on_manual_match=lambda a, b, c, d: calls.append((a, b, c, d)) or {"id": "m"},
            on_paper_match=lambda _t: {},
            on_carry_forward=lambda _t: {},
            on_complete=lambda: {},
        )
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        modal = next(s for s in app.screen_stack if isinstance(s, ManualMatchPickerModal))
        modal.dismiss("tx-7")
        await pilot.pause()

    assert calls == [("line-3", "tx-7", "-25.50", "USD")]


@pytest.mark.asyncio
async def test_paper_match_only_on_paper_recon() -> None:
    calls: list[str] = []
    # Imported reconciliation — k should refuse.
    detail = _detail(txs=(_tx(),), source_batch="batch-9")

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationDetailScreen(
            loader=lambda: detail,
            on_auto_match=lambda: {},
            on_reject=lambda _m: None,
            on_manual_match=lambda *_a: {},
            on_paper_match=lambda t: calls.append(t) or {"id": "m"},
            on_carry_forward=lambda _t: {},
            on_complete=lambda: {},
        )
        await app.push_screen(screen)
        await pilot.pause()
        # Focus the txs table.
        screen.query_one("#rcd-txs").focus()
        await pilot.pause()
        await pilot.press("k")
        await pilot.pause()

    assert calls == []
    assert "paper" in screen.notice()


@pytest.mark.asyncio
async def test_paper_match_fires_on_paper_recon() -> None:
    calls: list[str] = []
    detail = _detail(txs=(_tx(),), source_batch=None)

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationDetailScreen(
            loader=lambda: detail,
            on_auto_match=lambda: {},
            on_reject=lambda _m: None,
            on_manual_match=lambda *_a: {},
            on_paper_match=lambda t: calls.append(t) or {"id": "m"},
            on_carry_forward=lambda _t: {},
            on_complete=lambda: {},
        )
        await app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#rcd-txs").focus()
        await pilot.pause()
        await pilot.press("k")
        await pilot.pause()

    assert calls == ["tx-3"]


@pytest.mark.asyncio
async def test_carry_forward_fires_action() -> None:
    calls: list[str] = []
    detail = _detail(txs=(_tx(),))

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationDetailScreen(
            loader=lambda: detail,
            on_auto_match=lambda: {},
            on_reject=lambda _m: None,
            on_manual_match=lambda *_a: {},
            on_paper_match=lambda _t: {},
            on_carry_forward=lambda t: calls.append(t) or {"transaction_ids": [t]},
            on_complete=lambda: {},
        )
        await app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#rcd-txs").focus()
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()

    assert calls == ["tx-3"]


@pytest.mark.asyncio
async def test_complete_fires_action() -> None:
    calls: list[str] = []
    detail = _detail()

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationDetailScreen(
            loader=lambda: detail,
            on_auto_match=lambda: {},
            on_reject=lambda _m: None,
            on_manual_match=lambda *_a: {},
            on_paper_match=lambda _t: {},
            on_carry_forward=lambda _t: {},
            on_complete=lambda: calls.append("done") or {"affected_transaction_count": 5},
        )
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()

    assert calls == ["done"]


@pytest.mark.asyncio
async def test_complete_refuses_on_already_complete_recon() -> None:
    calls: list[str] = []
    detail = _detail(status="complete")

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationDetailScreen(
            loader=lambda: detail,
            on_auto_match=lambda: {},
            on_reject=lambda _m: None,
            on_manual_match=lambda *_a: {},
            on_paper_match=lambda _t: {},
            on_carry_forward=lambda _t: {},
            on_complete=lambda: calls.append("done") or {},
        )
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()

    assert calls == []
    assert "already complete" in screen.notice()


@pytest.mark.asyncio
async def test_inline_error_on_load_failure() -> None:
    def _boom() -> ReconciliationDetail:
        raise RuntimeError("offline")

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationDetailScreen(
            loader=_boom,
            on_auto_match=lambda: {},
            on_reject=lambda _m: None,
            on_manual_match=lambda *_a: {},
            on_paper_match=lambda _t: {},
            on_carry_forward=lambda _t: {},
            on_complete=lambda: {},
        )
        await app.push_screen(screen)
        await pilot.pause()

    assert "offline" in screen.status_text()


@pytest.mark.asyncio
async def test_reconciliations_screen_enter_drills_into_detail() -> None:
    drilled: list[str] = []
    recs = ReconciliationsData(
        reconciliations=(
            ReconciliationSummary(
                id="rec-1",
                account_id="acc-1",
                statement_period_start="2026-04-01",
                statement_period_end="2026-04-30",
                statement_starting_balance="1000.00",
                statement_ending_balance="1234.56",
                currency="USD",
                status="open",
                source_import_batch_id="batch-9",
                created_at="2026-05-01T12:00:00Z",
                completed_at=None,
            ),
        ),
    )

    app = TulipTuiApp(
        loader=_accounts_loader,
        reconciliations_loader=lambda: recs,
        reconciliation_detail_factory=lambda _rid: lambda: _detail(),
        reconciliation_auto_match=lambda _r: {},
        reconciliation_reject=lambda _r, _m: None,
        reconciliation_manual_match=lambda *_a: {},
        reconciliation_paper_match=lambda _r, _t: {},
        reconciliation_carry_forward=lambda _r, _t: {},
        reconciliation_complete=lambda _r: {},
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("c")  # open reconciliations browser
        await pilot.pause()

        from tulip_tui.screens.reconciliations import ReconciliationsScreen

        recs_screen = next(s for s in app.screen_stack if isinstance(s, ReconciliationsScreen))
        original = recs_screen._on_open_reconciliation  # type: ignore[attr-defined]

        def _capture(rid: str) -> None:
            drilled.append(rid)
            original(rid)

        recs_screen._on_open_reconciliation = _capture  # type: ignore[attr-defined]
        await pilot.press("enter")
        await pilot.pause()
        detail_present = any(isinstance(s, ReconciliationDetailScreen) for s in app.screen_stack)

    assert drilled == ["rec-1"]
    assert detail_present

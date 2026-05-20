"""Pilot-mode tests for the import-batch detail screen (P9.6.a).

Covers the row rendering, the `x`/`p`/`a` action bindings, the apply
confirm modal, refresh, error rendering, and the `i` → `enter` drill-in
from the list screen.
"""

from __future__ import annotations

import pytest

from tulip_tui.app import TulipTuiApp
from tulip_tui.data.accounts import AccountsData
from tulip_tui.data.import_batch_detail import (
    ImportBatchDetail,
    StatementLineSummary,
)
from tulip_tui.data.imports import ImportBatchSummary, ImportsData
from tulip_tui.screens.import_batch_detail import (
    ApplyConfirmModal,
    ImportBatchDetailScreen,
)


def _accounts_loader() -> AccountsData:
    return AccountsData(as_of="2026-05-17", accounts=(), groups=())


def _sample_detail() -> ImportBatchDetail:
    return ImportBatchDetail(
        id="batch-1",
        account_id="acc-1",
        source_format="ofx",
        source_filename="april.qfx",
        status="parsed",
        imported_count=3,
        skipped_count=0,
        error_count=0,
        created_at="2026-05-01T12:00:00Z",
        applied_at=None,
        reverted_at=None,
        lines=(
            StatementLineSummary(
                id="line-1",
                line_number=1,
                date="2026-05-01",
                description="AMAZON",
                amount_display="-42.17",
                currency="USD",
                is_excluded=False,
                promoted_transaction_id=None,
                reconciliation_match_id=None,
            ),
            StatementLineSummary(
                id="line-2",
                line_number=2,
                date="2026-05-02",
                description="LUNCH",
                amount_display="-12.50",
                currency="USD",
                is_excluded=True,
                promoted_transaction_id=None,
                reconciliation_match_id=None,
            ),
            StatementLineSummary(
                id="line-3",
                line_number=3,
                date="2026-05-03",
                description="PAYCHECK",
                amount_display="100.00",
                currency="USD",
                is_excluded=False,
                promoted_transaction_id="tx-99",
                reconciliation_match_id=None,
            ),
        ),
    )


def _empty_detail() -> ImportBatchDetail:
    return ImportBatchDetail(
        id="batch-0",
        account_id="acc-1",
        source_format="csv",
        source_filename="empty.csv",
        status="parsed",
        imported_count=0,
        skipped_count=0,
        error_count=0,
        created_at="2026-05-01T12:00:00Z",
        applied_at=None,
        reverted_at=None,
        lines=(),
    )


def _noop_exclude(_line_id: str, _is_excluded: bool) -> None: ...


def _noop_promote(_line_id: str) -> None: ...


def _noop_apply(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {"created_count": 0}


@pytest.mark.asyncio
async def test_detail_screen_renders_lines_with_status_markers() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ImportBatchDetailScreen(
            loader=_sample_detail,
            on_toggle_exclude=_noop_exclude,
            on_promote=_noop_promote,
            on_apply=_noop_apply,
        )
        await app.push_screen(screen)
        await pilot.pause()
        rows = screen.rendered_rows()

    assert len(rows) == 3
    assert "AMAZON" in rows[0] and "pending" in rows[0]
    assert "LUNCH" in rows[1] and "excluded" in rows[1]
    assert "PAYCHECK" in rows[2] and "promoted" in rows[2]


@pytest.mark.asyncio
async def test_detail_screen_status_strip_counts() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ImportBatchDetailScreen(
            loader=_sample_detail,
            on_toggle_exclude=_noop_exclude,
            on_promote=_noop_promote,
            on_apply=_noop_apply,
        )
        await app.push_screen(screen)
        await pilot.pause()
        status = screen.status_text()

    assert "1 pending" in status
    assert "1 excluded" in status
    assert "1 promoted" in status


@pytest.mark.asyncio
async def test_detail_screen_empty_state() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ImportBatchDetailScreen(
            loader=_empty_detail,
            on_toggle_exclude=_noop_exclude,
            on_promote=_noop_promote,
            on_apply=_noop_apply,
        )
        await app.push_screen(screen)
        await pilot.pause()
        assert "No statement lines" in screen.detail_text()


@pytest.mark.asyncio
async def test_detail_screen_error_path() -> None:
    def _boom() -> ImportBatchDetail:
        raise RuntimeError("offline")

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ImportBatchDetailScreen(
            loader=_boom,
            on_toggle_exclude=_noop_exclude,
            on_promote=_noop_promote,
            on_apply=_noop_apply,
        )
        await app.push_screen(screen)
        await pilot.pause()
        status = screen.status_text()

    assert "error" in status.lower()
    assert "offline" in status


@pytest.mark.asyncio
async def test_exclude_binding_calls_action_and_reloads() -> None:
    calls: list[tuple[str, bool]] = []

    def on_toggle(line_id: str, is_excluded: bool) -> None:
        calls.append((line_id, is_excluded))

    state = {"data": _sample_detail()}

    def loader() -> ImportBatchDetail:
        return state["data"]

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ImportBatchDetailScreen(
            loader=loader,
            on_toggle_exclude=on_toggle,
            on_promote=_noop_promote,
            on_apply=_noop_apply,
        )
        await app.push_screen(screen)
        await pilot.pause()
        # Cursor at row 0 (line-1, pending) — `x` should exclude.
        await pilot.press("x")
        await pilot.pause()

    assert calls == [("line-1", True)]
    assert "excluded" in screen.notice()


@pytest.mark.asyncio
async def test_exclude_binding_refuses_promoted_line() -> None:
    calls: list[tuple[str, bool]] = []

    def on_toggle(line_id: str, is_excluded: bool) -> None:
        calls.append((line_id, is_excluded))

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ImportBatchDetailScreen(
            loader=_sample_detail,
            on_toggle_exclude=on_toggle,
            on_promote=_noop_promote,
            on_apply=_noop_apply,
        )
        await app.push_screen(screen)
        await pilot.pause()
        # Move cursor to line-3 (promoted), then `x`.
        await pilot.press("down", "down")
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()

    # No action call should fire; notice tells the user why.
    assert calls == []
    assert "promoted" in screen.notice().lower()


@pytest.mark.asyncio
async def test_promote_binding_calls_action_and_reloads() -> None:
    calls: list[str] = []

    def on_promote(line_id: str) -> None:
        calls.append(line_id)

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ImportBatchDetailScreen(
            loader=_sample_detail,
            on_toggle_exclude=_noop_exclude,
            on_promote=on_promote,
            on_apply=_noop_apply,
        )
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()

    assert calls == ["line-1"]
    assert "promoted" in screen.notice()


@pytest.mark.asyncio
async def test_promote_binding_refuses_excluded_line() -> None:
    calls: list[str] = []

    def on_promote(line_id: str) -> None:
        calls.append(line_id)

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ImportBatchDetailScreen(
            loader=_sample_detail,
            on_toggle_exclude=_noop_exclude,
            on_promote=on_promote,
            on_apply=_noop_apply,
        )
        await app.push_screen(screen)
        await pilot.pause()
        # Move to line-2 (excluded), then `p`.
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()

    assert calls == []
    assert "excluded" in screen.notice().lower()


@pytest.mark.asyncio
async def test_apply_modal_renders_with_pending_count() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ImportBatchDetailScreen(
            loader=_sample_detail,
            on_toggle_exclude=_noop_exclude,
            on_promote=_noop_promote,
            on_apply=_noop_apply,
        )
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        # Modal is now on top — find it.
        modals = [s for s in app.screen_stack if isinstance(s, ApplyConfirmModal)]
        assert len(modals) == 1
        # Pending count from _sample_detail = 1.
        title_widget = modals[0].query_one("#apply-title")
        title_str = str(title_widget.render())
        assert "Apply 1 line(s)" in title_str


@pytest.mark.asyncio
async def test_apply_modal_cancel_clears_notice() -> None:
    apply_calls: list[tuple[bool, bool, bool]] = []

    def on_apply(a: bool, b: bool, c: bool) -> dict[str, object]:
        apply_calls.append((a, b, c))
        return {"created_count": 99}

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ImportBatchDetailScreen(
            loader=_sample_detail,
            on_toggle_exclude=_noop_exclude,
            on_promote=_noop_promote,
            on_apply=on_apply,
        )
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("escape")  # cancel
        await pilot.pause()

    assert apply_calls == []
    assert "cancel" in screen.notice().lower()


@pytest.mark.asyncio
async def test_apply_modal_confirm_fires_action() -> None:
    apply_calls: list[tuple[bool, bool, bool]] = []

    def on_apply(a: bool, b: bool, c: bool) -> dict[str, object]:
        apply_calls.append((a, b, c))
        return {"created_count": 5}

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ImportBatchDetailScreen(
            loader=_sample_detail,
            on_toggle_exclude=_noop_exclude,
            on_promote=_noop_promote,
            on_apply=on_apply,
        )
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        modal = next(s for s in app.screen_stack if isinstance(s, ApplyConfirmModal))
        # Dismiss programmatically with all defaults (all False).
        modal.dismiss(modal.snapshot_flags())
        await pilot.pause()

    assert apply_calls == [(False, False, False)]


@pytest.mark.asyncio
async def test_apply_blocked_when_nothing_pending() -> None:
    # Detail with every line already promoted.
    detail = ImportBatchDetail(
        id="batch-2",
        account_id="acc-1",
        source_format="ofx",
        source_filename="x.qfx",
        status="parsed",
        imported_count=1,
        skipped_count=0,
        error_count=0,
        created_at="2026-05-01T12:00:00Z",
        applied_at=None,
        reverted_at=None,
        lines=(
            StatementLineSummary(
                id="line-x",
                line_number=1,
                date="2026-05-01",
                description="X",
                amount_display="1.00",
                currency="USD",
                is_excluded=False,
                promoted_transaction_id="tx-1",
                reconciliation_match_id=None,
            ),
        ),
    )
    apply_calls: list[object] = []

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ImportBatchDetailScreen(
            loader=lambda: detail,
            on_toggle_exclude=_noop_exclude,
            on_promote=_noop_promote,
            on_apply=lambda *_args: apply_calls.append(_args) or {},
        )
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()

    modals = [s for s in app.screen_stack if isinstance(s, ApplyConfirmModal)]
    assert modals == []
    assert "nothing to apply" in screen.notice().lower()


@pytest.mark.asyncio
async def test_imports_screen_enter_drills_into_detail() -> None:
    drilled: list[str] = []

    imports = ImportsData(
        batches=(
            ImportBatchSummary(
                id="batch-1",
                account_id="acc-1",
                source_format="ofx",
                source_filename="april.qfx",
                status="parsed",
                imported_count=1,
                skipped_count=0,
                error_count=0,
                created_at="2026-05-01T12:00:00Z",
                applied_at=None,
                reverted_at=None,
            ),
        ),
    )

    app = TulipTuiApp(
        loader=_accounts_loader,
        imports_loader=lambda: imports,
        import_batch_detail_factory=lambda batch_id: lambda: _sample_detail(),
        line_exclude_action=lambda *_args: None,
        line_promote_action=lambda *_args: None,
        batch_apply_action=lambda *_args: {},
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("i")  # open imports screen
        await pilot.pause()
        # Patch the imports-screen handler to capture the batch id.
        from tulip_tui.screens.imports import ImportsScreen

        imports_screen = next(s for s in app.screen_stack if isinstance(s, ImportsScreen))
        original = imports_screen._on_open_batch  # type: ignore[attr-defined]

        def _capture(batch_id: str) -> None:
            drilled.append(batch_id)
            original(batch_id)

        imports_screen._on_open_batch = _capture  # type: ignore[attr-defined]
        await pilot.press("enter")
        await pilot.pause()
        # Detail screen should now be on top of the stack — check inside the
        # async-test context so the app is still live.
        detail_present = any(isinstance(s, ImportBatchDetailScreen) for s in app.screen_stack)

    assert drilled == ["batch-1"]
    assert detail_present

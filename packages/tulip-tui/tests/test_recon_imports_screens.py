"""Pilot-mode tests for ``ReconciliationsScreen`` and ``ImportsScreen``.

Both screens are list + cursor-follows detail. Tests inject a loader
so no API call is involved.
"""

from __future__ import annotations

import pytest

from tulip_tui.app import TulipTuiApp
from tulip_tui.data.accounts import AccountsData
from tulip_tui.data.imports import ImportBatchSummary, ImportsData
from tulip_tui.data.reconciliations import (
    ReconciliationsData,
    ReconciliationSummary,
)
from tulip_tui.screens.imports import ImportsScreen
from tulip_tui.screens.reconciliations import ReconciliationsScreen


def _accounts_loader() -> AccountsData:
    return AccountsData(as_of="2026-05-17", accounts=(), groups=())


# ---- reconciliations ------------------------------------------------


def _sample_recons() -> ReconciliationsData:
    return ReconciliationsData(
        reconciliations=(
            ReconciliationSummary(
                id="rec-1",
                account_id="acc-1",
                statement_period_start="2026-04-01",
                statement_period_end="2026-04-30",
                statement_starting_balance="1000.00",
                statement_ending_balance="1234.56",
                currency="USD",
                status="complete",
                source_import_batch_id=None,
                created_at="2026-05-01T12:00:00Z",
                completed_at="2026-05-02T09:30:00Z",
            ),
            ReconciliationSummary(
                id="rec-2",
                account_id="acc-2",
                statement_period_start="2026-05-01",
                statement_period_end="2026-05-31",
                statement_starting_balance="500.00",
                statement_ending_balance="725.10",
                currency="USD",
                status="open",
                source_import_batch_id="batch-9",
                created_at="2026-05-15T08:00:00Z",
                completed_at=None,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_reconciliations_screen_lists_envelopes() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationsScreen(loader=lambda: _sample_recons())
        await app.push_screen(screen)
        await pilot.pause()
        rendered = screen.rendered_rows()

    assert len(rendered) == 2
    assert any("complete" in row and "1,234.56" in row for row in rendered)
    assert any("open" in row and "725.10" in row for row in rendered)


@pytest.mark.asyncio
async def test_reconciliations_screen_detail_follows_cursor() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationsScreen(loader=lambda: _sample_recons())
        await app.push_screen(screen)
        await pilot.pause()
        first = screen.detail_text()
        assert "rec-1" in first
        assert "complete" in first
        await pilot.press("down")
        await pilot.pause()
        second = screen.detail_text()
        assert "rec-2" in second
        assert "open" in second
        assert "batch-9" in second  # source_import_batch_id surfaces in detail


@pytest.mark.asyncio
async def test_reconciliations_screen_empty_state() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationsScreen(loader=lambda: ReconciliationsData(reconciliations=()))
        await app.push_screen(screen)
        await pilot.pause()
        assert "no reconciliations" in screen.detail_text().lower()


@pytest.mark.asyncio
async def test_reconciliations_screen_renders_error_inline() -> None:
    def boom() -> ReconciliationsData:
        raise RuntimeError("api down")

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReconciliationsScreen(loader=boom)
        await app.push_screen(screen)
        await pilot.pause()
        assert "api down" in screen.detail_text()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("q")
    assert app.return_code == 0


# ---- imports --------------------------------------------------------


def _sample_imports() -> ImportsData:
    return ImportsData(
        batches=(
            ImportBatchSummary(
                id="batch-1",
                account_id="acc-1",
                source_format="ofx",
                source_filename="april.qfx",
                status="applied",
                imported_count=42,
                skipped_count=0,
                error_count=0,
                created_at="2026-05-01T12:00:00Z",
                applied_at="2026-05-01T12:05:00Z",
                reverted_at=None,
            ),
            ImportBatchSummary(
                id="batch-2",
                account_id="acc-2",
                source_format="csv",
                source_filename="visa.csv",
                status="parsed",
                imported_count=0,
                skipped_count=0,
                error_count=0,
                created_at="2026-05-10T09:00:00Z",
                applied_at=None,
                reverted_at=None,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_imports_screen_lists_batches() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ImportsScreen(loader=lambda: _sample_imports())
        await app.push_screen(screen)
        await pilot.pause()
        rendered = screen.rendered_rows()

    assert len(rendered) == 2
    assert any("ofx" in row and "april.qfx" in row and "applied" in row for row in rendered)
    assert any("csv" in row and "visa.csv" in row and "parsed" in row for row in rendered)


@pytest.mark.asyncio
async def test_imports_screen_detail_follows_cursor() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ImportsScreen(loader=lambda: _sample_imports())
        await app.push_screen(screen)
        await pilot.pause()
        first = screen.detail_text()
        assert "batch-1" in first
        assert "april.qfx" in first
        assert "42" in first  # imported_count
        await pilot.press("down")
        await pilot.pause()
        second = screen.detail_text()
        assert "batch-2" in second
        assert "visa.csv" in second
        assert "parsed" in second


@pytest.mark.asyncio
async def test_imports_screen_empty_state() -> None:
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ImportsScreen(loader=lambda: ImportsData(batches=()))
        await app.push_screen(screen)
        await pilot.pause()
        assert "no import batches" in screen.detail_text().lower()


@pytest.mark.asyncio
async def test_imports_screen_error_inline() -> None:
    def boom() -> ImportsData:
        raise RuntimeError("api blip")

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ImportsScreen(loader=boom)
        await app.push_screen(screen)
        await pilot.pause()
        assert "api blip" in screen.detail_text()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("q")
    assert app.return_code == 0


# ---- app bindings ---------------------------------------------------


@pytest.mark.asyncio
async def test_app_binding_c_pushes_reconciliations_screen() -> None:
    app = TulipTuiApp(
        loader=_accounts_loader,
        reconciliations_loader=lambda: _sample_recons(),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, ReconciliationsScreen)


@pytest.mark.asyncio
async def test_app_binding_i_pushes_imports_screen() -> None:
    app = TulipTuiApp(
        loader=_accounts_loader,
        imports_loader=lambda: _sample_imports(),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        assert isinstance(app.screen, ImportsScreen)

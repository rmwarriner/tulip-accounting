"""Pilot-mode tests for ``ReportsScreen``.

The reports screen pairs a left-hand menu (one row per catalogued
report) with a right-hand content pane that fills with the chosen
report's body. Tests inject a synchronous ``loader`` so no API call
is involved.
"""

from __future__ import annotations

import pytest

from tulip_tui.app import TulipTuiApp
from tulip_tui.data.accounts import AccountsData
from tulip_tui.data.reports import REPORT_CATALOGUE, ReportPayload, ReportSpec
from tulip_tui.screens.reports import ReportsScreen


def _accounts_loader() -> AccountsData:
    return AccountsData(as_of="2026-05-17", accounts=(), groups=())


def _fake_payload(spec: ReportSpec) -> ReportPayload:
    return ReportPayload(
        spec=spec,
        body={
            "as_of": "2026-05-17",
            "rows": [
                {"code": "assets:checking", "name": "Checking", "balance": "100.00"},
                {"code": "expenses:groceries", "name": "Groceries", "balance": "25.00"},
            ],
        },
    )


@pytest.mark.asyncio
async def test_reports_screen_lists_all_catalogue_entries() -> None:
    """Every catalogued report appears as a row in the left-hand menu."""
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReportsScreen(loader=_fake_payload)
        await app.push_screen(screen)
        await pilot.pause()
        menu_text = " ".join(screen.menu_rows())

    for spec in REPORT_CATALOGUE:
        assert spec.title in menu_text


@pytest.mark.asyncio
async def test_reports_screen_loads_first_report_by_default() -> None:
    """The first row is highlighted on mount and its body fills the pane."""
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReportsScreen(loader=_fake_payload)
        await app.push_screen(screen)
        await pilot.pause()
        content = screen.content_text()

    # The fake payload's rendering should mention a row from the table.
    assert "Checking" in content
    assert "100.00" in content


@pytest.mark.asyncio
async def test_reports_screen_swap_on_cursor_move() -> None:
    """Moving the cursor fetches and renders the new report."""
    seen: list[str] = []

    def loader(spec: ReportSpec) -> ReportPayload:
        seen.append(spec.key)
        return ReportPayload(
            spec=spec,
            body={"title": f"render of {spec.key}", "rows": []},
        )

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReportsScreen(loader=loader)
        await app.push_screen(screen)
        await pilot.pause()
        # First report should already be loaded.
        assert seen[0] == REPORT_CATALOGUE[0].key
        # Move down → second report loads.
        await pilot.press("down")
        await pilot.pause()
        assert seen[-1] == REPORT_CATALOGUE[1].key
        content = screen.content_text()
        assert "render of " + REPORT_CATALOGUE[1].key in content


@pytest.mark.asyncio
async def test_reports_screen_renders_error_inline() -> None:
    """A loader exception surfaces in the content pane; ``escape`` still works."""

    def boom(_spec: ReportSpec) -> ReportPayload:
        raise RuntimeError("api down")

    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReportsScreen(loader=boom)
        await app.push_screen(screen)
        await pilot.pause()
        assert "api down" in screen.content_text()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("q")
    assert app.return_code == 0


@pytest.mark.asyncio
async def test_reports_screen_handles_non_row_body() -> None:
    """A report without a top-level ``rows`` list is rendered as key/value lines."""
    spec = REPORT_CATALOGUE[0]
    payload = ReportPayload(
        spec=spec,
        body={"summary": "ok", "as_of": "2026-05-17", "totals": {"USD": "0.00"}},
    )
    app = TulipTuiApp(loader=_accounts_loader)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ReportsScreen(loader=lambda _s: payload)
        await app.push_screen(screen)
        await pilot.pause()
        content = screen.content_text()

    assert "summary" in content
    assert "ok" in content
    assert "as_of" in content
    assert "2026-05-17" in content

"""Pilot-mode tests for ``AICategorizeProposalModal`` (#425)."""

from __future__ import annotations

import pytest

from tulip_tui.app import TulipTuiApp
from tulip_tui.data.accounts import AccountsData
from tulip_tui.data.ai_categorize import AIProposalCandidate
from tulip_tui.screens.ai_categorize_modal import AICategorizeProposalModal


def _accounts_loader_empty() -> AccountsData:
    return AccountsData(as_of="2026-05-20", accounts=(), groups=())


def _sample_candidates() -> tuple[AIProposalCandidate, ...]:
    return (
        AIProposalCandidate(account_code="5100", confidence=0.85, reasoning="grocery"),
        AIProposalCandidate(account_code="5300", confidence=0.55, reasoning=None),
        AIProposalCandidate(account_code="5400", confidence=0.30, reasoning="dining-ish"),
    )


@pytest.mark.asyncio
async def test_modal_renders_top_candidate_and_alternates() -> None:
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = AICategorizeProposalModal(
            description="WHOLE FOODS MARKET", candidates=_sample_candidates()
        )
        await app.push_screen(modal)
        await pilot.pause()
        snapshot = modal.snapshot_candidates()
    assert len(snapshot) == 3
    assert snapshot[0].account_code == "5100"


@pytest.mark.asyncio
async def test_modal_accept_dismisses_with_top_code() -> None:
    """``a`` accepts the focused row — cursor starts at row 0 (the top)."""
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        accepted: list[str | None] = []

        def callback(result: object) -> None:
            accepted.append(result)  # type: ignore[arg-type]

        modal = AICategorizeProposalModal(description="X", candidates=_sample_candidates())
        await app.push_screen(modal, callback)
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
    assert accepted == ["5100"]


@pytest.mark.asyncio
async def test_modal_arrow_then_accept_picks_alternate() -> None:
    """Down-arrow + ``a`` picks the second-row candidate."""
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        accepted: list[str | None] = []

        def callback(result: object) -> None:
            accepted.append(result)  # type: ignore[arg-type]

        modal = AICategorizeProposalModal(description="X", candidates=_sample_candidates())
        await app.push_screen(modal, callback)
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
    assert accepted == ["5300"]


@pytest.mark.asyncio
async def test_modal_escape_dismisses_with_none() -> None:
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        accepted: list[object] = []

        def callback(result: object) -> None:
            accepted.append(result)

        modal = AICategorizeProposalModal(description="X", candidates=_sample_candidates())
        await app.push_screen(modal, callback)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert accepted == [None]


@pytest.mark.asyncio
async def test_modal_empty_candidates_renders_no_proposal_message() -> None:
    """Empty candidate tuple → modal still renders + escape dismisses cleanly."""
    app = TulipTuiApp(loader=_accounts_loader_empty)
    async with app.run_test() as pilot:
        await pilot.pause()
        accepted: list[object] = []

        def callback(result: object) -> None:
            accepted.append(result)

        modal = AICategorizeProposalModal(description="X", candidates=())
        await app.push_screen(modal, callback)
        await pilot.pause()
        # `a` on empty also dismisses with None — no candidate to accept.
        await pilot.press("a")
        await pilot.pause()
    assert accepted == [None]

"""Tests for ``AIProposalCapability`` (P6.4.b)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tulip_ai.adapters import RecordingAdapter
from tulip_ai.proposals import AIProposalCapability
from tulip_storage.encryption import encrypt_field, field_aad
from tulip_storage.models import AIInvocation, Household


def _series(days: int = 30, amount: str = "5.00") -> list[tuple[date, Decimal]]:
    return [(date(2026, 1, 1) + timedelta(days=i), Decimal(amount)) for i in range(days)]


def _set_policy(
    session_maker: sessionmaker[Session],
    household: Household,
    *,
    master_key: bytes,
    policy_extra: dict[str, object] | None = None,
    set_key: bool = True,
) -> None:
    if set_key:
        blob = encrypt_field(
            json.dumps({"anthropic": "sk-test"}).encode("utf-8"),
            master_key=master_key,
            aad=field_aad(
                table="households",
                column="ai_keys_encrypted",
                household_id=household.id,
                row_id=household.id,
            ),
        )
    else:
        blob = None
    policy: dict[str, object] = {
        "default_provider": "anthropic",
        "default_model": "claude-opus-4-7",
    }
    if policy_extra:
        policy.update(policy_extra)
    with session_maker() as s:
        h = s.get(Household, household.id)
        assert h is not None
        h.ai_keys_encrypted = blob
        h.ai_policy = policy
        s.commit()


@pytest.mark.asyncio
async def test_suggest_budget_happy_path_returns_proposed_change(
    session_maker: sessionmaker[Session],
    household_and_user: tuple[Household, object],
    master_key: bytes,
) -> None:
    household, _ = household_and_user
    _set_policy(session_maker, household, master_key=master_key)
    adapter = RecordingAdapter(
        canned_reply=(
            '{"new_budget_amount": "250.00", '
            '"rationale": "Spending has trended up; 250 covers the new normal."}'
        )
    )
    cap = AIProposalCapability(session_maker=session_maker, adapter=adapter)
    result = await cap.suggest_envelope_budget(
        household_id=household.id,
        actor_user_id=None,
        api_key="sk-test",
        envelope_id=household.id,  # any UUID works for the prompt
        envelope_name="Groceries",
        currency="USD",
        current_budget=Decimal("200"),
        recent_spend_series=_series(),
    )
    assert result.error is None
    assert result.proposal is not None
    assert result.proposal.kind == "envelope_budget_update"
    assert result.proposal.payload["new_budget_amount"] == "250.00"
    assert "Spending has trended up" in result.proposal.rationale
    assert result.proposal.ai_invocation_id is not None

    with session_maker() as s:
        rows = (
            s.execute(select(AIInvocation).where(AIInvocation.capability == "agentic"))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].outcome == "success"


@pytest.mark.asyncio
async def test_suggest_budget_no_key_returns_error(
    session_maker: sessionmaker[Session],
    household_and_user: tuple[Household, object],
    master_key: bytes,
) -> None:
    household, _ = household_and_user
    _set_policy(session_maker, household, master_key=master_key, set_key=False)
    adapter = RecordingAdapter(canned_reply="unused")
    cap = AIProposalCapability(session_maker=session_maker, adapter=adapter)
    result = await cap.suggest_envelope_budget(
        household_id=household.id,
        actor_user_id=None,
        api_key=None,
        envelope_id=household.id,
        envelope_name="Groceries",
        currency="USD",
        current_budget=None,
        recent_spend_series=_series(),
    )
    assert result.proposal is None
    assert "no api key" in (result.error or "")
    assert adapter.calls == []
    with session_maker() as s:
        rows = (
            s.execute(select(AIInvocation).where(AIInvocation.capability == "agentic"))
            .scalars()
            .all()
        )
        assert rows[0].outcome == "provider_error"


@pytest.mark.asyncio
async def test_suggest_budget_unparseable_response_records_error(
    session_maker: sessionmaker[Session],
    household_and_user: tuple[Household, object],
    master_key: bytes,
) -> None:
    household, _ = household_and_user
    _set_policy(session_maker, household, master_key=master_key)
    adapter = RecordingAdapter(canned_reply="not JSON at all!")
    cap = AIProposalCapability(session_maker=session_maker, adapter=adapter)
    result = await cap.suggest_envelope_budget(
        household_id=household.id,
        actor_user_id=None,
        api_key="sk-test",
        envelope_id=household.id,
        envelope_name="Groceries",
        currency="USD",
        current_budget=None,
        recent_spend_series=_series(),
    )
    assert result.proposal is None
    assert "unparseable" in (result.error or "")
    with session_maker() as s:
        rows = (
            s.execute(select(AIInvocation).where(AIInvocation.capability == "agentic"))
            .scalars()
            .all()
        )
        assert rows[0].outcome == "provider_error"


@pytest.mark.asyncio
async def test_suggest_budget_disabled_policy(
    session_maker: sessionmaker[Session],
    household_and_user: tuple[Household, object],
    master_key: bytes,
) -> None:
    household, _ = household_and_user
    _set_policy(
        session_maker,
        household,
        master_key=master_key,
        policy_extra={"capabilities": {"agentic": {"policy": "disabled"}}},
    )
    adapter = RecordingAdapter(canned_reply="unused")
    cap = AIProposalCapability(session_maker=session_maker, adapter=adapter)
    result = await cap.suggest_envelope_budget(
        household_id=household.id,
        actor_user_id=None,
        api_key="sk-test",
        envelope_id=household.id,
        envelope_name="Groceries",
        currency="USD",
        current_budget=None,
        recent_spend_series=_series(),
    )
    assert result.proposal is None
    assert result.error == "agentic disabled"
    assert adapter.calls == []
    with session_maker() as s:
        rows = (
            s.execute(select(AIInvocation).where(AIInvocation.capability == "agentic"))
            .scalars()
            .all()
        )
        assert rows[0].outcome == "policy_disabled"


@pytest.mark.asyncio
async def test_suggest_budget_handles_codefence_response(
    session_maker: sessionmaker[Session],
    household_and_user: tuple[Household, object],
    master_key: bytes,
) -> None:
    household, _ = household_and_user
    _set_policy(session_maker, household, master_key=master_key)
    adapter = RecordingAdapter(
        canned_reply='```json\n{"new_budget_amount": "175.50", "rationale": "ok"}\n```'
    )
    cap = AIProposalCapability(session_maker=session_maker, adapter=adapter)
    result = await cap.suggest_envelope_budget(
        household_id=household.id,
        actor_user_id=None,
        api_key="sk-test",
        envelope_id=household.id,
        envelope_name="Groceries",
        currency="USD",
        current_budget=None,
        recent_spend_series=_series(),
    )
    assert result.proposal is not None
    assert result.proposal.payload["new_budget_amount"] == "175.50"

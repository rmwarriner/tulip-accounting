"""Tests for ``AIForecastCapability`` + bucketing (P6.3.b, ADR-0005 §Q3)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tulip_ai.adapters import RecordingAdapter
from tulip_ai.forecast import (
    AIForecastCapability,
    bucket_time_series,
    build_forecast_prompt,
)
from tulip_storage.encryption import encrypt_field
from tulip_storage.models import AIInvocation, Household


class TestBucketing:
    def test_default_buckets_to_5_pct_of_max(self) -> None:
        series = [(date(2026, 1, i + 1), Decimal(str(v))) for i, v in enumerate([100, 5, 17, 99])]
        bucketed = bucket_time_series(series, profile="default")
        # max abs = 100 → bucket size = 5.
        # 100 -> 100, 5 -> 5, 17 -> 15 (nearest 5), 99 -> 100.
        amounts = [amt for _, amt in bucketed]
        assert amounts == [Decimal("100"), Decimal("5"), Decimal("15"), Decimal("100")]

    def test_strict_buckets_to_25_pct(self) -> None:
        series = [(date(2026, 1, i + 1), Decimal(str(v))) for i, v in enumerate([100, 30, 75])]
        bucketed = bucket_time_series(series, profile="strict")
        # bucket size = 25. 30 -> 25; 75 -> 75; 100 -> 100.
        amounts = [amt for _, amt in bucketed]
        assert amounts == [Decimal("100"), Decimal("25"), Decimal("75")]

    def test_local_only_passes_through(self) -> None:
        series = [
            (date(2026, 1, 1), Decimal("12.34")),
            (date(2026, 1, 2), Decimal("56.78")),
        ]
        assert bucket_time_series(series, profile="local_only") == series

    def test_empty_series_returns_empty(self) -> None:
        assert bucket_time_series([], profile="default") == []

    def test_all_zeros_passes_through(self) -> None:
        series = [
            (date(2026, 1, 1), Decimal("0")),
            (date(2026, 1, 2), Decimal("0")),
        ]
        assert bucket_time_series(series, profile="default") == series


class TestBuildPrompt:
    def test_strict_elides_envelope_name(self) -> None:
        payload = build_forecast_prompt(
            envelope_id="env-1",
            envelope_name="Groceries",
            currency="USD",
            time_series=[(date(2026, 1, 1), Decimal("10"))],
            target_amount=None,
            target_date=None,
            recent_inflow_average=None,
            profile="strict",
        )
        body = payload.to_dict()
        assert "envelope_name" not in body
        assert body["envelope_id"] == "env-1"

    def test_default_includes_envelope_name(self) -> None:
        payload = build_forecast_prompt(
            envelope_id="env-1",
            envelope_name="Groceries",
            currency="USD",
            time_series=[(date(2026, 1, 1), Decimal("10"))],
            target_amount=None,
            target_date=None,
            recent_inflow_average=None,
            profile="default",
        )
        assert payload.to_dict()["envelope_name"] == "Groceries"

    def test_target_fields_threaded_through(self) -> None:
        payload = build_forecast_prompt(
            envelope_id="env-1",
            envelope_name="Vacation",
            currency="USD",
            time_series=[(date(2026, 1, 1), Decimal("100"))],
            target_amount=Decimal("3000"),
            target_date=date(2026, 12, 31),
            recent_inflow_average=Decimal("250"),
            profile="default",
        )
        body = payload.to_dict()
        assert body["target_amount"] == "3000"
        assert body["target_date"] == "2026-12-31"
        assert body["recent_inflow_average"] == "250"


# ---- AIForecastCapability integration ---------------------------------


def _series(days: int = 30) -> list[tuple[date, Decimal]]:
    return [(date(2026, 1, 1) + timedelta(days=i), Decimal("10")) for i in range(days)]


def _set_keys_and_policy(
    session_maker: sessionmaker[Session],
    household: Household,
    *,
    master_key: bytes,
    ai_policy: dict[str, object] | None = None,
) -> None:
    blob = encrypt_field(
        json.dumps({"anthropic": "sk-test"}).encode("utf-8"), master_key=master_key
    )
    with session_maker() as s:
        h = s.get(Household, household.id)
        assert h is not None
        h.ai_keys_encrypted = blob
        h.ai_policy = ai_policy or {
            "default_provider": "anthropic",
            "default_model": "claude-opus-4-7",
        }
        s.commit()


@pytest.mark.asyncio
async def test_forecast_happy_path_records_success_invocation(
    session_maker: sessionmaker[Session],
    household_and_user: tuple[Household, object],
    master_key: bytes,
) -> None:
    household, _ = household_and_user
    _set_keys_and_policy(session_maker, household, master_key=master_key)
    adapter = RecordingAdapter(canned_reply="Groceries on track to run out around 2026-05-25.")
    cap = AIForecastCapability(session_maker=session_maker, adapter=adapter)
    result = await cap.forecast(
        household_id=household.id,
        actor_user_id=None,
        api_key="sk-test",
        envelope_id=household.id,  # any UUID will do for the prompt
        envelope_name="Groceries",
        currency="USD",
        time_series=_series(),
    )
    assert result.error is None
    assert "2026-05-25" in result.text
    assert len(adapter.calls) == 1
    with session_maker() as s:
        rows = (
            s.execute(select(AIInvocation).where(AIInvocation.capability == "forecast"))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].outcome == "success"


@pytest.mark.asyncio
async def test_forecast_returns_error_without_api_key(
    session_maker: sessionmaker[Session],
    household_and_user: tuple[Household, object],
    master_key: bytes,
) -> None:
    household, _ = household_and_user
    # ai_policy with no keys → resolver picks anthropic; no api_key → error path.
    with session_maker() as s:
        h = s.get(Household, household.id)
        assert h is not None
        h.ai_policy = {"default_provider": "anthropic", "default_model": "claude-opus-4-7"}
        s.commit()
    adapter = RecordingAdapter(canned_reply="unused")
    cap = AIForecastCapability(session_maker=session_maker, adapter=adapter)
    result = await cap.forecast(
        household_id=household.id,
        actor_user_id=None,
        api_key=None,
        envelope_id=household.id,
        envelope_name="Groceries",
        currency="USD",
        time_series=_series(),
    )
    assert result.text == ""
    assert "no api key" in (result.error or "")
    assert adapter.calls == []
    with session_maker() as s:
        rows = (
            s.execute(select(AIInvocation).where(AIInvocation.capability == "forecast"))
            .scalars()
            .all()
        )
        assert rows[0].outcome == "provider_error"


@pytest.mark.asyncio
async def test_forecast_disabled_policy_no_provider_call(
    session_maker: sessionmaker[Session],
    household_and_user: tuple[Household, object],
    master_key: bytes,
) -> None:
    household, _ = household_and_user
    _set_keys_and_policy(
        session_maker,
        household,
        master_key=master_key,
        ai_policy={
            "default_provider": "anthropic",
            "default_model": "claude-opus-4-7",
            "capabilities": {"forecast": {"policy": "disabled"}},
        },
    )
    adapter = RecordingAdapter(canned_reply="unused")
    cap = AIForecastCapability(session_maker=session_maker, adapter=adapter)
    result = await cap.forecast(
        household_id=household.id,
        actor_user_id=None,
        api_key="sk-test",
        envelope_id=household.id,
        envelope_name="Groceries",
        currency="USD",
        time_series=_series(),
    )
    assert result.error == "forecast disabled"
    assert adapter.calls == []
    with session_maker() as s:
        rows = (
            s.execute(select(AIInvocation).where(AIInvocation.capability == "forecast"))
            .scalars()
            .all()
        )
        assert rows[0].outcome == "policy_disabled"

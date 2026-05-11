"""Integration tests for ``AICategorizer`` (P6.1).

Each test seeds a household + chart of accounts in the test DB, drives
``AICategorizer.categorize()`` against a ``RecordingAdapter``, and
inspects both the returned ``CategorizationResult`` and the resulting
``ai_invocations`` row.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tulip_ai.adapters import RecordingAdapter
from tulip_ai.categorize import AICategorizer, build_categorize_prompt
from tulip_ai.redaction import ChartEntry
from tulip_core.money import Money
from tulip_core.reconciliation.categorizer import HouseholdContext
from tulip_core.reconciliation.statement_line import StatementLine
from tulip_storage.encryption import encrypt_field
from tulip_storage.models import (
    Account,
    AccountType,
    AIInvocation,
    Household,
)


def _seed_chart(
    session_maker: sessionmaker[Session], household_id, *, codes: list[tuple[str, str, AccountType]]
) -> None:
    with session_maker() as s:
        for code, name, ty in codes:
            s.add(
                Account(
                    household_id=household_id,
                    id=uuid4(),
                    code=code,
                    name=name,
                    type=ty,
                    currency="USD",
                    visibility="shared",
                    is_active=True,
                )
            )
        s.commit()


def _set_ai_keys(
    session_maker: sessionmaker[Session],
    household_id,
    *,
    keys: dict[str, str],
    master_key: bytes,
    provider: str = "anthropic",
    model: str = "claude-opus-4-7",
) -> None:
    blob = encrypt_field(json.dumps(keys).encode("utf-8"), master_key=master_key)
    with session_maker() as s:
        h = s.get(Household, household_id)
        assert h is not None
        h.ai_keys_encrypted = blob
        h.ai_policy = {"default_provider": provider, "default_model": model}
        s.commit()


def _make_statement_line(*, description: str, amount: str) -> StatementLine:
    return StatementLine(
        id=uuid4(),
        import_batch_id=uuid4(),
        line_number=1,
        posted_date=date(2026, 5, 3),
        amount=Money(Decimal(amount), "USD"),
        description=description,
    )


@pytest.mark.asyncio
async def test_categorize_happy_path_returns_model_choice(
    session_maker: sessionmaker[Session],
    household_and_user,
    master_key: bytes,
) -> None:
    household, _ = household_and_user
    _seed_chart(
        session_maker,
        household.id,
        codes=[
            ("5100", "Groceries", AccountType.EXPENSE),
            ("5300", "Fuel", AccountType.EXPENSE),
        ],
    )
    _set_ai_keys(session_maker, household.id, keys={"anthropic": "sk-test"}, master_key=master_key)

    adapter = RecordingAdapter(canned_reply='{"account_code": "5100", "confidence": 0.92}')
    categorizer = AICategorizer(session_maker=session_maker, master_key=master_key, adapter=adapter)
    result = await categorizer.categorize(
        _make_statement_line(description="WHOLE FOODS MARKET", amount="-87.42"),
        HouseholdContext(household_id=household.id, account_whitelist=frozenset()),
    )
    assert result.account_code == "5100"
    assert result.confidence == pytest.approx(0.92)
    # Adapter was called with the household's key.
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["api_key_was_passed"] is True
    # Audit row written with outcome=success.
    with session_maker() as s:
        rows = s.execute(select(AIInvocation)).scalars().all()
        assert len(rows) == 1
        assert rows[0].outcome == "success"
        assert rows[0].provider == "anthropic"
        assert rows[0].capability == "categorize"


@pytest.mark.asyncio
async def test_categorize_falls_back_when_policy_disabled(
    session_maker: sessionmaker[Session], household_and_user, master_key: bytes
) -> None:
    household, _ = household_and_user
    _seed_chart(
        session_maker,
        household.id,
        codes=[("5100", "Groceries", AccountType.EXPENSE)],
    )
    with session_maker() as s:
        h = s.get(Household, household.id)
        assert h is not None
        h.ai_policy = {
            "capabilities": {"categorize": {"policy": "disabled"}},
        }
        s.commit()

    adapter = RecordingAdapter(canned_reply='{"account_code": "5100"}')
    categorizer = AICategorizer(session_maker=session_maker, master_key=master_key, adapter=adapter)
    result = await categorizer.categorize(
        _make_statement_line(description="WHOLE FOODS", amount="-12.00"),
        HouseholdContext(household_id=household.id, account_whitelist=frozenset()),
    )
    assert result.account_code == "Imbalance:Unknown"
    # No adapter call when policy is disabled.
    assert adapter.calls == []
    # Audit row records the policy_disabled outcome.
    with session_maker() as s:
        rows = s.execute(select(AIInvocation)).scalars().all()
        assert len(rows) == 1
        assert rows[0].outcome == "policy_disabled"


@pytest.mark.asyncio
async def test_categorize_falls_back_when_no_api_key(
    session_maker: sessionmaker[Session], household_and_user, master_key: bytes
) -> None:
    household, _ = household_and_user
    _seed_chart(
        session_maker,
        household.id,
        codes=[("5100", "Groceries", AccountType.EXPENSE)],
    )
    # Household has policy but no key — non-ollama provider must have one.
    with session_maker() as s:
        h = s.get(Household, household.id)
        assert h is not None
        h.ai_policy = {"default_provider": "anthropic", "default_model": "claude-opus-4-7"}
        s.commit()

    adapter = RecordingAdapter(canned_reply='{"account_code": "5100"}')
    categorizer = AICategorizer(session_maker=session_maker, master_key=master_key, adapter=adapter)
    result = await categorizer.categorize(
        _make_statement_line(description="WHOLE FOODS", amount="-12.00"),
        HouseholdContext(household_id=household.id, account_whitelist=frozenset()),
    )
    assert result.account_code == "Imbalance:Unknown"
    assert adapter.calls == []
    with session_maker() as s:
        rows = s.execute(select(AIInvocation)).scalars().all()
        assert rows[0].outcome == "provider_error"
        assert rows[0].response_text == "no api key configured for provider"


@pytest.mark.asyncio
async def test_categorize_rejects_account_code_not_in_chart(
    session_maker: sessionmaker[Session], household_and_user, master_key: bytes
) -> None:
    """If the model hallucinates a code, fall back rather than propose it."""
    household, _ = household_and_user
    _seed_chart(session_maker, household.id, codes=[("5100", "Groceries", AccountType.EXPENSE)])
    _set_ai_keys(session_maker, household.id, keys={"anthropic": "sk-test"}, master_key=master_key)

    adapter = RecordingAdapter(
        canned_reply='{"account_code": "9999-not-in-chart", "confidence": 1.0}'
    )
    categorizer = AICategorizer(session_maker=session_maker, master_key=master_key, adapter=adapter)
    result = await categorizer.categorize(
        _make_statement_line(description="WHOLE FOODS", amount="-12.00"),
        HouseholdContext(household_id=household.id, account_whitelist=frozenset()),
    )
    assert result.account_code == "Imbalance:Unknown"
    # Audit row still says success — the call landed; the response just wasn't useful.
    with session_maker() as s:
        rows = s.execute(select(AIInvocation)).scalars().all()
        assert rows[0].outcome == "success"


@pytest.mark.asyncio
async def test_categorize_handles_response_wrapped_in_codefence(
    session_maker: sessionmaker[Session], household_and_user, master_key: bytes
) -> None:
    household, _ = household_and_user
    _seed_chart(session_maker, household.id, codes=[("5100", "Groceries", AccountType.EXPENSE)])
    _set_ai_keys(session_maker, household.id, keys={"anthropic": "sk-test"}, master_key=master_key)

    adapter = RecordingAdapter(
        canned_reply='```json\n{"account_code": "5100", "confidence": 0.8}\n```'
    )
    categorizer = AICategorizer(session_maker=session_maker, master_key=master_key, adapter=adapter)
    result = await categorizer.categorize(
        _make_statement_line(description="WHOLE FOODS", amount="-12.00"),
        HouseholdContext(household_id=household.id, account_whitelist=frozenset()),
    )
    assert result.account_code == "5100"


def test_build_categorize_prompt_is_pure() -> None:
    """Same inputs → same output. The preview surface depends on this."""
    line = _make_statement_line(description="WHOLE FOODS", amount="-87.42")
    chart = (ChartEntry(code="5100", name="Groceries", type="expense"),)
    a = build_categorize_prompt(line, chart)
    b = build_categorize_prompt(line, chart)
    assert a.to_dict() == b.to_dict()

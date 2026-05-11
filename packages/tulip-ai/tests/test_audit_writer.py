"""Tests for ``AIInvocationWriter`` (ADR-0005 §Q6)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from tulip_ai.audit import AIInvocationRecord, AIInvocationWriter, hash_prompt_payload
from tulip_storage.models import AIInvocation, Household


def test_hash_prompt_payload_is_deterministic() -> None:
    a = {"task": "categorize", "line": {"amount": "-87.42"}}
    b = {"line": {"amount": "-87.42"}, "task": "categorize"}
    assert hash_prompt_payload(a) == hash_prompt_payload(b)


def test_hash_prompt_payload_differs_for_different_input() -> None:
    assert hash_prompt_payload({"x": 1}) != hash_prompt_payload({"x": 2})


def test_writer_inserts_row_with_provided_fields(
    session_maker: sessionmaker[Session],
    household_and_user: tuple[Household, object],
) -> None:
    household, _ = household_and_user
    with session_maker() as session:
        writer = AIInvocationWriter(session)
        row = writer.write(
            AIInvocationRecord(
                household_id=household.id,
                capability="categorize",
                policy_resolved="permissive",
                profile="default",
                outcome="success",
                provider="anthropic",
                model="claude-opus-4-7",
                tokens_in=120,
                tokens_out=45,
                cost_estimate_usd=Decimal("0.001234"),
                latency_ms=420,
                prompt_hash=b"\x00" * 32,
            )
        )
        session.commit()

        fetched = session.get(AIInvocation, (household.id, row.id))
        assert fetched is not None
        assert fetched.capability == "categorize"
        assert fetched.outcome == "success"
        assert fetched.cost_estimate_usd == Decimal("0.001234")
        assert fetched.prompt_hash == b"\x00" * 32


def test_writer_supports_minimal_record_for_preview(
    session_maker: sessionmaker[Session],
    household_and_user: tuple[Household, object],
) -> None:
    """Preview-only rows carry provider=NULL, tokens=0, cost=0."""
    household, _ = household_and_user
    with session_maker() as session:
        writer = AIInvocationWriter(session)
        writer.write(
            AIInvocationRecord(
                household_id=household.id,
                capability="categorize",
                policy_resolved="permissive",
                profile="default",
                outcome="redacted_only_preview",
                prompt_hash=b"\x11" * 32,
            )
        )
        session.commit()

    with session_maker() as s2:
        from sqlalchemy import select

        row = s2.execute(
            select(AIInvocation).where(AIInvocation.outcome == "redacted_only_preview")
        ).scalar_one()
        assert row.provider is None
        assert row.tokens_in == 0
        assert row.cost_estimate_usd == Decimal("0")

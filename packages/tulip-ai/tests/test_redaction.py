"""Unit tests for ``PromptRedactor`` (ADR-0005 §Q4)."""

from __future__ import annotations

from decimal import Decimal

from tulip_ai.redaction import (
    CategorizeExample,
    CategorizePromptPayload,
    ChartEntry,
    PromptRedactor,
)


def _payload(**overrides: object) -> CategorizePromptPayload:
    defaults: dict[str, object] = {
        "description": "WHOLE FOODS MARKET",
        "amount": Decimal("-87.42"),
        "currency": "USD",
        "posted_date": "2026-05-03",
        "chart": (
            ChartEntry(code="5100", name="Groceries", type="expense"),
            ChartEntry(code="5300", name="Fuel", type="expense"),
        ),
        "recent_examples": (CategorizeExample(description="TRADER JOE'S", code="5100"),),
    }
    defaults.update(overrides)
    return CategorizePromptPayload(**defaults)  # type: ignore[arg-type]


class TestDefaultProfile:
    def test_default_passes_payload_through_unchanged(self) -> None:
        r = PromptRedactor("default")
        body = r.to_message_body(_payload())
        assert body["line"]["amount"] == "-87.42"
        assert body["line"]["description"] == "WHOLE FOODS MARKET"
        assert len(body["recent_examples"]) == 1


class TestStrictProfile:
    def test_strict_buckets_amount_in_message_body(self) -> None:
        body = PromptRedactor("strict").to_message_body(_payload())
        # -87.42 → magnitude 10..100 → "-10-100"
        assert body["line"]["amount"] == "-10-100"

    def test_strict_token_redacts_description(self) -> None:
        body = PromptRedactor("strict").to_message_body(_payload())
        description = body["line"]["description"]
        # "WHOLE", "FOODS", "MARKET" are all >= 4 chars so they survive.
        # Test the redaction with a short common-word token to confirm
        # the redactor stars out tokens it can't keep.
        body2 = PromptRedactor("strict").to_message_body(_payload(description="JOE BAR"))
        # "JOE" is 3 chars and not in the keep-list → starred.
        # "BAR" is 3 chars but IS in the keep-list → kept (drinking est.).
        assert "*" in body2["line"]["description"]
        assert "BAR" in body2["line"]["description"]
        # Sanity: the long-token case keeps tokens.
        assert "WHOLE" in description

    def test_strict_drops_recent_examples(self) -> None:
        body = PromptRedactor("strict").to_message_body(_payload())
        assert body["recent_examples"] == []

    def test_strict_keeps_chart(self) -> None:
        """The chart is the model's menu; redacting it breaks the capability."""
        body = PromptRedactor("strict").to_message_body(_payload())
        codes = [entry["code"] for entry in body["chart"]]
        assert codes == ["5100", "5300"]


class TestLocalOnlyProfile:
    def test_local_only_passes_payload_through(self) -> None:
        """``local_only`` doesn't redact (it just pins to a local provider)."""
        body = PromptRedactor("local_only").to_message_body(_payload())
        assert body["line"]["amount"] == "-87.42"
        assert body["line"]["description"] == "WHOLE FOODS MARKET"
        assert len(body["recent_examples"]) == 1


class TestBucketing:
    def test_zero_amount_buckets_to_zero(self) -> None:
        body = PromptRedactor("strict").to_message_body(_payload(amount=Decimal("0")))
        assert body["line"]["amount"] == "0"

    def test_positive_small_amount(self) -> None:
        body = PromptRedactor("strict").to_message_body(_payload(amount=Decimal("3.14")))
        # magnitude 1..10
        assert body["line"]["amount"] == "1-10"

    def test_large_positive_amount(self) -> None:
        body = PromptRedactor("strict").to_message_body(_payload(amount=Decimal("12345")))
        # magnitude 10000..100000
        assert body["line"]["amount"] == "10000-100000"

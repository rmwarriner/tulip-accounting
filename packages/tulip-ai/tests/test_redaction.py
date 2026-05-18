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


def _row() -> dict[str, object]:
    return {
        "tx_id": "abc",
        "description": "WHOLE FOODS MARKET on Main",
        "amount": Decimal("-42.17"),
        "currency": "USD",
        "date": "2026-05-15",
    }


class TestNLRowRedaction:
    """#347 M-8 + M-9: nl_query row redaction lives in PromptRedactor."""

    def test_default_passes_description_through(self) -> None:
        out = PromptRedactor("default").redact_nl_row(_row())
        # Decimal coerced to string for JSON; description untouched.
        assert out["description"] == "WHOLE FOODS MARKET on Main"
        assert out["amount"] == "-42.17"
        assert out["currency"] == "USD"

    def test_strict_redacts_description_and_buckets_decimal_as_str(self) -> None:
        out = PromptRedactor("strict").redact_nl_row(_row())
        # 'on' (2 chars) gets starred; multi-char tokens survive.
        assert "*" in out["description"]
        assert "WHOLE" in out["description"]
        assert out["amount"] == "-42.17"  # amounts not redacted

    def test_local_only_passes_through_decimal_unchanged(self) -> None:
        out = PromptRedactor("local_only").redact_nl_row(_row())
        assert out["description"] == "WHOLE FOODS MARKET on Main"
        # local_only is the verbatim pass-through path.
        assert out["amount"] == Decimal("-42.17")

    def test_redact_nl_rows_bulk(self) -> None:
        baseline = _row()
        out = PromptRedactor("default").redact_nl_rows([baseline, baseline])
        assert len(out) == 2
        assert all(r["description"] == baseline["description"] for r in out)

    def test_strict_handles_none_description(self) -> None:
        row = {"description": None, "amount": Decimal("1")}
        out = PromptRedactor("strict").redact_nl_row(row)
        assert out["description"] in {"", "(redacted)"}


class TestForecastBucketingViaRedactor:
    """#347 M-8: bucketing logic lives in PromptRedactor."""

    def test_default_uses_5pct_bucket(self) -> None:
        from datetime import date as _date

        series = [(_date(2026, 1, i + 1), Decimal(str(i * 10))) for i in range(10)]
        out = PromptRedactor("default").bucket_time_series(series)
        # max_abs = 90; bucket = 4.5; values rounded to nearest 4.5.
        assert out[0][1] == Decimal("0")
        assert out[2][1] == Decimal("18")  # 20 → 18 (4x 4.5)
        assert out[9][1] == Decimal("90")  # 90 → 90 (20x 4.5)

    def test_strict_uses_25pct_bucket(self) -> None:
        from datetime import date as _date

        series = [(_date(2026, 1, i + 1), Decimal(str(i * 10))) for i in range(10)]
        out = PromptRedactor("strict").bucket_time_series(series)
        # max_abs = 90; bucket = 22.5; coarser buckets.
        assert out[1][1] == Decimal("0")  # 10 → 0
        assert out[5][1] == Decimal("45")  # 50 → 45 (2x 22.5)

    def test_local_only_passes_through(self) -> None:
        from datetime import date as _date

        series = [(_date(2026, 1, 1), Decimal("3.14"))]
        out = PromptRedactor("local_only").bucket_time_series(series)
        assert out == series

    def test_empty_series(self) -> None:
        assert PromptRedactor("default").bucket_time_series([]) == []

    def test_envelope_name_strict_elision(self) -> None:
        assert PromptRedactor("default").forecast_envelope_name("Groceries") == "Groceries"
        assert PromptRedactor("strict").forecast_envelope_name("Groceries") is None
        assert PromptRedactor("local_only").forecast_envelope_name("Groceries") == "Groceries"


class TestComputePromptHash:
    """#347 M-12: single helper for the prompt-hash audit invariant."""

    def test_same_body_same_hash(self) -> None:
        from tulip_ai.redaction import compute_prompt_hash

        body = {"k": [1, 2, 3], "t": "x"}
        assert compute_prompt_hash(body) == compute_prompt_hash(body)

    def test_different_bodies_different_hashes(self) -> None:
        from tulip_ai.redaction import compute_prompt_hash

        assert compute_prompt_hash({"k": 1}) != compute_prompt_hash({"k": 2})

    def test_hash_is_32_bytes(self) -> None:
        from tulip_ai.redaction import compute_prompt_hash

        assert len(compute_prompt_hash({"x": 1})) == 32

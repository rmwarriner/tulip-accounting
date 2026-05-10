"""Unit tests for ``_summarize_refill_rule`` (#137).

Pure-function summariser that turns an envelope's ``refill_rule`` JSON
into the one-liner shown in the inline list view.
"""

from __future__ import annotations

import pytest

from tulip_cli.commands._pools import _summarize_refill_rule


def test_none_rule_renders_em_dash() -> None:
    assert _summarize_refill_rule(None) == "—"


def test_empty_rule_renders_em_dash() -> None:
    assert _summarize_refill_rule({}) == "—"


def test_fixed_amount_includes_amount_and_currency() -> None:
    out = _summarize_refill_rule(
        {"strategy": "fixed_amount", "amount": "100.00", "currency": "USD"}
    )
    assert "fixed" in out
    assert "100.00" in out
    assert "USD" in out


def test_fill_to_amount_includes_target() -> None:
    out = _summarize_refill_rule(
        {"strategy": "fill_to_amount", "amount": "500.00", "currency": "USD"}
    )
    assert "target" in out
    assert "500.00" in out


@pytest.mark.parametrize(
    ("decimal_pct", "expected"),
    [(0.05, "5%"), (0.1, "10%"), (0.125, "12.5%")],
)
def test_percentage_of_income_renders_percent(decimal_pct: float, expected: str) -> None:
    out = _summarize_refill_rule({"strategy": "percentage_of_income", "percentage": decimal_pct})
    assert "pct-inflow" in out
    assert expected in out


def test_unknown_strategy_falls_back_to_strategy_name() -> None:
    out = _summarize_refill_rule({"strategy": "moonbeams"})
    assert out == "moonbeams"

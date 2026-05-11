"""Unit + property tests for ``tulip_core.insights.find_anomalies`` (P6.3)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tulip_core.insights import AnomalySeverity, find_anomalies


def _flat_series(n_days: int, value: str = "10.00") -> list[tuple[date, Decimal]]:
    return [(date(2026, 1, 1) + timedelta(days=i), Decimal(value)) for i in range(n_days)]


class TestNoAnomalies:
    def test_short_series_returns_empty(self) -> None:
        assert find_anomalies(_flat_series(5), window_size=30) == []

    def test_flat_series_no_anomaly(self) -> None:
        # window 30, then 30 more flat days — no stdev → no anomaly.
        assert find_anomalies(_flat_series(60), window_size=30) == []

    def test_negative_tail_not_reported(self) -> None:
        # Spending drops sharply — anomaly *below* the mean; not reported in v1.
        series = [
            *_flat_series(30, "100.00"),
            (date(2026, 2, 1), Decimal("1.00")),
        ]
        assert find_anomalies(series, window_size=30) == []


class TestDetection:
    def test_spike_above_threshold_reported(self) -> None:
        # 30 days of $10 spend → mean=10, stdev=0; window must have variance
        # to produce a z. Build variance with alternating $10/$11.
        series: list[tuple[date, Decimal]] = []
        d0 = date(2026, 1, 1)
        for i in range(30):
            series.append((d0 + timedelta(days=i), Decimal("10") + Decimal(i % 2)))
        # Spike day 30: $100.
        series.append((d0 + timedelta(days=30), Decimal("100")))
        anomalies = find_anomalies(series, window_size=30)
        assert len(anomalies) == 1
        assert anomalies[0].amount == Decimal("100")
        # z is way above 2.
        assert anomalies[0].z_score > Decimal("2")

    def test_critical_severity_for_high_z(self) -> None:
        series: list[tuple[date, Decimal]] = [
            (date(2026, 1, 1) + timedelta(days=i), Decimal("10") + Decimal(i % 2))
            for i in range(30)
        ]
        series.append((date(2026, 1, 31), Decimal("1000")))
        anomalies = find_anomalies(series, window_size=30)
        assert anomalies[0].severity == AnomalySeverity.CRITICAL


class TestValidation:
    def test_window_size_must_be_greater_than_one(self) -> None:
        with pytest.raises(ValueError, match="window_size"):
            find_anomalies(_flat_series(10), window_size=1)


class TestProperty:
    @given(
        n=st.integers(min_value=31, max_value=80),
        flat_value=st.decimals(min_value=Decimal("0"), max_value=Decimal("100"), places=2),
    )
    def test_flat_series_never_flags(self, n: int, flat_value: Decimal) -> None:
        """A truly flat series has stdev=0; no anomaly should ever flag."""
        series = [(date(2026, 1, 1) + timedelta(days=i), flat_value) for i in range(n)]
        assert find_anomalies(series, window_size=30) == []

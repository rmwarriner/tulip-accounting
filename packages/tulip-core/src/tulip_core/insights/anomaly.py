"""Rolling-mean / stdev anomaly detection over a daily spending series.

A series is a list of ``(date, amount)`` pairs in chronological order;
amounts are ``Decimal`` (per the project-wide "no float on money" rule).
``find_anomalies`` walks the series with a rolling window and reports
any sample whose value is more than ``threshold_sigma`` standard
deviations above the rolling mean.

Pure-domain by design — no I/O, no clock, no framework deps. The
scheduler handler that consumes this in P6.3 plugs the result into the
notifications table; tests exercise the math directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class AnomalySeverity(Enum):
    """Magnitude bucket; matches the notification severity column."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class Anomaly:
    """One detected anomaly: amount above the rolling mean."""

    sample_date: date
    amount: Decimal
    rolling_mean: Decimal
    rolling_stdev: Decimal
    z_score: Decimal
    severity: AnomalySeverity


def _stdev(values: list[Decimal], mean: Decimal) -> Decimal:
    """Sample standard deviation, returning ``Decimal('0')`` for ``len < 2``."""
    if len(values) < 2:
        return Decimal("0")
    n = Decimal(len(values))
    variance = sum(((v - mean) ** 2 for v in values), start=Decimal("0")) / (n - 1)
    # Decimal has no native sqrt, but the ** operator with Decimal('0.5')
    # works for non-negative inputs.
    return variance.sqrt() if hasattr(variance, "sqrt") else Decimal(str(float(variance) ** 0.5))


def _classify(z: Decimal) -> AnomalySeverity:
    """``z`` >= 4 sigma is critical, >= 3 sigma is warning, >= 2 sigma is info."""
    if z >= Decimal("4"):
        return AnomalySeverity.CRITICAL
    if z >= Decimal("3"):
        return AnomalySeverity.WARNING
    return AnomalySeverity.INFO


def find_anomalies(
    series: list[tuple[date, Decimal]],
    *,
    window_size: int = 30,
    threshold_sigma: Decimal = Decimal("2"),
) -> list[Anomaly]:
    """Return samples whose value exceeds ``mean + threshold_sigma * stdev``.

    Only positive-tail anomalies (overspending) are reported — under-spending
    isn't a notification-worthy event in v1.

    Series shorter than ``window_size + 1`` returns ``[]`` (not enough
    history for a meaningful baseline).
    """
    if window_size <= 1:
        raise ValueError(f"window_size must be > 1 (got {window_size})")
    if len(series) <= window_size:
        return []
    anomalies: list[Anomaly] = []
    for i in range(window_size, len(series)):
        window = [amount for _, amount in series[i - window_size : i]]
        sample_date, sample_amount = series[i]
        mean = sum(window, start=Decimal("0")) / Decimal(len(window))
        stdev = _stdev(window, mean)
        if stdev == 0:
            continue
        z = (sample_amount - mean) / stdev
        if z >= threshold_sigma:
            anomalies.append(
                Anomaly(
                    sample_date=sample_date,
                    amount=sample_amount,
                    rolling_mean=mean,
                    rolling_stdev=stdev,
                    z_score=z,
                    severity=_classify(z),
                )
            )
    return anomalies


__all__ = ["Anomaly", "AnomalySeverity", "find_anomalies"]

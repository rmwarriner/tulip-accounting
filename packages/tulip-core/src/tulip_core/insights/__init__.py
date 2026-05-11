"""Pure-domain anomaly + forecast primitives (P6.3).

The functions here are testable in isolation: no DB, no clock, no I/O,
no framework deps. ``tulip-storage`` and ``tulip-ai`` consume them; the
scheduler handler wires a daily run.
"""

from __future__ import annotations

from tulip_core.insights.anomaly import (
    Anomaly,
    AnomalySeverity,
    find_anomalies,
)

__all__ = [
    "Anomaly",
    "AnomalySeverity",
    "find_anomalies",
]

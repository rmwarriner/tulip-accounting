"""Unit tests for Period value object."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from tulip_core.periods import Period, PeriodStatus


class TestPeriodConstruction:
    def test_open_period(self):
        p = Period(
            id=uuid4(),
            household_id=uuid4(),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            status=PeriodStatus.OPEN,
        )
        assert p.is_open is True
        assert p.is_soft_closed is False

    def test_soft_closed_period(self):
        p = Period(
            id=uuid4(),
            household_id=uuid4(),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            status=PeriodStatus.SOFT_CLOSED,
        )
        assert p.is_open is False
        assert p.is_soft_closed is True

    def test_end_before_start_raises(self):
        with pytest.raises(ValueError, match="end_date"):
            Period(
                id=uuid4(),
                household_id=uuid4(),
                start_date=date(2026, 2, 1),
                end_date=date(2026, 1, 31),
                status=PeriodStatus.OPEN,
            )

    def test_end_equal_to_start_is_allowed(self):
        # A single-day period is permitted.
        p = Period(
            id=uuid4(),
            household_id=uuid4(),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 1),
            status=PeriodStatus.OPEN,
        )
        assert p.contains(date(2026, 1, 1)) is True


class TestPeriodContains:
    def _period(self) -> Period:
        return Period(
            id=uuid4(),
            household_id=uuid4(),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            status=PeriodStatus.OPEN,
        )

    def test_contains_inside(self):
        assert self._period().contains(date(2026, 1, 15)) is True

    def test_contains_start_inclusive(self):
        assert self._period().contains(date(2026, 1, 1)) is True

    def test_contains_end_inclusive(self):
        assert self._period().contains(date(2026, 1, 31)) is True

    def test_contains_outside(self):
        p = self._period()
        assert p.contains(date(2025, 12, 31)) is False
        assert p.contains(date(2026, 2, 1)) is False

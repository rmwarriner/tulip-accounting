"""Period: an accounting period with open/soft-closed status."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date
    from uuid import UUID


class PeriodStatus(Enum):
    """Whether a period accepts new postings without warning."""

    OPEN = "open"
    SOFT_CLOSED = "soft_closed"


@dataclass(frozen=True, slots=True)
class Period:
    """An inclusive [start_date, end_date] window with a status.

    Soft-closed periods still accept postings (the API layer logs and warns)
    but reports treat the figures as closed-with-overrides. See
    ARCHITECTURE.md §5.5.
    """

    id: UUID
    household_id: UUID
    start_date: date
    end_date: date
    status: PeriodStatus

    def __post_init__(self) -> None:
        """Reject inverted ranges; equal dates (single-day period) are allowed."""
        if self.end_date < self.start_date:
            raise ValueError(
                f"end_date ({self.end_date}) must be >= start_date ({self.start_date})"
            )

    @property
    def is_open(self) -> bool:
        """Return True iff this period accepts postings without warning."""
        return self.status is PeriodStatus.OPEN

    @property
    def is_soft_closed(self) -> bool:
        """Return True iff this period is soft-closed."""
        return self.status is PeriodStatus.SOFT_CLOSED

    def contains(self, d: date) -> bool:
        """Return True iff `d` lies within this period (inclusive bounds)."""
        return self.start_date <= d <= self.end_date

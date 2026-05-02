"""SinkingFund: a goal-bounded pool with target amount/date and contribution strategy."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from tulip_core.allocation.pool import Pool, PoolType

if TYPE_CHECKING:
    from datetime import date

    from tulip_core.money import Money


class ContributionStrategy(Enum):
    """How a sinking fund's scheduled contribution is computed."""

    MANUAL = "manual"
    EVEN_SPLIT = "even_split"
    PERCENTAGE_OF_INCOME = "percentage_of_income"


@dataclass(frozen=True, slots=True)
class SinkingFund:
    """A goal-bounded savings pool on top of a :class:`Pool`."""

    pool: Pool
    target_amount: Money
    target_date: date
    contribution_strategy: ContributionStrategy
    contribution_amount: Money | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate pool type, currency consistency, and per-strategy fields."""
        if self.pool.pool_type is not PoolType.SINKING_FUND:
            raise ValueError(
                f"SinkingFund must wrap a pool of type 'sinking_fund', got "
                f"{self.pool.pool_type.value!r}"
            )
        if self.target_amount.currency != self.pool.currency:
            raise ValueError(
                f"target_amount currency {self.target_amount.currency!r} "
                f"does not match pool currency {self.pool.currency!r}"
            )
        if self.target_amount.amount <= 0:
            raise ValueError(f"target_amount must be positive, got {self.target_amount.amount}")
        if (
            self.contribution_amount is not None
            and self.contribution_amount.currency != self.pool.currency
        ):
            raise ValueError(
                f"contribution_amount currency {self.contribution_amount.currency!r} "
                f"does not match pool currency {self.pool.currency!r}"
            )
        if self.contribution_strategy is ContributionStrategy.MANUAL:
            # Manual strategy: contribution_amount is optional, no further checks.
            pass
        elif self.contribution_strategy is ContributionStrategy.EVEN_SPLIT:
            if self.contribution_amount is not None:
                raise ValueError("contribution_amount is derived for EVEN_SPLIT; do not set it")
        elif self.contribution_strategy is ContributionStrategy.PERCENTAGE_OF_INCOME:
            if self.contribution_amount is not None:
                raise ValueError(
                    "contribution_amount is derived for PERCENTAGE_OF_INCOME; do not set it"
                )

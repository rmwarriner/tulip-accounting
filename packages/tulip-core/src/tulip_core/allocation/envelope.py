"""Envelope: a periodic-budget pool with rollover policy and optional refill rule."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from tulip_core.allocation.pool import Pool, PoolType

if TYPE_CHECKING:
    from tulip_core.allocation.refill_rule import RefillRule
    from tulip_core.money import Money


class BudgetPeriod(Enum):
    """The cadence on which an envelope's budget renews."""

    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    CUSTOM = "custom"


class RolloverPolicy(Enum):
    """What happens to an envelope's unused balance at period end.

    - ``RESET``: balance returns to budget_amount at next period start.
    - ``ACCUMULATE``: balance carries over (savings-style envelope).
    - ``CAP_AT_BUDGET``: carry over up to budget_amount; trim the excess.
    """

    RESET = "reset"
    ACCUMULATE = "accumulate"
    CAP_AT_BUDGET = "cap_at_budget"


@dataclass(frozen=True, slots=True)
class Envelope:
    """An envelope (period-bounded budget) on top of a :class:`Pool`."""

    pool: Pool
    budget_period: BudgetPeriod
    rollover_policy: RolloverPolicy
    budget_amount: Money | None = field(default=None)
    refill_rule: RefillRule | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate the envelope's pool type and currency consistency."""
        if self.pool.pool_type is not PoolType.ENVELOPE:
            raise ValueError(
                f"Envelope must wrap a pool of type 'envelope', got {self.pool.pool_type.value!r}"
            )
        if self.budget_amount is not None and self.budget_amount.currency != self.pool.currency:
            raise ValueError(
                f"budget_amount currency {self.budget_amount.currency!r} "
                f"does not match pool currency {self.pool.currency!r}"
            )
        if self.budget_amount is not None and self.budget_amount.amount < 0:
            raise ValueError(f"budget_amount must be non-negative, got {self.budget_amount.amount}")
        if (
            self.refill_rule is not None
            and self.refill_rule.amount is not None
            and self.refill_rule.amount.currency != self.pool.currency
        ):
            raise ValueError(
                f"refill_rule.amount currency {self.refill_rule.amount.currency!r} "
                f"does not match pool currency {self.pool.currency!r}"
            )

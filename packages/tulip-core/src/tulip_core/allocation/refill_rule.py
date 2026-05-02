"""RefillRule: a structured value object describing how an envelope refills.

Three strategies are supported. The shape is **structured** — no expression
language, no string-to-execute — to keep refill execution off any
code-execution path. Per the threat-model checkpoint
(docs/THREAT_MODEL.md §5.1), refill rules persisted as JSON must never be
passed to a Python expression evaluator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tulip_core.money import Money


class RefillStrategy(Enum):
    """Three refill strategies. See ADR-0001 / ARCHITECTURE §5.3."""

    FIXED_AMOUNT = "fixed_amount"
    FILL_TO_AMOUNT = "fill_to_amount"
    PERCENTAGE_OF_INCOME = "percentage_of_income"


@dataclass(frozen=True, slots=True)
class RefillRule:
    """How (and how much) an envelope receives at the next refill event.

    - ``FIXED_AMOUNT``: contribute ``amount`` per period. ``percentage`` unset.
    - ``FILL_TO_AMOUNT``: top up the envelope to ``amount`` per period.
      ``percentage`` unset.
    - ``PERCENTAGE_OF_INCOME``: contribute ``percentage`` of the next inflow.
      ``amount`` unset; ``percentage`` is a fraction in (0, 1].
    """

    strategy: RefillStrategy
    amount: Money | None = field(default=None)
    percentage: Decimal | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate the per-strategy field shape."""
        if self.strategy in (RefillStrategy.FIXED_AMOUNT, RefillStrategy.FILL_TO_AMOUNT):
            if self.amount is None:
                raise ValueError(f"strategy={self.strategy.value} requires amount; got None")
            if self.percentage is not None:
                raise ValueError(f"strategy={self.strategy.value} forbids percentage")
            if self.amount.amount <= 0:
                raise ValueError(f"strategy={self.strategy.value} requires positive amount")
        elif self.strategy is RefillStrategy.PERCENTAGE_OF_INCOME:
            if self.percentage is None:
                raise ValueError("strategy=percentage_of_income requires percentage; got None")
            if self.amount is not None:
                raise ValueError("strategy=percentage_of_income forbids amount")
            if not (Decimal(0) < self.percentage <= Decimal(1)):
                raise ValueError(f"percentage must be in (0, 1], got {self.percentage}")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict for storage in ``envelopes.refill_rule_json``.

        The shape is intentionally simple and round-trips through
        :meth:`from_dict`. No code, no callables, no references — just
        primitives.
        """
        out: dict[str, Any] = {"strategy": self.strategy.value}
        if self.amount is not None:
            out["amount"] = str(self.amount.amount)
            out["currency"] = self.amount.currency
        if self.percentage is not None:
            out["percentage"] = str(self.percentage)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RefillRule:
        """Deserialize from the dict shape produced by :meth:`to_dict`."""
        from tulip_core.money import Money

        strategy = RefillStrategy(data["strategy"])
        amount: Money | None = None
        percentage: Decimal | None = None
        if "amount" in data:
            amount = Money(Decimal(data["amount"]), data["currency"])
        if "percentage" in data:
            percentage = Decimal(data["percentage"])
        return cls(strategy=strategy, amount=amount, percentage=percentage)

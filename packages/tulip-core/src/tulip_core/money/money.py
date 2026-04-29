"""Money value object: an immutable (Decimal amount, ISO 4217 currency) pair.

No `float` ever touches a Money value. Cross-currency arithmetic is forbidden
and raises CurrencyMismatchError. This is the foundation invariant of the
whole accounting system; every monetary operation in higher layers ultimately
goes through this object.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

from tulip_core.currency import Currency

if TYPE_CHECKING:
    from typing import Self


class CurrencyMismatchError(ValueError):
    """Raised when an operation requires two Money values of the same currency."""


@dataclass(frozen=True, slots=True)
class Money:
    """An immutable (Decimal amount, ISO 4217 currency) pair.

    Floats are rejected at construction time. Arithmetic across currencies is
    forbidden. Multiplication by a scalar (int or Decimal) is supported;
    multiplication by another Money or by a float is not.
    """

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        """Validate that amount is a Decimal and currency is recognized."""
        # Route through object so the runtime check survives mypy --strict;
        # the annotated type is Decimal, but Python doesn't enforce that and
        # we must reject floats explicitly (no float ever touches money).
        amount_obj: object = self.amount
        if isinstance(amount_obj, bool) or not isinstance(amount_obj, Decimal):
            raise TypeError(f"Money amount must be Decimal, got {type(self.amount).__name__}")
        # Currency.from_code raises ValueError on unknown / malformed codes,
        # which preserves the Money construction contract.
        Currency.from_code(self.currency)

    @classmethod
    def zero(cls, currency: str) -> Self:
        """Return a zero-amount Money in the given currency."""
        return cls(Decimal("0"), currency)

    def _check_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot operate on Money values of different currencies: "
                f"{self.currency} vs {other.currency}"
            )

    def __add__(self, other: Money) -> Money:
        """Return the sum of two Money values of the same currency."""
        self._check_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        """Return the difference of two Money values of the same currency."""
        self._check_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        """Return a Money with the negated amount and the same currency."""
        return Money(-self.amount, self.currency)

    def __mul__(self, scalar: int | Decimal) -> Money:
        """Multiply by an int or Decimal scalar; reject Money, float, and bool."""
        # Route through object so runtime guards against float / Money survive
        # mypy --strict; the annotated type already rules them out for callers.
        scalar_obj: object = scalar
        if isinstance(scalar_obj, bool | float | Money):
            raise TypeError(f"Cannot multiply Money by {type(scalar).__name__}; use int or Decimal")
        if not isinstance(scalar_obj, int | Decimal):
            raise TypeError(f"Cannot multiply Money by {type(scalar).__name__}; use int or Decimal")
        return Money(self.amount * Decimal(scalar), self.currency)

    def quantize_to_currency(self) -> Money:
        """Round amount to the currency's minor units using banker's rounding.

        Uses decimal.ROUND_HALF_EVEN. The number of fractional digits is the
        currency's `minor_units` (USD=2, JPY=0, BHD=3). Idempotent.
        """
        minor_units = Currency.from_code(self.currency).minor_units
        # Decimal("1e-N") is exactly the quantum we want at N fractional digits;
        # for minor_units=0 use Decimal("1") so the result has no fractional part.
        quantum = Decimal(1).scaleb(-minor_units) if minor_units > 0 else Decimal(1)
        rounded = self.amount.quantize(quantum, rounding=ROUND_HALF_EVEN)
        return Money(rounded, self.currency)

    def __repr__(self) -> str:
        """Return a useful debug string like Money('87.42', 'USD')."""
        return f"Money('{self.amount}', '{self.currency}')"

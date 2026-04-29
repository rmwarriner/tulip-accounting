"""ISO 4217 currency value object with minor-unit metadata.

The currency table here is intentionally a small in-process constant; expand
from a data file (or import from `iso4217` / `babel`) when more breadth is
needed. Phase 0's tests only require a handful of codes plus the canonical
interning contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Minor-unit table per ISO 4217. Most currencies use 2; notable exceptions:
# JPY/KRW (0), BHD/JOD/KWD/OMR (3), CLF (4).
_MINOR_UNITS: Final[dict[str, int]] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "JPY": 0,
    "CAD": 2,
    "AUD": 2,
    "CHF": 2,
    "CNY": 2,
    "BHD": 3,
}


@dataclass(frozen=True, slots=True)
class Currency:
    """An ISO 4217 currency.

    Construct via `Currency.from_code(...)`, which validates the code and
    returns a canonical (interned) instance — repeated lookups for the same
    code return the same object.
    """

    code: str
    minor_units: int

    @classmethod
    def from_code(cls, code: str) -> Currency:
        """Return the canonical Currency for the given ISO 4217 code.

        Raises:
            ValueError: if the code is not exactly three uppercase letters,
                or if it is not in the known currency table.

        """
        if not (len(code) == 3 and code.isascii() and code.isupper() and code.isalpha()):
            raise ValueError(f"Invalid ISO 4217 code: {code!r}")
        if code not in _MINOR_UNITS:
            raise ValueError(f"Unknown ISO 4217 currency: {code!r}")
        return _intern(code)


# Module-level cache for canonical instances. Populated lazily via from_code.
_CACHE: dict[str, Currency] = {}


def _intern(code: str) -> Currency:
    cached = _CACHE.get(code)
    if cached is not None:
        return cached
    instance = Currency(code=code, minor_units=_MINOR_UNITS[code])
    _CACHE[code] = instance
    return instance

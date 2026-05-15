"""Currency-natural display formatting for CLI rendering (issue #213).

The Tulip API serializes monetary amounts as ``Decimal`` strings at full
storage precision (e.g. ``"12.20000000"`` for a value that came in via QIF
or OFX). For machine consumers that's the right answer — the trailing
digits preserve the imported precision. For humans reading a ``tulip
imports show`` table or a ``tulip balance`` trial-balance it is just
visual noise.

This helper quantises an amount string to the currency's natural minor-unit
precision for display only (USD/EUR/GBP → 2, JPY → 0, BHD → 3, etc.). The
canonical minor-units table lives in :mod:`tulip_core.currency`; we
duplicate the subset we need here rather than importing ``tulip_core``,
because ARCHITECTURE.md §9 forbids ``tulip-cli`` from depending on
``tulip-core`` directly (CLI is a network client of the API).

The duplication is small and the values are ISO 4217 — they don't drift.
Unknown currencies fall back to two decimals (the overwhelmingly common
case) so this never raises on display.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Final

# Mirror of the ``tulip_core.currency._MINOR_UNITS`` table. Kept in sync by
# convention — ISO 4217 minor-unit assignments don't churn. When a new
# currency is added to ``tulip-core``, mirror it here.
_DISPLAY_MINOR_UNITS: Final[dict[str, int]] = {
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

_DEFAULT_MINOR_UNITS: Final[int] = 2


def _minor_units(currency: str | None) -> int:
    """Return the display precision for ``currency`` (defaults to 2)."""
    if not currency:
        return _DEFAULT_MINOR_UNITS
    return _DISPLAY_MINOR_UNITS.get(currency.upper(), _DEFAULT_MINOR_UNITS)


def format_amount(amount: object, currency: str | None) -> str:
    """Render ``amount`` at ``currency``'s natural minor-unit precision.

    ``amount`` may be a ``Decimal``, a string (the API's JSON encoding),
    an ``int``, or anything else — non-numeric input is passed through
    via ``str()`` unchanged so the rendering never crashes on display.

    Banker's rounding (``ROUND_HALF_EVEN``) matches the rest of Tulip's
    money handling (``Money.quantize_to_currency``).
    """
    if amount is None:
        return ""
    try:
        if isinstance(amount, Decimal):
            dec = amount
        else:
            dec = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return str(amount)

    minor = _minor_units(currency)
    quantum = Decimal(1).scaleb(-minor) if minor > 0 else Decimal(1)
    quantized = dec.quantize(quantum, rounding=ROUND_HALF_EVEN)
    return str(quantized)


__all__ = ["format_amount"]

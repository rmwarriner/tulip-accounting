"""Pure refill-rule evaluation engine — see ADR-0002 §7.

Given an envelope's :class:`RefillRule`, the envelope's current balance,
and (for ``PERCENTAGE_OF_INCOME``) the household's recent inflow,
returns the ``Money`` amount the runner should refill into the envelope.

Pure function: no DB, no clock, no I/O. The runner handler in
``tulip_storage`` builds the inputs and consumes the output. This
separation keeps the math in :mod:`tulip_core` testable without
infrastructure and keeps the no-eval guarantee (per
``docs/THREAT_MODEL.md §5.1``) trivially provable.
"""

from __future__ import annotations

from tulip_core.allocation.refill_rule import RefillRule, RefillStrategy
from tulip_core.money import CurrencyMismatchError, Money


def evaluate_refill_rule(
    rule: RefillRule,
    *,
    current_balance: Money,
    recent_inflow: Money | None = None,
) -> Money:
    """Compute the amount to add to an envelope on this refill cycle.

    The returned amount is always non-negative: a rule that produces a
    zero or negative contribution (e.g. ``FILL_TO_AMOUNT`` when the
    envelope is already at or above target) yields ``Money.zero(...)``.
    The runner skips the shadow-tx write when amount is zero.

    Args:
        rule: The envelope's stored refill rule.
        current_balance: The envelope's balance in the envelope's
            currency. The result is always returned in this currency
            (envelopes are single-currency by construction).
        recent_inflow: For ``PERCENTAGE_OF_INCOME``, the household's
            recent inflow in the envelope's currency. ``None`` (or zero)
            means no inflow since the last evaluation; the rule
            contributes nothing.

    Returns:
        ``Money`` in ``current_balance.currency``. Always non-negative.

    Raises:
        CurrencyMismatchError: A rule input's currency disagrees with
            ``current_balance.currency``. Indicates an upstream bug — the
            envelope's storage layer should already enforce that
            ``rule.amount.currency == envelope.currency``.

    """
    target_ccy = current_balance.currency

    if rule.strategy is RefillStrategy.FIXED_AMOUNT:
        # Pre-validated by RefillRule.__post_init__: amount is non-None
        # and positive. Currency must match the envelope.
        assert rule.amount is not None  # noqa: S101 - validated by __post_init__
        if rule.amount.currency != target_ccy:
            raise CurrencyMismatchError(
                f"FIXED_AMOUNT rule currency {rule.amount.currency!r} "
                f"differs from envelope currency {target_ccy!r}"
            )
        return rule.amount

    if rule.strategy is RefillStrategy.FILL_TO_AMOUNT:
        assert rule.amount is not None  # noqa: S101 - validated by __post_init__
        if rule.amount.currency != target_ccy:
            raise CurrencyMismatchError(
                f"FILL_TO_AMOUNT rule currency {rule.amount.currency!r} "
                f"differs from envelope currency {target_ccy!r}"
            )
        gap = rule.amount.amount - current_balance.amount
        if gap <= 0:
            # Envelope already at or above target — no contribution.
            return Money.zero(target_ccy)
        return Money(gap, target_ccy)

    # PERCENTAGE_OF_INCOME — validated by __post_init__: percentage is
    # non-None and in (0, 1]. amount is None.
    assert rule.strategy is RefillStrategy.PERCENTAGE_OF_INCOME  # noqa: S101
    assert rule.percentage is not None  # noqa: S101 - validated by __post_init__

    if recent_inflow is None or recent_inflow.amount <= 0:
        return Money.zero(target_ccy)
    if recent_inflow.currency != target_ccy:
        raise CurrencyMismatchError(
            f"recent_inflow currency {recent_inflow.currency!r} differs "
            f"from envelope currency {target_ccy!r}"
        )
    contribution = recent_inflow.amount * rule.percentage
    if contribution <= 0:
        return Money.zero(target_ccy)
    return Money(contribution, target_ccy)


__all__ = ["evaluate_refill_rule"]

"""RRULE helper — wraps ``dateutil.rrule.rrulestr`` for the runner.

Per ADR-0002 §5, schedules are stored as RFC 5545 RRULE strings on
``scheduled_jobs.rrule`` (e.g. ``"FREQ=MONTHLY;BYMONTHDAY=1"``). The runner
calls :func:`compute_next_fire` after each successful run to advance
``next_run_at``.
"""

from __future__ import annotations

from datetime import datetime

from dateutil.rrule import rrulestr


class InvalidRRuleError(ValueError):
    """Raised when an RRULE string fails to parse."""


def compute_next_fire(
    rrule: str,
    *,
    dtstart: datetime,
    after: datetime,
    inclusive: bool = False,
) -> datetime | None:
    """Return the next datetime an RRULE fires after ``after``.

    The RRULE's series is anchored at ``dtstart`` — important for
    bounded rules (``COUNT=`` / ``UNTIL=``) where the count is from the
    original schedule's start, not from the polling moment. ``after`` is
    the cursor: occurrences strictly after ``after`` (or at-or-after
    when ``inclusive=True``).

    Returns ``None`` if the rule is bounded and has no occurrence after
    ``after`` — i.e. the recurring job is exhausted and the runner
    should deactivate it.

    Args:
        rrule: An RFC 5545 RRULE string (e.g. ``"FREQ=DAILY;COUNT=12"``).
        dtstart: The original anchor of the recurrence series. For
            ``schedule_recurring(start_at=X)``, this is X. Stays stable
            across the job's lifetime.
        after: The cursor; the next occurrence is after this.
        inclusive: When True, ``after`` itself is a candidate.

    Raises:
        InvalidRRuleError: ``rrule`` could not be parsed.

    """
    try:
        rule = rrulestr(rrule, dtstart=dtstart)
    except (ValueError, TypeError) as exc:
        msg = f"invalid RRULE {rrule!r}: {exc}"
        raise InvalidRRuleError(msg) from exc
    next_dt = rule.after(after, inc=inclusive)
    if next_dt is None:
        return None
    # ``dateutil`` returns a tz-aware datetime when both ``dtstart`` and
    # ``after`` are tz-aware.
    return next_dt

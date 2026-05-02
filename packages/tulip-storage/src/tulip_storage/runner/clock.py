"""Clock injection seam for the runner.

Per ADR-0002 §6, the runner is the only place in the codebase that takes
a ``Clock`` callable. Every other path uses real ``datetime.now(UTC)``;
the runner is the only meaningful time-dependent component, so we
constrain the injection seam to it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

#: A callable returning the current time. The runner threads this through
#: the polling loop and into every handler invocation.
Clock = Callable[[], datetime]


def default_clock() -> datetime:
    """Return the current UTC time. The default ``Clock`` for the runner."""
    return datetime.now(UTC)

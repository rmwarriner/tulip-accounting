"""Rich ``Console`` factory that honors the ``COLUMNS`` env var (#285).

Rich only reads ``COLUMNS`` when stdout is a TTY. In a piped or
subprocess context — CI, ``tulip ... | less``, the test harness — it
falls back to a fixed 80-column width, which truncates table and panel
content unpredictably. Several CLI tests have had to work around this
one site at a time (pinning ``COLUMNS`` in the subprocess env, then
discovering it had no effect).

This factory threads an explicit ``width=`` through from ``COLUMNS``
when it's set to a positive integer, so callers and tests get the width
they ask for. When ``COLUMNS`` is unset or junk, it falls back to
Rich's normal auto-detection (``width=None``) — identical to today's
behaviour.
"""

from __future__ import annotations

import os

from rich.console import Console

#: Width to fall back to when ``COLUMNS`` is set but unusable. Matches
#: Rich's own conventional non-TTY default. Used only to *override* a
#: junk ``COLUMNS`` — see ``_resolve_width``.
_SAFE_DEFAULT_WIDTH = 80


def _resolve_width() -> int | None:
    """Decide the explicit ``width=`` to hand Rich, based on ``COLUMNS``.

    Three cases:

    * ``COLUMNS`` unset → return ``None`` so Rich auto-detects (real
      terminal size on a TTY, 80 otherwise).
    * ``COLUMNS`` is a positive integer → use it. This is the whole
      point of the helper: Rich ignores ``COLUMNS`` off a TTY.
    * ``COLUMNS`` set but junk / zero / negative → return a safe default
      rather than ``None``. ``None`` isn't enough here: Rich's *own*
      auto-detect also reads ``COLUMNS`` and would render at width 0 for
      ``COLUMNS=0`` (seen in some CI shells). Forcing the default
      overrides that.
    """
    raw = os.environ.get("COLUMNS")
    if raw is None:
        return None
    stripped = raw.strip()
    if stripped.isdigit():
        value = int(stripped)
        if value > 0:
            return value
    return _SAFE_DEFAULT_WIDTH


def make_console(*, stderr: bool = False, highlight: bool = True) -> Console:
    """Build a Rich ``Console`` that respects ``COLUMNS`` even off a TTY.

    ``stderr`` and ``highlight`` are the only ``Console`` kwargs the CLI
    actually uses; keeping the signature explicit avoids a catch-all
    ``**kwargs`` and the ``ANN401`` it would attract.
    """
    return Console(stderr=stderr, highlight=highlight, width=_resolve_width())

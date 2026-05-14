"""Shared interactive picker for "missing UUID argument" command paths (#273).

When a CLI command that takes a UUID/prefix positional argument is invoked
without one, instead of immediately erroring out the calling command can
fetch a short list (e.g. recent imports, in-progress reconciliations,
recent transactions) and let the user pick by number. Non-TTY callers
(scripts, CI) still get a clean usage error so they don't deadlock waiting
for input.

The module is intentionally tiny and dependency-free beyond ``typer`` —
callers supply the list (already filtered to "actionable" rows where
applicable) and a row-to-label function. The picker only knows about

* whether stdin is interactive,
* rendering numbered choices + a ``[c] cancel`` row,
* parsing the user's response.

It returns the picked id string or ``None`` when the user cancels (``c``,
empty input, ``EOF``) so callers can map that to ``typer.Exit`` or to
their existing usage-error path uniformly.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from typing import Any

import typer

#: Hard cap on the number of choices rendered. Lists longer than this
#: should be narrowed with the command's existing filter flags rather
#: than scrolled by the user.
PICKER_MAX_ENTRIES = 20


def is_interactive() -> bool:
    """``True`` when stdin is a TTY — i.e. the picker can prompt safely.

    Stdin (not stdout) is the relevant signal: tests and pipelines pipe
    stdout but leave stdin attached. If stdin isn't a TTY we can't ask the
    user a question, so the caller falls back to its legacy usage-error
    path.
    """
    return sys.stdin.isatty()


def pick(
    items: Sequence[dict[str, Any]],
    *,
    label: Callable[[dict[str, Any]], str],
    title: str,
    empty_message: str,
    overflow_hint: str,
    id_key: str = "id",
) -> str | None:
    """Render a numbered picker and return the chosen item's id string.

    Args:
        items: Pre-filtered list of API rows. The caller is responsible
            for any "actionable rows only" filtering (e.g. only ``parsed``
            import batches for ``imports apply``).
        label: ``row -> str`` that renders one row as a human-readable
            line. Width-sensitive callers should keep these short — the
            picker doesn't truncate.
        title: One-line banner rendered above the choices.
        empty_message: Rendered when ``items`` is empty; the picker then
            returns ``None`` without prompting. Callers map that to their
            usage-error exit.
        overflow_hint: Rendered after the list when ``len(items) >
            PICKER_MAX_ENTRIES``. Should name the filter flags the user
            can use to narrow.
        id_key: Key in each row holding the id to return. Defaults to
            ``"id"``.

    Returns:
        The selected row's ``id_key`` value as a string, or ``None`` if
        the user cancelled (``c``, empty input, EOF) or the list was
        empty.
    """
    if not items:
        typer.echo(empty_message, err=True)
        return None

    display = list(items[:PICKER_MAX_ENTRIES])
    typer.echo(title, err=True)
    for idx, row in enumerate(display, start=1):
        typer.echo(f"  [{idx:>2}] {label(row)}", err=True)
    typer.echo("  [ c] cancel", err=True)
    if len(items) > PICKER_MAX_ENTRIES:
        typer.echo(overflow_hint, err=True)

    try:
        raw = typer.prompt("Pick one", default="c", show_default=False)
    except (EOFError, typer.Abort):
        return None
    choice = raw.strip().lower()
    if choice in ("", "c", "cancel"):
        return None
    try:
        position = int(choice)
    except ValueError:
        typer.echo(f"Not a number: {raw!r}. Cancelled.", err=True)
        return None
    if not 1 <= position <= len(display):
        typer.echo(
            f"Choice {position} out of range (1-{len(display)}). Cancelled.",
            err=True,
        )
        return None
    picked = display[position - 1]
    return str(picked[id_key])

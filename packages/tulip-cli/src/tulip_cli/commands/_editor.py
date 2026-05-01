"""Spawn the user's ``$EDITOR`` on a temporary buffer and return what they saved.

Used by ``tulip add --edit`` (#43) to drive the interactive transaction
flow. Mirrors ``git commit``'s editor selection: ``$VISUAL`` →
``$EDITOR`` → platform default (``vi`` on Unix, ``notepad`` on Windows).

The editor command is split with ``shlex.split`` so users can put flags
in their env var (e.g. ``EDITOR='code --wait'``).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

#: Default editor when nothing is configured. Posix-y; Windows users
#: typically have ``EDITOR`` or ``VISUAL`` set already.
_DEFAULT_EDITOR: Final[str] = "notepad" if sys.platform == "win32" else "vi"


def _resolve_editor_command() -> list[str]:
    """Return the editor invocation as a list of argv-style tokens."""
    raw = os.environ.get("VISUAL") or os.environ.get("EDITOR") or _DEFAULT_EDITOR
    parts = shlex.split(raw)
    if not parts:
        return [_DEFAULT_EDITOR]
    return parts


def edit_buffer(initial: str, *, suffix: str = ".tulip") -> str:
    """Open ``initial`` in the user's editor; return the saved buffer.

    A temporary file is created with the given content, the editor is
    spawned synchronously against it, and the file's contents are
    returned after the editor exits. The temp file is deleted in all
    cases (success, editor-non-zero, exception).
    """
    cmd = _resolve_editor_command()
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=suffix,
        delete=False,
    ) as fh:
        path = Path(fh.name)
        fh.write(initial)
    try:
        # Foreground subprocess: editor takes control of the terminal,
        # we wait for it. Don't capture output — let the editor draw.
        subprocess.run([*cmd, str(path)], check=True)  # noqa: S603
        return path.read_text(encoding="utf-8")
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

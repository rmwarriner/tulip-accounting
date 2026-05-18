"""Persistent CLI preferences (#209b first user).

A tiny JSON store at ``~/.config/tulip/preferences.json`` (override via
``$TULIP_PREFERENCES_FILE`` for tests). The first key is
``reconciled_edit_confirm``, written by the ``tulip transactions edit``
flow when the user picks ``[A]lways`` on the "this transaction is
reconciled — edit anyway?" prompt.

Distinct from ``~/.config/tulip/config.toml``: that file is the human-
edited site config (api_url, …). This file is machine-managed — read
and written by the CLI as the user toggles preferences. Mixing the two
would risk the CLI rewriting hand-edited TOML and clobbering comments.

The store is intentionally minimal: no schema validation beyond
"file is JSON", no migrations, no defaults file. Unknown keys are
preserved on round-trip so the file is forward-compatible across
versions that introduce new preference keys.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Final, Literal

#: Type alias for the prompt-pref value the edit-decision matrix uses.
ReconciledEditConfirm = Literal["ask", "never_ask"]

#: The single key written by #209b. Either:
#:
#: * ``"ask"`` (or absent) — prompt every time the user edits a
#:   RECONCILED transaction.
#: * ``"never_ask"`` — skip the prompt; go straight to the void+recreate
#:   flow.
RECONCILED_EDIT_CONFIRM_KEY: Final[str] = "reconciled_edit_confirm"

_ENV_PATH: Final[str] = "TULIP_PREFERENCES_FILE"


def default_preferences_path() -> Path:
    """Return the XDG-compliant default location of the prefs file."""
    override = os.environ.get(_ENV_PATH)
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "tulip" / "preferences.json"


def load_preferences(*, path: Path | None = None) -> dict[str, object]:
    """Read the preferences file; return ``{}`` when absent or malformed."""
    resolved = path or default_preferences_path()
    if not resolved.is_file():
        return {}
    try:
        raw = resolved.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        # Malformed prefs shouldn't make the CLI unusable; treat as
        # absent and let subsequent writes overwrite.
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def save_preferences(values: dict[str, object], *, path: Path | None = None) -> None:
    """Atomically write ``values`` to the preferences file (creates parent dirs)."""
    resolved = path or default_preferences_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    # Write to a sibling temp file then rename; rename is atomic on POSIX
    # so a crash mid-write leaves the previous contents intact rather
    # than truncating to empty.
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    tmp.write_text(json.dumps(values, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(resolved)
    # 0600: only the user can read the prefs file. Same posture as
    # the token store.
    try:
        os.chmod(resolved, 0o600)
    except OSError:
        # Windows / FS without POSIX modes — silently accept the
        # default umask.
        pass


def get_reconciled_edit_confirm(*, path: Path | None = None) -> ReconciledEditConfirm:
    """Return ``"ask"`` (default) or ``"never_ask"``."""
    prefs = load_preferences(path=path)
    value = prefs.get(RECONCILED_EDIT_CONFIRM_KEY)
    if isinstance(value, str) and value == "never_ask":
        return "never_ask"
    return "ask"


def set_reconciled_edit_confirm(value: str, *, path: Path | None = None) -> None:
    """Persist ``value`` (``"ask"`` or ``"never_ask"``)."""
    if value not in ("ask", "never_ask"):
        raise ValueError(f"invalid reconciled_edit_confirm value: {value!r}")
    prefs = load_preferences(path=path)
    if value == "ask":
        prefs.pop(RECONCILED_EDIT_CONFIRM_KEY, None)
    else:
        prefs[RECONCILED_EDIT_CONFIRM_KEY] = value
    save_preferences(prefs, path=path)


__all__: list[str] = [
    "RECONCILED_EDIT_CONFIRM_KEY",
    "ReconciledEditConfirm",
    "default_preferences_path",
    "get_reconciled_edit_confirm",
    "load_preferences",
    "save_preferences",
    "set_reconciled_edit_confirm",
]

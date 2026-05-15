"""Shared assertion for Typer usage / ``BadParameter`` CLI errors (#294).

Gotcha this exists to encode: Typer renders ``BadParameter`` and
missing-argument errors through its *own* internal Rich console, which
the CLI's ``make_console`` factory (#285) never reaches. CI runs with a
narrower terminal than local dev, so any test asserting a substring on
the *body* of a Typer error panel is fragile — the panel line-wraps and
truncates mid-word, dropping the substring.

The width-stable contract is the non-zero exit code plus the ``usage``
banner Typer prints below the panel. Assert on those. Pass ``contains``
only for substrings short enough to survive CI's panel truncation.
"""

from __future__ import annotations

import re
import subprocess

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def assert_cli_usage_error(
    result: subprocess.CompletedProcess[str],
    *,
    contains: str | None = None,
) -> None:
    """Assert a CLI invocation failed as a Typer usage error.

    Checks a non-zero exit code and the width-stable ``usage`` banner.
    ``contains`` is an *optional* extra substring check, applied against
    the ANSI-stripped, lower-cased combined output — pass it only when
    the substring is short enough to survive CI's panel truncation.
    """
    combined = _ANSI_RE.sub("", result.stdout + result.stderr).lower()
    assert result.returncode != 0, (
        f"expected a non-zero exit code, got {result.returncode}\n{combined}"
    )
    assert "usage" in combined, f"expected a 'usage' banner in the CLI output:\n{combined}"
    if contains is not None:
        needle = contains.lower()
        assert needle in combined, f"expected {needle!r} in the CLI output:\n{combined}"

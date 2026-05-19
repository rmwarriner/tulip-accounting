"""Pin the help text for ``--account`` surfaces (#416).

The CLI's input-side resolver (#197) has always accepted UUID, code,
name, and hierarchical paths — but the ``help=`` strings on every
surface said only "code or UUID," making the path form invisible to
users. This snapshot pins the corrected phrasing across the four most-
used surfaces so a future drift away from the new vocabulary fails
CI rather than silently regressing the discoverability fix.

A pinned phrase, not a full ``--help`` snapshot: ``--help`` output
includes terminal-width-dependent wrapping that's brittle to pin in
full.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

_PHRASE = "UUID, code, name, or hierarchical path"


@pytest.mark.parametrize(
    "command",
    [
        ["balance", "--help"],
        ["accounts", "show", "--help"],
        ["accounts", "edit", "--help"],
        ["accounts", "deactivate", "--help"],
        ["transactions", "list", "--help"],
        ["reconcile", "create", "--help"],
        ["reconcile", "list", "--help"],
        ["imports", "ofx", "--help"],
        ["imports", "list", "--help"],
    ],
)
def test_help_mentions_path_form(command: list[str]) -> None:
    """Every ``--account`` / ``ACCOUNT`` surface advertises path input."""
    result = subprocess.run(
        [sys.executable, "-m", "tulip_cli", *command],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={"COLUMNS": "200", "PATH": ""},
    )
    assert result.returncode == 0, result.stderr
    assert _PHRASE in result.stdout, (
        f"`tulip {' '.join(command)}` help missing the path phrase. "
        "If you intentionally reshaped the help text, update _PHRASE in "
        "this test to the new canonical form so it stays pinned."
    )

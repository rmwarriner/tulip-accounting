"""Smoke test: invoke the installed `tulip` console script and confirm it runs."""

from __future__ import annotations

import subprocess
import sys


def test_tulip_help_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tulip_cli", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "tulip" in result.stdout.lower()


def test_tulip_version_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tulip_cli", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()

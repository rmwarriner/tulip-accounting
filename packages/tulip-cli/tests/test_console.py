"""Tests for the COLUMNS-honoring Rich ``Console`` factory (#285).

Rich only reads ``COLUMNS`` off a TTY; ``make_console`` threads it
through explicitly so piped / subprocess output (CI, the test harness)
renders at the requested width instead of a fixed 80 columns.
"""

from __future__ import annotations

import pytest

from tulip_cli._console import make_console


@pytest.mark.parametrize("columns", ["120", "160", "240"])
def test_make_console_honors_columns_env(monkeypatch: pytest.MonkeyPatch, columns: str) -> None:
    monkeypatch.setenv("COLUMNS", columns)
    assert make_console().width == int(columns)


def test_make_console_columns_changes_rendered_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The acceptance check from #285: COLUMNS=N actually changes width."""
    monkeypatch.setenv("COLUMNS", "160")
    narrow = make_console()
    monkeypatch.setenv("COLUMNS", "240")
    wide = make_console()
    assert narrow.width == 160
    assert wide.width == 240
    assert narrow.width != wide.width


def test_make_console_unset_columns_falls_back_to_autodetect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COLUMNS", raising=False)
    # No explicit width forced — Rich auto-detects (80 off a TTY-less
    # pipe). We only assert we didn't crash and produced a usable width.
    assert make_console().width > 0


def test_make_console_zero_or_negative_columns_uses_safe_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # COLUMNS=0 is real (some CI shells export it). Rich's own auto-detect
    # would honor it and render at width 0, so the helper must *override*
    # a junk COLUMNS with a safe default rather than just declining to
    # force a width.
    from tulip_cli._console import _SAFE_DEFAULT_WIDTH

    for bad in ("0", "-40", "not-a-number"):
        monkeypatch.setenv("COLUMNS", bad)
        assert make_console().width == _SAFE_DEFAULT_WIDTH


def test_make_console_stderr_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COLUMNS", raising=False)
    assert make_console(stderr=True).stderr is True
    assert make_console().stderr is False

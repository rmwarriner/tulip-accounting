"""Unit tests for the shared interactive picker (#273).

Covers the pure helper in :mod:`tulip_cli._picker`. The integration of
the picker into individual commands is exercised by the per-command
tests (``test_import_command``, ``test_reconcile_command``,
``test_transactions_picker``); here we drive every branch of the
picker itself without spawning a subprocess.
"""

from __future__ import annotations

import io

import pytest

from tulip_cli._picker import PICKER_MAX_ENTRIES, is_interactive, pick


def _rows(n: int) -> list[dict[str, str]]:
    return [{"id": f"id-{i:02d}", "label": f"row-{i:02d}"} for i in range(n)]


def _label(row: dict[str, str]) -> str:
    return row["label"]


def test_pick_returns_selected_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Numeric selection returns the matching row's id."""
    monkeypatch.setattr("sys.stdin", io.StringIO("2\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    picked = pick(
        _rows(3),
        label=_label,
        title="Pick:",
        empty_message="empty",
        overflow_hint="too many",
    )
    assert picked == "id-01"


def test_pick_returns_none_on_explicit_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Typing ``c`` cancels the picker; the helper returns ``None``."""
    monkeypatch.setattr("sys.stdin", io.StringIO("c\n"))
    picked = pick(
        _rows(3),
        label=_label,
        title="Pick:",
        empty_message="empty",
        overflow_hint="too many",
    )
    assert picked is None


def test_pick_returns_none_on_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare Enter (default ``c``) cancels."""
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    picked = pick(
        _rows(3),
        label=_label,
        title="Pick:",
        empty_message="empty",
        overflow_hint="too many",
    )
    assert picked is None


def test_pick_returns_none_on_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    """EOF during the prompt cancels (typer raises Abort on click EOF)."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # nothing to read
    picked = pick(
        _rows(3),
        label=_label,
        title="Pick:",
        empty_message="empty",
        overflow_hint="too many",
    )
    assert picked is None


def test_pick_returns_none_on_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """A number outside ``[1, len(items)]`` cancels rather than crashing."""
    monkeypatch.setattr("sys.stdin", io.StringIO("99\n"))
    picked = pick(
        _rows(3),
        label=_label,
        title="Pick:",
        empty_message="empty",
        overflow_hint="too many",
    )
    assert picked is None


def test_pick_returns_none_on_non_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-numeric, non-cancel input cancels with a diagnostic."""
    monkeypatch.setattr("sys.stdin", io.StringIO("zzz\n"))
    picked = pick(
        _rows(3),
        label=_label,
        title="Pick:",
        empty_message="empty",
        overflow_hint="too many",
    )
    assert picked is None


def test_pick_returns_none_on_empty_list(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty input list short-circuits to ``None`` without prompting."""
    monkeypatch.setattr("sys.stdin", io.StringIO("1\n"))  # would pick if prompted
    picked = pick(
        [],
        label=_label,
        title="Pick:",
        empty_message="nothing actionable",
        overflow_hint="too many",
    )
    assert picked is None
    captured = capsys.readouterr()
    assert "nothing actionable" in captured.err


def test_pick_caps_choices_at_20(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Lists longer than ``PICKER_MAX_ENTRIES`` render the overflow hint."""
    monkeypatch.setattr("sys.stdin", io.StringIO("c\n"))
    big = _rows(PICKER_MAX_ENTRIES + 5)
    pick(
        big,
        label=_label,
        title="Pick:",
        empty_message="empty",
        overflow_hint="narrow with --account",
    )
    captured = capsys.readouterr()
    # Only the first PICKER_MAX_ENTRIES rows render.
    assert "row-00" in captured.err
    assert "row-19" in captured.err
    assert "row-20" not in captured.err
    assert "narrow with --account" in captured.err


def test_is_interactive_reflects_isatty(monkeypatch: pytest.MonkeyPatch) -> None:
    """``is_interactive`` is just ``sys.stdin.isatty()``."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    assert is_interactive() is True
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    assert is_interactive() is False


def test_pick_renders_label_for_each_row(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Caller-supplied label is invoked once per rendered row."""
    monkeypatch.setattr("sys.stdin", io.StringIO("c\n"))
    seen_ids: list[str] = []

    def _label_record(row: dict[str, str]) -> str:
        seen_ids.append(row["id"])
        return f"<{row['id']}>"

    pick(
        _rows(3),
        label=_label_record,
        title="Pick:",
        empty_message="empty",
        overflow_hint="narrow",
    )
    assert seen_ids == ["id-00", "id-01", "id-02"]
    captured = capsys.readouterr()
    assert "<id-00>" in captured.err
    assert "<id-02>" in captured.err

"""Tests for the editor-spawn helper used by ``tulip add --edit``."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tulip_cli.commands import _editor


def _make_fake_editor(tmp_path: Path, output: str) -> Path:
    """Write a python script that overwrites argv[1] with ``output``."""
    script = tmp_path / "fake_editor.py"
    script.write_text(f"import sys, pathlib\npathlib.Path(sys.argv[1]).write_text({output!r})\n")
    return script


def test_edit_buffer_returns_what_the_editor_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _make_fake_editor(tmp_path, "edited content\n")
    monkeypatch.setenv("EDITOR", f"{sys.executable} {script}")
    monkeypatch.delenv("VISUAL", raising=False)

    result = _editor.edit_buffer("initial content\n")
    assert result == "edited content\n"


def test_visual_overrides_editor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    visual_script = tmp_path / "visual.py"
    visual_script.write_text(
        "import sys, pathlib\npathlib.Path(sys.argv[1]).write_text('from-visual\\n')\n"
    )
    other_script = tmp_path / "other.py"
    other_script.write_text("raise SystemExit('EDITOR should not run')\n")

    monkeypatch.setenv("VISUAL", f"{sys.executable} {visual_script}")
    monkeypatch.setenv("EDITOR", f"{sys.executable} {other_script}")

    result = _editor.edit_buffer("ignored")
    assert result == "from-visual\n"


def test_temp_file_is_cleaned_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The temp file must be removed even on the happy path."""
    script = tmp_path / "fake_editor.py"
    script.write_text(
        "import sys, pathlib, os\n"
        "p = pathlib.Path(sys.argv[1])\n"
        "with open(os.environ['CAPTURE_PATH'], 'w') as f: f.write(str(p))\n"
        "p.write_text('done\\n')\n"
    )
    capture = tmp_path / "capture.txt"
    monkeypatch.setenv("EDITOR", f"{sys.executable} {script}")
    monkeypatch.setenv("CAPTURE_PATH", str(capture))
    monkeypatch.delenv("VISUAL", raising=False)

    _editor.edit_buffer("initial\n")
    captured_path = Path(capture.read_text().strip())
    assert not captured_path.exists(), "temp file should be deleted after edit"


def test_resolve_editor_command_handles_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``EDITOR='code --wait'`` should split into argv tokens."""
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "code --wait --foo")
    cmd = _editor._resolve_editor_command()
    assert cmd == ["code", "--wait", "--foo"]


def test_resolve_editor_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    cmd = _editor._resolve_editor_command()
    assert cmd  # at least one token; exact value depends on platform

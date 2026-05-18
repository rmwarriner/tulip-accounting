"""Unit tests for ``tulip_cli._preferences`` (#209b storage half)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tulip_cli._preferences import (
    RECONCILED_EDIT_CONFIRM_KEY,
    default_preferences_path,
    get_reconciled_edit_confirm,
    load_preferences,
    save_preferences,
    set_reconciled_edit_confirm,
)


def test_default_path_honours_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``TULIP_PREFERENCES_FILE`` overrides the XDG default."""
    target = tmp_path / "prefs.json"
    monkeypatch.setenv("TULIP_PREFERENCES_FILE", str(target))
    assert default_preferences_path() == target


def test_default_path_under_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No env override → ``$XDG_CONFIG_HOME/tulip/preferences.json``."""
    monkeypatch.delenv("TULIP_PREFERENCES_FILE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_preferences_path() == tmp_path / "tulip" / "preferences.json"


def test_load_returns_empty_when_file_absent(tmp_path: Path) -> None:
    """Missing file → empty dict (no crash, no exception)."""
    assert load_preferences(path=tmp_path / "absent.json") == {}


def test_load_returns_empty_on_malformed_json(tmp_path: Path) -> None:
    """Malformed JSON → empty dict (forward-compatible degraded mode)."""
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert load_preferences(path=bad) == {}


def test_load_returns_empty_when_root_not_object(tmp_path: Path) -> None:
    """JSON arrays / scalars at root → empty dict; CLI never crashes on shape."""
    bad = tmp_path / "list.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_preferences(path=bad) == {}


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    """``save_preferences`` writes JSON that ``load_preferences`` reads back."""
    target = tmp_path / "prefs.json"
    save_preferences({"foo": "bar"}, path=target)
    assert load_preferences(path=target) == {"foo": "bar"}


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    """``save_preferences`` mkdirs intermediate directories."""
    target = tmp_path / "deep" / "nested" / "prefs.json"
    save_preferences({"a": 1}, path=target)
    assert target.is_file()


def test_save_uses_atomic_rename(tmp_path: Path) -> None:
    """Writing twice doesn't leave the ``.tmp`` sidecar."""
    target = tmp_path / "prefs.json"
    save_preferences({"k": "v1"}, path=target)
    save_preferences({"k": "v2"}, path=target)
    assert load_preferences(path=target) == {"k": "v2"}
    assert not target.with_suffix(target.suffix + ".tmp").exists()


def test_save_sets_owner_read_write_only(tmp_path: Path) -> None:
    """File mode is ``0600`` on POSIX (token-store-equivalent posture)."""
    target = tmp_path / "prefs.json"
    save_preferences({"k": "v"}, path=target)
    import os
    import stat

    mode = stat.S_IMODE(os.stat(target).st_mode)
    # On macOS / Linux this lands exactly at 0o600. On Windows we
    # cannot guarantee POSIX-style modes, so loosen the assertion
    # there (the chmod silently no-ops by design).
    if os.name == "posix":
        assert mode == 0o600


def test_get_reconciled_edit_confirm_defaults_to_ask(tmp_path: Path) -> None:
    """Absent prefs file → ``"ask"`` (prompt every time, conservative default)."""
    assert get_reconciled_edit_confirm(path=tmp_path / "absent.json") == "ask"


def test_get_reconciled_edit_confirm_reads_never_ask(tmp_path: Path) -> None:
    """A persisted ``"never_ask"`` value is honored."""
    target = tmp_path / "prefs.json"
    save_preferences({RECONCILED_EDIT_CONFIRM_KEY: "never_ask"}, path=target)
    assert get_reconciled_edit_confirm(path=target) == "never_ask"


def test_get_reconciled_edit_confirm_ignores_unknown_value(tmp_path: Path) -> None:
    """An unknown / corrupted value falls back to ``"ask"`` rather than crashing."""
    target = tmp_path / "prefs.json"
    save_preferences({RECONCILED_EDIT_CONFIRM_KEY: "garbage"}, path=target)
    assert get_reconciled_edit_confirm(path=target) == "ask"


def test_set_reconciled_edit_confirm_persists_never_ask(tmp_path: Path) -> None:
    """``set(..., "never_ask")`` writes the key; round-trips via ``get``."""
    target = tmp_path / "prefs.json"
    set_reconciled_edit_confirm("never_ask", path=target)
    assert get_reconciled_edit_confirm(path=target) == "never_ask"


def test_set_reconciled_edit_confirm_ask_removes_key(tmp_path: Path) -> None:
    """``set(..., "ask")`` removes the key — default state, no on-disk entry."""
    target = tmp_path / "prefs.json"
    set_reconciled_edit_confirm("never_ask", path=target)
    set_reconciled_edit_confirm("ask", path=target)
    assert RECONCILED_EDIT_CONFIRM_KEY not in load_preferences(path=target)


def test_set_reconciled_edit_confirm_preserves_unknown_keys(tmp_path: Path) -> None:
    """Forward-compatibility: unknown keys round-trip across version updates."""
    target = tmp_path / "prefs.json"
    save_preferences({"future_key": "future_value"}, path=target)
    set_reconciled_edit_confirm("never_ask", path=target)
    prefs = load_preferences(path=target)
    assert prefs["future_key"] == "future_value"


def test_set_reconciled_edit_confirm_rejects_invalid_value(tmp_path: Path) -> None:
    """Bad values raise rather than silently accept."""
    with pytest.raises(ValueError, match="invalid"):
        set_reconciled_edit_confirm("maybe", path=tmp_path / "prefs.json")

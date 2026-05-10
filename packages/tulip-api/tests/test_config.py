"""Tests for tulip_api.config.Settings — focused on the master_key wiring.

The JWT-secret behavior is exercised indirectly by every auth test; this
file covers master_key, which is new and has no other coverage path yet.
"""

from __future__ import annotations

import base64
from unittest.mock import patch

import pytest

from tulip_api.config import Settings


class TestMasterKey:
    def test_reads_from_env_base64(self, monkeypatch: pytest.MonkeyPatch):
        raw = b"\x01" * 32
        monkeypatch.setenv("TULIP_MASTER_KEY", base64.b64encode(raw).decode("ascii"))
        s = Settings()
        assert s.master_key == raw

    def test_rejects_wrong_key_length(self, monkeypatch: pytest.MonkeyPatch):
        # 16 bytes is a valid AES-128 key but not what we want.
        monkeypatch.setenv("TULIP_MASTER_KEY", base64.b64encode(b"\x01" * 16).decode("ascii"))
        with pytest.raises(ValueError, match="32 bytes"):
            Settings()

    def test_rejects_non_base64(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TULIP_MASTER_KEY", "not-base64!!!")
        with pytest.raises(ValueError, match="base64"):
            Settings()

    def test_ephemeral_fallback_warns(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("TULIP_MASTER_KEY", raising=False)
        with patch("tulip_api.config.log") as mock_log:
            s = Settings()
        assert len(s.master_key) == 32
        mock_log.warning.assert_called_once()
        msg = mock_log.warning.call_args.args[0].lower()
        assert "ephemeral" in msg and "master" in msg

    def test_ephemeral_keys_differ_per_construction(self, monkeypatch: pytest.MonkeyPatch):
        # Each construction generates a fresh key — that's what makes it
        # production-unsafe and worth warning about.
        monkeypatch.delenv("TULIP_MASTER_KEY", raising=False)
        a = Settings()
        b = Settings()
        assert a.master_key != b.master_key


class TestMasterKeyFile:
    """``$TULIP_KEY_FILE`` file-based key store (#132 / #121 hardening).

    Locked design decisions:
    - Env var TULIP_KEY_FILE points at a file containing base64-encoded 32 bytes.
    - File mode must be 0600 (or stricter); any group/other read bit refuses boot.
    - TULIP_MASTER_KEY (if set) takes precedence — file is the fallback for
      the env var, ephemeral is the fallback for both.
    """

    def _write_key(self, tmp_path, raw: bytes, *, mode: int = 0o600):
        from pathlib import Path

        key_file = Path(tmp_path) / "master.key"
        key_file.write_text(base64.b64encode(raw).decode("ascii"))
        key_file.chmod(mode)
        return key_file

    def test_loads_from_file_with_strict_mode(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.delenv("TULIP_MASTER_KEY", raising=False)
        raw = b"\x02" * 32
        key_file = self._write_key(tmp_path, raw, mode=0o600)
        monkeypatch.setenv("TULIP_KEY_FILE", str(key_file))
        s = Settings()
        assert s.master_key == raw

    def test_refuses_world_readable_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.delenv("TULIP_MASTER_KEY", raising=False)
        key_file = self._write_key(tmp_path, b"\x03" * 32, mode=0o644)
        monkeypatch.setenv("TULIP_KEY_FILE", str(key_file))
        with pytest.raises(ValueError, match="0600"):
            Settings()

    def test_refuses_group_readable_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.delenv("TULIP_MASTER_KEY", raising=False)
        key_file = self._write_key(tmp_path, b"\x04" * 32, mode=0o640)
        monkeypatch.setenv("TULIP_KEY_FILE", str(key_file))
        with pytest.raises(ValueError, match="0600"):
            Settings()

    def test_refuses_bad_base64(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        from pathlib import Path

        monkeypatch.delenv("TULIP_MASTER_KEY", raising=False)
        key_file = Path(tmp_path) / "master.key"
        key_file.write_text("not-base64!!!")
        key_file.chmod(0o600)
        monkeypatch.setenv("TULIP_KEY_FILE", str(key_file))
        with pytest.raises(ValueError, match="base64"):
            Settings()

    def test_refuses_wrong_length(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.delenv("TULIP_MASTER_KEY", raising=False)
        key_file = self._write_key(tmp_path, b"\x05" * 16, mode=0o600)
        monkeypatch.setenv("TULIP_KEY_FILE", str(key_file))
        with pytest.raises(ValueError, match="32 bytes"):
            Settings()

    def test_refuses_missing_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.delenv("TULIP_MASTER_KEY", raising=False)
        monkeypatch.setenv("TULIP_KEY_FILE", str(tmp_path / "does-not-exist.key"))
        with pytest.raises(ValueError, match=r"not found|does not exist"):
            Settings()

    def test_env_var_wins_when_both_set(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        env_key = b"\x06" * 32
        file_key = b"\x07" * 32
        monkeypatch.setenv("TULIP_MASTER_KEY", base64.b64encode(env_key).decode("ascii"))
        key_file = self._write_key(tmp_path, file_key, mode=0o600)
        monkeypatch.setenv("TULIP_KEY_FILE", str(key_file))
        s = Settings()
        assert s.master_key == env_key  # env wins

    def test_neither_set_falls_back_to_ephemeral(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("TULIP_MASTER_KEY", raising=False)
        monkeypatch.delenv("TULIP_KEY_FILE", raising=False)
        with patch("tulip_api.config.log") as mock_log:
            s = Settings()
        assert len(s.master_key) == 32
        mock_log.warning.assert_called_once()


class TestMasterKeySource:
    """``Settings.master_key_source`` reports which env path was used (#135).

    Surfaced through ``GET /v1/system/diagnostics`` so the doctor CLI can
    flag the ephemeral fallback as a hard failure.
    """

    def test_env_source_when_master_key_env_var_set(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TULIP_MASTER_KEY", base64.b64encode(b"\x01" * 32).decode("ascii"))
        assert Settings().master_key_source == "env"

    def test_file_source_when_only_key_file_set(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        from pathlib import Path

        monkeypatch.delenv("TULIP_MASTER_KEY", raising=False)
        key_file = Path(tmp_path) / "master.key"
        key_file.write_text(base64.b64encode(b"\x02" * 32).decode("ascii"))
        key_file.chmod(0o600)
        monkeypatch.setenv("TULIP_KEY_FILE", str(key_file))
        assert Settings().master_key_source == "file"

    def test_ephemeral_source_when_neither_set(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("TULIP_MASTER_KEY", raising=False)
        monkeypatch.delenv("TULIP_KEY_FILE", raising=False)
        with patch("tulip_api.config.log"):  # silence the warning
            assert Settings().master_key_source == "ephemeral"

    def test_env_source_wins_when_both_set(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        from pathlib import Path

        monkeypatch.setenv("TULIP_MASTER_KEY", base64.b64encode(b"\x06" * 32).decode("ascii"))
        key_file = Path(tmp_path) / "master.key"
        key_file.write_text(base64.b64encode(b"\x07" * 32).decode("ascii"))
        key_file.chmod(0o600)
        monkeypatch.setenv("TULIP_KEY_FILE", str(key_file))
        # Mirrors the resolution order: env wins over file for both
        # the bytes and the source label.
        assert Settings().master_key_source == "env"

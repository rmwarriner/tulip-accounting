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

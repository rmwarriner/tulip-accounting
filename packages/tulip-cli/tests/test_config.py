"""Tests for configuration loading.

Precedence: CLI flag > env var > config file > built-in default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tulip_cli.config import DEFAULT_API_URL, Config, load_config


def test_default_api_url_when_nothing_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TULIP_API_URL", raising=False)
    config = load_config(config_path=tmp_path / "missing.toml", api_url_override=None)
    assert config.api_url == DEFAULT_API_URL


def test_config_file_overrides_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TULIP_API_URL", raising=False)
    cfg = tmp_path / "config.toml"
    cfg.write_text('api_url = "https://file.example.com"\n', encoding="utf-8")
    config = load_config(config_path=cfg, api_url_override=None)
    assert config.api_url == "https://file.example.com"


def test_env_var_overrides_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('api_url = "https://file.example.com"\n', encoding="utf-8")
    monkeypatch.setenv("TULIP_API_URL", "https://env.example.com")
    config = load_config(config_path=cfg, api_url_override=None)
    assert config.api_url == "https://env.example.com"


def test_cli_override_beats_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('api_url = "https://file.example.com"\n', encoding="utf-8")
    monkeypatch.setenv("TULIP_API_URL", "https://env.example.com")
    config = load_config(config_path=cfg, api_url_override="https://flag.example.com")
    assert config.api_url == "https://flag.example.com"


def test_config_strips_trailing_slash() -> None:
    config = Config(api_url="https://api.example.com/")
    assert config.api_url == "https://api.example.com"

"""Configuration loading for the Tulip CLI.

Precedence: CLI flag > env var > config file > built-in default.

The on-disk config lives at ``~/.config/tulip/config.toml`` (XDG-compliant).
For now the only key is ``api_url``; future slices will add per-environment
profiles, default editor, output format, and the like.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

DEFAULT_API_URL: Final[str] = "http://127.0.0.1:8000"
ENV_API_URL: Final[str] = "TULIP_API_URL"


def default_config_path() -> Path:
    """Return the XDG-compliant default location of the CLI config file."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "tulip" / "config.toml"


@dataclass(frozen=True, slots=True)
class Config:
    """Resolved CLI configuration."""

    api_url: str

    def __post_init__(self) -> None:
        """Strip a trailing slash from ``api_url`` so URL joins are unambiguous."""
        cleaned = self.api_url.rstrip("/")
        if cleaned != self.api_url:
            object.__setattr__(self, "api_url", cleaned)


def _read_config_file(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_config(
    *,
    config_path: Path | None = None,
    api_url_override: str | None = None,
) -> Config:
    """Resolve a :class:`Config` from flag, env, file, and default in that order."""
    file_data = _read_config_file(config_path or default_config_path())
    api_url = (
        api_url_override
        or os.environ.get(ENV_API_URL)
        or _str_or_none(file_data.get("api_url"))
        or DEFAULT_API_URL
    )
    return Config(api_url=api_url)


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None

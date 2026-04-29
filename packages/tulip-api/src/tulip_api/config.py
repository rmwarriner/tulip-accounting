"""Runtime configuration."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field


def _default_jwt_secret() -> str:
    return os.environ.get("TULIP_JWT_SECRET") or secrets.token_urlsafe(48)


def _default_db_url() -> str:
    return os.environ.get("TULIP_DATABASE_URL", "sqlite:///./tulip.db")


@dataclass(frozen=True, slots=True)
class Settings:
    """Read-only runtime settings."""

    database_url: str = field(default_factory=_default_db_url)
    jwt_secret: str = field(default_factory=_default_jwt_secret)


_SINGLETON: Settings | None = None


def get_settings() -> Settings:
    """Return the process-global Settings (or build one from env on first call)."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = Settings()
    return _SINGLETON

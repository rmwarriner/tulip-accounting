"""Runtime configuration."""

from __future__ import annotations

import base64
import binascii
import logging
import os
import secrets
from dataclasses import dataclass, field

log = logging.getLogger("tulip_api.config")


def _default_jwt_secret() -> str:
    return os.environ.get("TULIP_JWT_SECRET") or secrets.token_urlsafe(48)


def _default_db_url() -> str:
    return os.environ.get("TULIP_DATABASE_URL", "sqlite:///./tulip.db")


def _default_master_key() -> bytes:
    """Resolve the field-encryption master key.

    Reads ``TULIP_MASTER_KEY`` (base64-encoded 32 bytes). If unset, falls
    back to a freshly generated ephemeral key and logs a warning — fine
    for tests and dev, fatal for prod (existing TOTP secrets become
    undecryptable on every restart).
    """
    raw = os.environ.get("TULIP_MASTER_KEY")
    if raw is None:
        log.warning(
            "TULIP_MASTER_KEY not set; using an ephemeral master key. "
            "Field-encrypted data (e.g. TOTP secrets) will not survive a process restart."
        )
        return secrets.token_bytes(32)
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("TULIP_MASTER_KEY must be valid base64 (32 raw bytes encoded).") from exc
    if len(decoded) != 32:
        raise ValueError(f"TULIP_MASTER_KEY must decode to exactly 32 bytes (got {len(decoded)}).")
    return decoded


@dataclass(frozen=True, slots=True)
class Settings:
    """Read-only runtime settings."""

    database_url: str = field(default_factory=_default_db_url)
    jwt_secret: str = field(default_factory=_default_jwt_secret)
    master_key: bytes = field(default_factory=_default_master_key)


_SINGLETON: Settings | None = None


def get_settings() -> Settings:
    """Return the process-global Settings (or build one from env on first call)."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = Settings()
    return _SINGLETON

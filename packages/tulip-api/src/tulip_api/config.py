"""Runtime configuration."""

from __future__ import annotations

import base64
import binascii
import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("tulip_api.config")


def _default_jwt_secret() -> str:
    return os.environ.get("TULIP_JWT_SECRET") or secrets.token_urlsafe(48)


def _default_db_url() -> str:
    return os.environ.get("TULIP_DATABASE_URL", "sqlite:///./tulip.db")


def _default_attachment_root() -> Path:
    """Resolve the on-disk root for encrypted import-file attachments (P5.1).

    Reads ``TULIP_ATTACHMENT_ROOT`` (an absolute path) or defaults to
    ``~/.local/share/tulip/attachments`` per ADR-0004 §Q9. The directory
    is created on first use; the bytes stored under it are encrypted with
    the same master key used for ``transactions.notes_encrypted`` and
    ``users.totp_secret_encrypted``.
    """
    raw = os.environ.get("TULIP_ATTACHMENT_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / ".local" / "share" / "tulip" / "attachments"


def _decode_key_bytes(raw: str, *, source: str) -> bytes:
    """Decode a base64 string into a 32-byte key, with typed errors.

    ``source`` is a human-readable origin used in the error message
    (e.g. ``TULIP_MASTER_KEY`` or ``$TULIP_KEY_FILE``).
    """
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{source} must be valid base64 (32 raw bytes encoded).") from exc
    if len(decoded) != 32:
        raise ValueError(f"{source} must decode to exactly 32 bytes (got {len(decoded)}).")
    return decoded


def _load_master_key_from_file(path: Path) -> bytes:
    """Load the master key from a file, refusing world/group-readable modes.

    Per ADR / #132 hardening: the file mode must be 0600 (owner-only RW).
    Any group/other read or write bit refuses boot — internal-beta deploys
    must keep the key off shared filesystems.
    """
    if not path.exists():
        raise ValueError(f"$TULIP_KEY_FILE points at {path}, but that file was not found.")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise ValueError(
            f"$TULIP_KEY_FILE {path} has mode {mode:#o}; group/other access "
            "is forbidden. Run `chmod 0600 {path}` and retry."
        )
    contents = path.read_text(encoding="ascii").strip()
    return _decode_key_bytes(contents, source=f"$TULIP_KEY_FILE ({path})")


def _default_master_key() -> bytes:
    """Resolve the field-encryption master key.

    Resolution order:

    1. ``TULIP_MASTER_KEY`` (base64-encoded 32 bytes) wins if set.
    2. ``TULIP_KEY_FILE`` (path to a 0600 file containing base64-encoded
       32 bytes) is the second choice. Per #132 hardening: the file mode
       gate refuses boot on a world/group-readable file.
    3. Fall back to a freshly generated ephemeral key with a warning.
       Fine for tests and dev; fatal for prod (existing TOTP secrets
       become undecryptable on every restart).
    """
    raw = os.environ.get("TULIP_MASTER_KEY")
    if raw is not None:
        return _decode_key_bytes(raw, source="TULIP_MASTER_KEY")

    file_path = os.environ.get("TULIP_KEY_FILE")
    if file_path:
        return _load_master_key_from_file(Path(file_path).expanduser())

    log.warning(
        "Neither TULIP_MASTER_KEY nor TULIP_KEY_FILE is set; using an ephemeral "
        "master key. Field-encrypted data (e.g. TOTP secrets) will not survive "
        "a process restart."
    )
    return secrets.token_bytes(32)


@dataclass(frozen=True, slots=True)
class Settings:
    """Read-only runtime settings."""

    database_url: str = field(default_factory=_default_db_url)
    jwt_secret: str = field(default_factory=_default_jwt_secret)
    master_key: bytes = field(default_factory=_default_master_key)
    attachment_root: Path = field(default_factory=_default_attachment_root)


_SINGLETON: Settings | None = None


def get_settings() -> Settings:
    """Return the process-global Settings (or build one from env on first call)."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = Settings()
    return _SINGLETON

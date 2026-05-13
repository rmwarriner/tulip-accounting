"""Runtime configuration."""

from __future__ import annotations

import base64
import binascii
import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

log = logging.getLogger("tulip_api.config")

#: Where the master key came from. Surfaced in ``GET /v1/system/diagnostics``
#: so the doctor CLI can flag ephemeral fallback as a hard failure (#135).
MasterKeySource = Literal["env", "file", "ephemeral"]
#: Where the JWT secret came from. Parallels ``MasterKeySource`` (#223).
JwtSecretSource = Literal["env", "ephemeral"]
#: Deployment mode. ``prod`` refuses to boot with any ephemeral secret;
#: ``dev`` warns but allows. Default ``dev`` so existing test + local
#: workflows are unchanged (#223).
DeploymentMode = Literal["dev", "prod"]


def _default_jwt_secret() -> str:
    raw = os.environ.get("TULIP_JWT_SECRET")
    if raw is not None:
        return raw
    # Mirror the master-key warning shape — silent fallback was the #223
    # M-3 finding: operators forgot to set the env var and lost every
    # outstanding access token on the next restart with no signal.
    log.warning(
        "TULIP_JWT_SECRET not set; generating an ephemeral 48-byte secret. "
        "Every restart invalidates all outstanding access tokens.",
    )
    return secrets.token_urlsafe(48)


def _default_jwt_secret_source() -> JwtSecretSource:
    """Pure inspection of which env path produced the JWT secret."""
    if os.environ.get("TULIP_JWT_SECRET") is not None:
        return "env"
    return "ephemeral"


def _default_deployment_mode() -> DeploymentMode:
    """Read ``TULIP_ENV`` (``dev`` | ``prod``). Default ``dev``.

    Used by :class:`Settings` to gate on ephemeral-secret refusal.
    """
    raw = os.environ.get("TULIP_ENV", "dev").strip().lower()
    if raw in ("prod", "production"):
        return "prod"
    return "dev"


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


def _default_master_key_source() -> MasterKeySource:
    """Side-effect-free peek at which env source is configured.

    Mirrors the resolution order in :func:`_default_master_key` but does
    not load files, decode keys, or emit warnings — it's a pure inspection
    of env vars. Side effects belong to ``_default_master_key`` so they
    fire exactly once per ``Settings()`` construction.
    """
    if os.environ.get("TULIP_MASTER_KEY") is not None:
        return "env"
    if os.environ.get("TULIP_KEY_FILE"):
        return "file"
    return "ephemeral"


@dataclass(frozen=True, slots=True)
class Settings:
    """Read-only runtime settings.

    Boots silently in ``dev`` mode (the default) even when secrets fall
    back to ephemeral values. In ``prod`` mode (``TULIP_ENV=prod``) the
    constructor refuses to materialise a Settings instance if either the
    master key or the JWT secret is ephemeral — see #223 (M-2 + M-3).
    """

    database_url: str = field(default_factory=_default_db_url)
    jwt_secret: str = field(default_factory=_default_jwt_secret)
    jwt_secret_source: JwtSecretSource = field(default_factory=_default_jwt_secret_source)
    master_key: bytes = field(default_factory=_default_master_key)
    master_key_source: MasterKeySource = field(default_factory=_default_master_key_source)
    attachment_root: Path = field(default_factory=_default_attachment_root)
    deployment_mode: DeploymentMode = field(default_factory=_default_deployment_mode)

    def __post_init__(self) -> None:
        """Refuse boot in prod mode when any secret is ephemeral (#223)."""
        if self.deployment_mode != "prod":
            return
        if self.master_key_source == "ephemeral":
            raise RuntimeError(
                "TULIP_ENV=prod: refusing to boot with an ephemeral master key. "
                "Set TULIP_MASTER_KEY or TULIP_KEY_FILE before starting the API.",
            )
        if self.jwt_secret_source == "ephemeral":  # noqa: S105 — enum-value compare, not a credential
            raise RuntimeError(
                "TULIP_ENV=prod: refusing to boot with an ephemeral JWT secret. "
                "Set TULIP_JWT_SECRET before starting the API.",
            )


_SINGLETON: Settings | None = None


def get_settings() -> Settings:
    """Return the process-global Settings (or build one from env on first call)."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = Settings()
    return _SINGLETON

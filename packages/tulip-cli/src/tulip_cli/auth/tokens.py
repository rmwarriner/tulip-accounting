"""Persistent storage for the access + refresh tokens issued by the Tulip API.

Two backends:

* **keyring** (default) — uses the OS keychain via the ``keyring``
  library. Tokens never appear on disk in plaintext. The right choice
  for real users.
* **file** (``TULIP_TOKEN_STORE`` env var pointing at a path) — writes
  tokens to a JSON file at the named path. **Unencrypted.** Intended
  for tests and CI; not for real-user use. Documented as such in
  ``docs/CLI.md`` (when that exists).

The keyring entry is keyed by the API base URL so multiple tenants /
environments can coexist without the CLI getting confused. Pluggable
secret-tool backends (``1Password``, ``pass``, etc.) are tracked
separately in #28.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

import keyring
import keyring.errors

_KEYRING_SERVICE: Final[str] = "tulip-accounting"
_ENV_TOKEN_STORE: Final[str] = "TULIP_TOKEN_STORE"  # noqa: S105 — env var name, not a credential


class TokenStoreError(RuntimeError):
    """Raised when the token store backend is unusable (e.g. no keyring service).

    The CLI surfaces this with operator guidance rather than the underlying
    library traceback. See #227.
    """


_KEYRING_GUIDANCE: Final[str] = (
    "No usable OS keyring service was found. On Linux install "
    "`libsecret`/`gnome-keyring`; on macOS / Windows the OS provides one. "
    "For tests only you can set TULIP_TOKEN_STORE to a file path."
)


@dataclass(frozen=True, slots=True)
class TokenSet:
    """The data the CLI persists after a successful login."""

    email: str
    access_token: str
    refresh_token: str
    access_expires_at: int


def _normalize(api_url: str) -> str:
    return api_url.rstrip("/")


class TokenStore:
    """Read/write/clear tokens keyed by API URL.

    Construct with ``file_path=...`` to use the JSON-file backend (tests).
    Without it, falls through to the OS keyring.
    """

    def __init__(self, *, file_path: Path | None = None) -> None:
        """Build a store using either the file backend (if a path is given) or keyring."""
        self._file_path = file_path

    @property
    def is_keyring_backed(self) -> bool:
        """Return whether this store writes to the OS keyring."""
        return self._file_path is None

    def save(self, api_url: str, tokens: TokenSet) -> None:
        """Persist ``tokens`` for ``api_url``, replacing any existing entry.

        Raises ``TokenStoreError`` when the keyring backend is unavailable
        (e.g. headless Linux without `dbus`/`secret-service`).
        """
        url = _normalize(api_url)
        payload = json.dumps(asdict(tokens))
        if self._file_path is not None:
            data = self._read_file()
            data[url] = payload
            self._write_file(data)
        else:
            try:
                keyring.set_password(_KEYRING_SERVICE, url, payload)
            except keyring.errors.NoKeyringError as exc:
                raise TokenStoreError(_KEYRING_GUIDANCE) from exc

    def load(self, api_url: str) -> TokenSet | None:
        """Return tokens for ``api_url`` if any are stored, else ``None``.

        Raises ``TokenStoreError`` when the keyring backend is unavailable.
        """
        url = _normalize(api_url)
        if self._file_path is not None:
            payload = self._read_file().get(url)
        else:
            try:
                payload = keyring.get_password(_KEYRING_SERVICE, url)
            except keyring.errors.NoKeyringError as exc:
                raise TokenStoreError(_KEYRING_GUIDANCE) from exc
        if not payload:
            return None
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        try:
            return TokenSet(**data)
        except TypeError:
            return None

    def clear(self, api_url: str) -> None:
        """Remove any stored tokens for ``api_url``. Idempotent — no error if absent.

        Raises ``TokenStoreError`` when the keyring backend is unavailable.
        """
        url = _normalize(api_url)
        if self._file_path is not None:
            data = self._read_file()
            data.pop(url, None)
            self._write_file(data)
        else:
            try:
                keyring.delete_password(_KEYRING_SERVICE, url)
            except keyring.errors.PasswordDeleteError:
                pass
            except keyring.errors.NoKeyringError as exc:
                raise TokenStoreError(_KEYRING_GUIDANCE) from exc

    def _read_file(self) -> dict[str, str]:
        if self._file_path is None or not self._file_path.is_file():
            return {}
        # #226: refuse to load if mode is loose. Mirrors the master-key-file
        # gate in tulip_api.config — operators should keep tokens off shared
        # filesystems. The JSON backend is documented as CI/tests only.
        mode = self._file_path.stat().st_mode & 0o777
        if mode & 0o077:
            raise TokenStoreError(
                f"token store {self._file_path} has mode {mode:#o}; "
                f"group/other access is forbidden. Run `chmod 0600 "
                f"{self._file_path}` and retry, or migrate to the keyring "
                "backend (unset TULIP_TOKEN_STORE).",
            )
        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_file(self, data: dict[str, str]) -> None:
        if self._file_path is None:
            return
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        # #226: atomic write + 0600 mode.
        #   * `os.open(..., O_CREAT|O_EXCL, 0o600)` would race with concurrent
        #     writers; use the tempfile-in-same-dir + os.replace pattern which
        #     is atomic on POSIX and tolerates concurrent overwrites.
        #   * 0o600 enforced via the open() mode + an explicit chmod (umask
        #     subtraction means open() alone is not sufficient on every host).
        tmp = self._file_path.with_suffix(self._file_path.suffix + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(data))
            os.chmod(tmp, 0o600)  # defensive against permissive umask
            os.replace(tmp, self._file_path)
        except Exception:
            # Best-effort cleanup of the half-written temp file.
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise


def default_token_store() -> TokenStore:
    """Build a ``TokenStore`` from environment configuration.

    ``TULIP_TOKEN_STORE`` set → file backend at that path.
    Unset → keyring-backed (default for real users).
    """
    env_path = os.environ.get(_ENV_TOKEN_STORE)
    if env_path:
        return TokenStore(file_path=Path(env_path))
    return TokenStore()

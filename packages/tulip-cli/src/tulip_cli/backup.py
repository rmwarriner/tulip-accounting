"""Backup + restore primitives for ``tulip backup`` / ``tulip restore`` (#133).

Internal-beta hardening (#121 Tier 1b). The backup format is a tar.gz
containing:

- ``manifest.json`` (top-level): format version, alembic head, Tulip
  version, hostname, ISO-8601 UTC timestamp, master-key envelope.
- ``db/tulip.db``: SQLite snapshot taken via the stdlib ``sqlite3``
  ``.backup`` API (concurrent-safe; the API process can stay running
  during backup).
- ``attachments/<...>``: full ``$TULIP_ATTACHMENT_ROOT`` tree, files
  preserved as-is (already field-encrypted via the master key).

The master-key envelope is HMAC-SHA256 of a fixed constant under the
master key. Restore recomputes the HMAC with the currently-loaded master
key; mismatch refuses extraction. The key itself is never written into
the backup — losing the backup file alone doesn't leak the key.

Per the locked design decision in #121: the tarball itself is *not*
re-encrypted. The high-confidentiality fields (TOTP secrets, attachment
bytes) are already field/file-encrypted; doubling up adds ergonomic
friction (need the key just to inspect a backup) for marginal gain.
Pre-external-beta we revisit.

The CLI imports stdlib (``sqlite3``, ``tarfile``, ``hmac``, ``hashlib``)
only — the architecture test forbids ``tulip_storage`` / ``sqlalchemy``
/ ``alembic`` in this package.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import platform
import socket
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

#: Bumped when the on-disk shape changes incompatibly. Restore refuses
#: a backup whose ``manifest.format_version`` is newer than this.
FORMAT_VERSION = 1

#: Fixed constant fed to HMAC-SHA256 under the master key. Verifies
#: "this backup was made with the same master key as the current install"
#: without storing the key in the backup.
ENVELOPE_INPUT = b"tulip-backup-key-verify-v1"

#: Top-level path inside the tarball.
_MANIFEST_NAME = "manifest.json"
_DB_PATH_PREFIX = "db/"
_ATTACHMENTS_PATH_PREFIX = "attachments/"


def write_backup_audit_rows(
    *,
    db_path: Path,
    action: str,
    metadata: dict[str, object],
) -> None:
    """Write one ``audit_log`` row per household via raw sqlite3 (#368, audit L-24).

    The CLI can't import SQLAlchemy (arch test), so this uses stdlib
    sqlite3 + hand-written SQL. Best-effort — a failure here logs and
    returns rather than blocking the backup itself.

    Targets the file at ``db_path``:
    - For ``tulip backup`` (action="backup.created"), this is the LIVE
      database — the audit row lands in the running app's DB and is
      included in the *next* backup.
    - For ``tulip restore`` (action="backup.restored"), this is the
      RESTORED database — the audit row joins the post-restore state.
    """
    import uuid as _uuid

    if not db_path.exists():
        return  # nothing to write to
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            now_iso = datetime.now(UTC).isoformat(sep=" ", timespec="microseconds")
            now_iso = now_iso.replace("+00:00", "")
            metadata_json = json.dumps(metadata, ensure_ascii=False)
            for (hh_id,) in conn.execute("SELECT id FROM households"):
                conn.execute(
                    "INSERT INTO audit_log "
                    "(household_id, id, occurred_at, actor_user_id, actor_kind, "
                    "action, entity_type, entity_id, request_id, "
                    "ip_address, user_agent, before_snapshot, after_snapshot, metadata) "
                    "VALUES (?, ?, ?, NULL, 'system', ?, 'household', ?, "
                    "NULL, NULL, NULL, NULL, NULL, ?)",
                    (
                        hh_id,
                        str(_uuid.uuid4()),
                        now_iso,
                        action,
                        hh_id,
                        metadata_json,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        # Best-effort: never block the backup on an audit-row failure.
        # The user-facing tarball / restore action is the load-bearing
        # output; the audit row is the trace.
        return


class BackupError(Exception):
    """Raised when backup creation fails for a recoverable reason."""


class RestoreError(Exception):
    """Raised when restore fails for a recoverable reason (envelope mismatch, etc)."""


@dataclass(frozen=True, slots=True)
class Manifest:
    """Backup manifest, serialised as the ``manifest.json`` entry."""

    format_version: int
    tulip_version: str
    alembic_head: str | None
    hostname: str
    timestamp: str
    key_envelope: str  # base64-encoded HMAC-SHA256

    def to_json(self) -> bytes:
        """Serialise to the canonical ``manifest.json`` byte form."""
        return json.dumps(
            {
                "format_version": self.format_version,
                "tulip_version": self.tulip_version,
                "alembic_head": self.alembic_head,
                "hostname": self.hostname,
                "timestamp": self.timestamp,
                "key_envelope": self.key_envelope,
            },
            indent=2,
            sort_keys=True,
        ).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes) -> Manifest:
        """Parse a ``manifest.json`` byte payload back into a ``Manifest`` dataclass."""
        try:
            obj = json.loads(data)
        except json.JSONDecodeError as exc:
            raise RestoreError("manifest.json is not valid JSON") from exc
        try:
            return cls(
                format_version=int(obj["format_version"]),
                tulip_version=str(obj["tulip_version"]),
                alembic_head=(str(obj["alembic_head"]) if obj.get("alembic_head") else None),
                hostname=str(obj["hostname"]),
                timestamp=str(obj["timestamp"]),
                key_envelope=str(obj["key_envelope"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise RestoreError(f"manifest.json missing required field: {exc}") from exc


def _seal_key_envelope(master_key: bytes) -> str:
    """Compute the HMAC-SHA256 of ENVELOPE_INPUT under ``master_key``, base64-encoded."""
    digest = hmac.new(master_key, ENVELOPE_INPUT, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _verify_key_envelope(envelope: str, master_key: bytes) -> bool:
    """Constant-time check that ``envelope`` was sealed with ``master_key``."""
    expected = _seal_key_envelope(master_key)
    return hmac.compare_digest(expected, envelope)


def build_manifest(*, alembic_head: str | None, master_key: bytes, tulip_version: str) -> Manifest:
    """Construct a Manifest for the current process / install."""
    return Manifest(
        format_version=FORMAT_VERSION,
        tulip_version=tulip_version,
        alembic_head=alembic_head,
        hostname=socket.gethostname() or platform.node() or "unknown",
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        key_envelope=_seal_key_envelope(master_key),
    )


def snapshot_sqlite_db(source_path: Path, dest_path: Path) -> None:
    """Hot-copy a SQLite database via the stdlib ``.backup`` API.

    Concurrent-safe: the source DB can be in use by another process. The
    snapshot is a complete, consistent copy at the moment of the call.

    Raises:
        BackupError: source file does not exist or is not a SQLite database.

    """
    if not source_path.exists():
        raise BackupError(f"database file not found: {source_path}")
    try:
        src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        raise BackupError(f"could not open database {source_path}: {exc}") from exc
    try:
        dest = sqlite3.connect(str(dest_path))
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()


def alembic_head_from_db(db_path: Path) -> str | None:
    """Read the current alembic revision from a SQLite database, or None if absent."""
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None
    try:
        cur = conn.execute("SELECT version_num FROM alembic_version LIMIT 1")
        row = cur.fetchone()
        return str(row[0]) if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def write_backup(
    *,
    db_path: Path,
    attachment_root: Path,
    master_key: bytes,
    tulip_version: str,
    out: IO[bytes],
) -> Manifest:
    """Stream a tar.gz backup to ``out`` (file or stdout buffer).

    Returns the manifest for caller-side reporting.
    """
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        snapshot_path = td_path / "tulip.db"
        snapshot_sqlite_db(db_path, snapshot_path)

        manifest = build_manifest(
            alembic_head=alembic_head_from_db(snapshot_path),
            master_key=master_key,
            tulip_version=tulip_version,
        )

        with tarfile.open(fileobj=out, mode="w:gz") as tar:
            # manifest.json first so streaming readers can verify the
            # envelope before extracting any data.
            manifest_bytes = manifest.to_json()
            info = tarfile.TarInfo(name=_MANIFEST_NAME)
            info.size = len(manifest_bytes)
            info.mode = 0o600
            info.mtime = int(datetime.now(UTC).timestamp())
            tar.addfile(info, io.BytesIO(manifest_bytes))

            # db/tulip.db
            tar.add(
                snapshot_path,
                arcname=_DB_PATH_PREFIX + snapshot_path.name,
                recursive=False,
            )

            # attachments/...
            if attachment_root.exists():
                tar.add(attachment_root, arcname=_ATTACHMENTS_PATH_PREFIX.rstrip("/"))

    return manifest


def read_backup_manifest(in_path: Path) -> Manifest:
    """Read just the manifest from an on-disk backup, without extracting."""
    with tarfile.open(in_path, mode="r:gz") as tar:
        try:
            member = tar.getmember(_MANIFEST_NAME)
        except KeyError as exc:
            raise RestoreError(f"backup is missing the required {_MANIFEST_NAME} entry") from exc
        f = tar.extractfile(member)
        if f is None:
            raise RestoreError(f"could not read {_MANIFEST_NAME} from backup")
        return Manifest.from_json(f.read())


def restore_backup(
    *,
    in_path: Path,
    db_path: Path,
    attachment_root: Path,
    master_key: bytes,
    current_alembic_head: str | None,
    force: bool,
) -> Manifest:
    """Restore a backup tarball onto ``db_path`` + ``attachment_root``.

    Refuses with :class:`RestoreError` if:

    - The manifest's key envelope doesn't match the current master key.
    - The format_version is newer than this build supports.
    - ``db_path`` exists or ``attachment_root`` is non-empty, unless
      ``force=True``.
    - The manifest's alembic_head differs from ``current_alembic_head``
      (the user must run ``alembic upgrade head`` against the restored
      DB before re-pointing the API at it).

    Returns the restored manifest for caller-side reporting.
    """
    manifest = read_backup_manifest(in_path)

    if manifest.format_version > FORMAT_VERSION:
        raise RestoreError(
            f"backup format_version is {manifest.format_version}; this build "
            f"only understands up to v{FORMAT_VERSION}. Upgrade Tulip and retry."
        )

    if not _verify_key_envelope(manifest.key_envelope, master_key):
        raise RestoreError(
            "key envelope mismatch — the backup was made with a different "
            "master key than the current install. Set TULIP_MASTER_KEY / "
            "TULIP_KEY_FILE to the key that was active when the backup was "
            "taken, then retry."
        )

    if (
        current_alembic_head is not None
        and manifest.alembic_head is not None
        and manifest.alembic_head != current_alembic_head
    ):
        raise RestoreError(
            f"backup was taken at alembic head {manifest.alembic_head!r}, but "
            f"the current install is at {current_alembic_head!r}. Restore the "
            "backup to a scratch path, run `alembic upgrade head` against it, "
            "then move it into place. (Auto-migrate during restore is "
            "intentionally not done — the operator should see the schema "
            "transition.)"
        )

    if not force:
        if db_path.exists():
            raise RestoreError(
                f"refusing to overwrite existing database at {db_path}. "
                "Pass --force to overwrite, or restore to an empty path."
            )
        if attachment_root.exists() and any(attachment_root.iterdir()):
            raise RestoreError(
                f"refusing to overwrite non-empty attachment root at "
                f"{attachment_root}. Pass --force to overwrite."
            )

    safe_attachment_root = attachment_root.resolve()
    with tarfile.open(in_path, mode="r:gz") as tar:
        for member in tar.getmembers():
            name = member.name
            if name == _MANIFEST_NAME:
                continue
            if name.startswith(_DB_PATH_PREFIX):
                member.name = db_path.name
                tar.extract(member, path=db_path.parent, filter="data")
            elif name.startswith(_ATTACHMENTS_PATH_PREFIX):
                # Re-root attachments under attachment_root.
                stripped = name[len(_ATTACHMENTS_PATH_PREFIX) :]
                if not stripped:
                    continue  # the directory entry itself
                attachment_root.mkdir(parents=True, exist_ok=True)
                target = attachment_root / stripped
                # Reject any member whose resolved path escapes
                # attachment_root (`..` segments, absolute paths, etc.).
                # The DB branch uses `tar.extract(..., filter="data")`
                # which has Python 3.12+'s built-in safe filter; the
                # attachment branch writes manually so it needs its own
                # guard. See #217.
                resolved = target.resolve()
                if not resolved.is_relative_to(safe_attachment_root):
                    raise RestoreError(
                        f"backup contains path traversal in attachment "
                        f"member {name!r}: resolves to {resolved} which "
                        f"escapes attachment_root {safe_attachment_root}"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    f = tar.extractfile(member)
                    if f is not None:
                        target.write_bytes(f.read())
            else:
                # Unknown top-level entry — skip rather than error so a
                # forward-compatible v2 backup can still be read by v1
                # for the parts it understands.
                continue

    return manifest


def resolve_db_path_from_url(database_url: str) -> Path:
    """Extract the SQLite file path from a sqlite:/// URL."""
    if not database_url.startswith("sqlite:///"):
        raise BackupError(
            f"backup only supports sqlite:/// URLs (got {database_url!r}). "
            "Postgres backup is a Phase 9 concern."
        )
    return Path(database_url[len("sqlite:///") :]).expanduser().resolve()


def load_master_key_from_env() -> bytes:
    """Resolve the master key the same way the API does (#132)."""
    raw = os.environ.get("TULIP_MASTER_KEY")
    if raw is not None:
        decoded = base64.b64decode(raw, validate=True)
        if len(decoded) != 32:
            raise BackupError(f"TULIP_MASTER_KEY must decode to 32 bytes (got {len(decoded)})")
        return decoded

    file_path = os.environ.get("TULIP_KEY_FILE")
    if file_path:
        path = Path(file_path).expanduser()
        if not path.exists():
            raise BackupError(f"$TULIP_KEY_FILE points at {path}, but file not found")
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise BackupError(
                f"$TULIP_KEY_FILE {path} has mode {mode:#o}; group/other access is forbidden"
            )
        contents = path.read_text(encoding="ascii").strip()
        decoded = base64.b64decode(contents, validate=True)
        if len(decoded) != 32:
            raise BackupError(
                f"$TULIP_KEY_FILE {path} must decode to 32 bytes (got {len(decoded)})"
            )
        return decoded

    raise BackupError(
        "Master key not configured. Set TULIP_MASTER_KEY or TULIP_KEY_FILE "
        "before running backup/restore. Backup-time key envelope verification "
        "would be meaningless under an ephemeral key."
    )

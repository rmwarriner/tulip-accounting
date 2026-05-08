"""Tests for ``tulip backup`` / ``tulip restore`` (#133)."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from tulip_cli.backup import (
    FORMAT_VERSION,
    BackupError,
    Manifest,
    RestoreError,
    _seal_key_envelope,
    _verify_key_envelope,
    alembic_head_from_db,
    build_manifest,
    read_backup_manifest,
    resolve_db_path_from_url,
    restore_backup,
    snapshot_sqlite_db,
    write_backup,
)

# ---- Manifest + envelope --------------------------------------------------


class TestKeyEnvelope:
    def test_seal_then_verify_round_trips(self):
        key = b"\x01" * 32
        env = _seal_key_envelope(key)
        assert _verify_key_envelope(env, key) is True

    def test_verify_rejects_wrong_key(self):
        env = _seal_key_envelope(b"\x01" * 32)
        assert _verify_key_envelope(env, b"\x02" * 32) is False

    def test_envelope_is_base64(self):
        env = _seal_key_envelope(b"\x03" * 32)
        # Round-trip through base64 must succeed.
        base64.b64decode(env, validate=True)


class TestManifestSerialisation:
    def test_to_json_then_from_json_round_trips(self):
        m = build_manifest(
            alembic_head="deadbeef",
            master_key=b"\x04" * 32,
            tulip_version="1.2.3",
        )
        roundtripped = Manifest.from_json(m.to_json())
        assert roundtripped == m

    def test_from_json_rejects_garbage(self):
        with pytest.raises(RestoreError, match="not valid JSON"):
            Manifest.from_json(b"not-json")

    def test_from_json_rejects_missing_field(self):
        with pytest.raises(RestoreError, match="missing required field"):
            Manifest.from_json(b'{"format_version": 1}')


# ---- Snapshot + alembic_head_from_db --------------------------------------


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    """Tiny SQLite DB with an alembic_version row."""
    db = tmp_path / "src.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        conn.execute("INSERT INTO alembic_version VALUES ('aaaa1111')")
        conn.execute("CREATE TABLE rows (id INTEGER PRIMARY KEY, label TEXT)")
        conn.execute("INSERT INTO rows VALUES (1, 'one'), (2, 'two')")
        conn.commit()
    finally:
        conn.close()
    return db


class TestSnapshot:
    def test_snapshot_copies_data(self, tmp_path: Path, seeded_db: Path):
        dest = tmp_path / "snap.db"
        snapshot_sqlite_db(seeded_db, dest)
        assert dest.exists()
        conn = sqlite3.connect(str(dest))
        try:
            rows = conn.execute("SELECT label FROM rows ORDER BY id").fetchall()
        finally:
            conn.close()
        assert [r[0] for r in rows] == ["one", "two"]

    def test_snapshot_missing_source_raises(self, tmp_path: Path):
        with pytest.raises(BackupError, match="not found"):
            snapshot_sqlite_db(tmp_path / "nope.db", tmp_path / "snap.db")

    def test_alembic_head_from_db(self, seeded_db: Path):
        assert alembic_head_from_db(seeded_db) == "aaaa1111"

    def test_alembic_head_returns_none_for_missing(self, tmp_path: Path):
        assert alembic_head_from_db(tmp_path / "nope.db") is None

    def test_alembic_head_returns_none_for_no_table(self, tmp_path: Path):
        db = tmp_path / "empty.db"
        sqlite3.connect(str(db)).close()
        assert alembic_head_from_db(db) is None


# ---- write_backup / restore_backup round-trip -----------------------------


@pytest.fixture
def attachment_root(tmp_path: Path) -> Path:
    """Attachment tree with two files, mirroring the real layout."""
    root = tmp_path / "attachments"
    (root / "ab" / "cd").mkdir(parents=True)
    (root / "ab" / "cd" / "doc.bin").write_bytes(b"opaque encrypted bytes 1")
    (root / "ef").mkdir(parents=True)
    (root / "ef" / "more.bin").write_bytes(b"opaque encrypted bytes 2")
    return root


class TestRoundTrip:
    def test_backup_then_restore_recovers_db_and_attachments(
        self, tmp_path: Path, seeded_db: Path, attachment_root: Path
    ):
        master_key = b"\x05" * 32
        backup_path = tmp_path / "snap.tar.gz"
        with backup_path.open("wb") as f:
            write_backup(
                db_path=seeded_db,
                attachment_root=attachment_root,
                master_key=master_key,
                tulip_version="0.1.0",
                out=f,
            )
        assert backup_path.stat().st_size > 0

        # Restore to fresh paths.
        new_db = tmp_path / "restored.db"
        new_root = tmp_path / "restored-attachments"
        manifest = restore_backup(
            in_path=backup_path,
            db_path=new_db,
            attachment_root=new_root,
            master_key=master_key,
            current_alembic_head="aaaa1111",  # matches seeded DB
            force=False,
        )
        assert manifest.format_version == FORMAT_VERSION
        assert new_db.exists()
        # DB content survives.
        conn = sqlite3.connect(str(new_db))
        try:
            rows = conn.execute("SELECT label FROM rows ORDER BY id").fetchall()
        finally:
            conn.close()
        assert [r[0] for r in rows] == ["one", "two"]
        # Attachments survive.
        assert (new_root / "ab" / "cd" / "doc.bin").read_bytes() == b"opaque encrypted bytes 1"
        assert (new_root / "ef" / "more.bin").read_bytes() == b"opaque encrypted bytes 2"

    def test_restore_refuses_wrong_master_key(
        self, tmp_path: Path, seeded_db: Path, attachment_root: Path
    ):
        backup_path = tmp_path / "snap.tar.gz"
        with backup_path.open("wb") as f:
            write_backup(
                db_path=seeded_db,
                attachment_root=attachment_root,
                master_key=b"\x06" * 32,
                tulip_version="0.1.0",
                out=f,
            )
        with pytest.raises(RestoreError, match="key envelope mismatch"):
            restore_backup(
                in_path=backup_path,
                db_path=tmp_path / "restored.db",
                attachment_root=tmp_path / "restored-attachments",
                master_key=b"\x07" * 32,  # wrong key
                current_alembic_head="aaaa1111",
                force=False,
            )

    def test_restore_refuses_schema_mismatch(
        self, tmp_path: Path, seeded_db: Path, attachment_root: Path
    ):
        backup_path = tmp_path / "snap.tar.gz"
        with backup_path.open("wb") as f:
            write_backup(
                db_path=seeded_db,
                attachment_root=attachment_root,
                master_key=b"\x08" * 32,
                tulip_version="0.1.0",
                out=f,
            )
        with pytest.raises(RestoreError, match="alembic upgrade head"):
            restore_backup(
                in_path=backup_path,
                db_path=tmp_path / "restored.db",
                attachment_root=tmp_path / "restored-attachments",
                master_key=b"\x08" * 32,
                current_alembic_head="zzzz9999",  # different head
                force=False,
            )

    def test_restore_refuses_existing_db_without_force(
        self, tmp_path: Path, seeded_db: Path, attachment_root: Path
    ):
        backup_path = tmp_path / "snap.tar.gz"
        with backup_path.open("wb") as f:
            write_backup(
                db_path=seeded_db,
                attachment_root=attachment_root,
                master_key=b"\x09" * 32,
                tulip_version="0.1.0",
                out=f,
            )
        existing_db = tmp_path / "existing.db"
        existing_db.write_text("not a db, just exists")
        with pytest.raises(RestoreError, match="refusing to overwrite"):
            restore_backup(
                in_path=backup_path,
                db_path=existing_db,
                attachment_root=tmp_path / "restored-attachments",
                master_key=b"\x09" * 32,
                current_alembic_head="aaaa1111",
                force=False,
            )

    def test_restore_overwrites_with_force(
        self, tmp_path: Path, seeded_db: Path, attachment_root: Path
    ):
        backup_path = tmp_path / "snap.tar.gz"
        with backup_path.open("wb") as f:
            write_backup(
                db_path=seeded_db,
                attachment_root=attachment_root,
                master_key=b"\x0a" * 32,
                tulip_version="0.1.0",
                out=f,
            )
        existing_db = tmp_path / "existing.db"
        existing_db.write_text("not a db, just exists")
        # Should not raise.
        restore_backup(
            in_path=backup_path,
            db_path=existing_db,
            attachment_root=tmp_path / "restored-attachments",
            master_key=b"\x0a" * 32,
            current_alembic_head="aaaa1111",
            force=True,
        )

    def test_read_manifest_without_extracting(
        self, tmp_path: Path, seeded_db: Path, attachment_root: Path
    ):
        backup_path = tmp_path / "snap.tar.gz"
        with backup_path.open("wb") as f:
            write_backup(
                db_path=seeded_db,
                attachment_root=attachment_root,
                master_key=b"\x0b" * 32,
                tulip_version="0.1.0",
                out=f,
            )
        manifest = read_backup_manifest(backup_path)
        assert manifest.format_version == FORMAT_VERSION
        assert manifest.alembic_head == "aaaa1111"
        assert manifest.tulip_version == "0.1.0"

    def test_format_version_too_new_refused(self, tmp_path: Path):
        # Build a tarball with a manifest whose format_version is FORMAT_VERSION+1.
        backup_path = tmp_path / "future.tar.gz"
        future_manifest = {
            "format_version": FORMAT_VERSION + 1,
            "tulip_version": "9.9.9",
            "alembic_head": "ffff",
            "hostname": "future",
            "timestamp": "2099-12-31T23:59:59Z",
            "key_envelope": _seal_key_envelope(b"\x0c" * 32),
        }
        with tarfile.open(backup_path, mode="w:gz") as tar:
            data = json.dumps(future_manifest).encode("utf-8")
            info = tarfile.TarInfo(name="manifest.json")
            info.size = len(data)
            import io as _io

            tar.addfile(info, _io.BytesIO(data))
        with pytest.raises(RestoreError, match="format_version"):
            restore_backup(
                in_path=backup_path,
                db_path=tmp_path / "restored.db",
                attachment_root=tmp_path / "restored-attachments",
                master_key=b"\x0c" * 32,
                current_alembic_head="ffff",
                force=False,
            )

    def test_missing_manifest_raises(self, tmp_path: Path):
        backup_path = tmp_path / "no-manifest.tar.gz"
        with tarfile.open(backup_path, mode="w:gz") as tar:
            data = b"some payload"
            info = tarfile.TarInfo(name="other.txt")
            info.size = len(data)
            import io as _io

            tar.addfile(info, _io.BytesIO(data))
        with pytest.raises(RestoreError, match="missing the required manifest"):
            read_backup_manifest(backup_path)


class TestResolveDbPath:
    def test_sqlite_url(self):
        assert resolve_db_path_from_url("sqlite:///./tulip.db") == Path("./tulip.db").resolve()

    def test_non_sqlite_refused(self):
        with pytest.raises(BackupError, match="sqlite:///"):
            resolve_db_path_from_url("postgresql://user:pass@host/db")


# ---- CLI integration (subprocess) ----------------------------------------


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "tulip_cli", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=full_env,
    )


@pytest.mark.integration
def test_cli_backup_restore_round_trip(tmp_path: Path):
    """End-to-end: write a tiny seeded DB, `tulip backup`, then `tulip restore`."""
    master_key_b64 = base64.b64encode(b"\x10" * 32).decode("ascii")
    src_db = tmp_path / "tulip.db"
    conn = sqlite3.connect(str(src_db))
    try:
        conn.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        conn.execute("INSERT INTO alembic_version VALUES ('cliround')")
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        conn.execute("INSERT INTO notes VALUES (1, 'hi')")
        conn.commit()
    finally:
        conn.close()
    att_root = tmp_path / "attachments"
    att_root.mkdir()
    (att_root / "blob.bin").write_bytes(b"\x00\x01\x02")

    backup_path = tmp_path / "backup.tar.gz"
    env = {
        "TULIP_MASTER_KEY": master_key_b64,
        "TULIP_DATABASE_URL": f"sqlite:///{src_db}",
        "TULIP_ATTACHMENT_ROOT": str(att_root),
    }
    result = _run_cli("backup", "--out", str(backup_path), env=env)
    assert result.returncode == 0, result.stderr
    assert backup_path.exists()
    assert "wrote" in result.stdout

    # Inspect via backup-inspect.
    inspect = _run_cli("backup-inspect", str(backup_path), env=env)
    assert inspect.returncode == 0, inspect.stderr
    assert "cliround" in inspect.stdout

    # Restore to fresh paths.
    new_db = tmp_path / "restored.db"
    new_att = tmp_path / "restored-att"
    restore_env = {
        "TULIP_MASTER_KEY": master_key_b64,
        "TULIP_DATABASE_URL": f"sqlite:///{new_db}",
        "TULIP_ATTACHMENT_ROOT": str(new_att),
    }
    restore = _run_cli("restore", str(backup_path), env=restore_env)
    assert restore.returncode == 0, restore.stderr
    # Verify content.
    conn = sqlite3.connect(str(new_db))
    try:
        body = conn.execute("SELECT body FROM notes WHERE id = 1").fetchone()[0]
    finally:
        conn.close()
    assert body == "hi"
    assert (new_att / "blob.bin").read_bytes() == b"\x00\x01\x02"


@pytest.mark.integration
def test_cli_restore_wrong_key_exits_2(tmp_path: Path):
    master_key_b64 = base64.b64encode(b"\x11" * 32).decode("ascii")
    src_db = tmp_path / "tulip.db"
    sqlite3.connect(str(src_db)).close()
    att_root = tmp_path / "attachments"
    att_root.mkdir()

    backup_path = tmp_path / "backup.tar.gz"
    env = {
        "TULIP_MASTER_KEY": master_key_b64,
        "TULIP_DATABASE_URL": f"sqlite:///{src_db}",
        "TULIP_ATTACHMENT_ROOT": str(att_root),
    }
    _run_cli("backup", "--out", str(backup_path), env=env)

    # Try restore with a different master key.
    wrong_key = base64.b64encode(b"\x99" * 32).decode("ascii")
    restore_env = {
        "TULIP_MASTER_KEY": wrong_key,
        "TULIP_DATABASE_URL": f"sqlite:///{tmp_path / 'restored.db'}",
        "TULIP_ATTACHMENT_ROOT": str(tmp_path / "restored-att"),
    }
    result = _run_cli("restore", str(backup_path), env=restore_env)
    assert result.returncode == 2
    assert "envelope" in (result.stdout + result.stderr).lower()


@pytest.mark.integration
def test_cli_backup_no_master_key_exits_2(tmp_path: Path):
    """Backup refuses without a configured master key — envelope verify would be meaningless."""
    src_db = tmp_path / "tulip.db"
    sqlite3.connect(str(src_db)).close()
    env = {
        "TULIP_DATABASE_URL": f"sqlite:///{src_db}",
        "TULIP_ATTACHMENT_ROOT": str(tmp_path / "att"),
    }
    # Strip both env vars from the inherited environment.
    env_with_keys_stripped = {
        k: v
        for k, v in {**os.environ, **env}.items()
        if k not in ("TULIP_MASTER_KEY", "TULIP_KEY_FILE")
    }
    result = subprocess.run(
        [sys.executable, "-m", "tulip_cli", "backup", "--out", str(tmp_path / "x.tar.gz")],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env=env_with_keys_stripped,
    )
    assert result.returncode == 2
    assert "master key" in (result.stdout + result.stderr).lower()

"""``tulip backup`` + ``tulip restore`` commands (#133, #121 hardening Tier 1b)."""

from __future__ import annotations

import json as _json
import os
import sys
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Annotated

import typer

from tulip_cli.backup import (
    BackupError,
    RestoreError,
    alembic_head_from_db,
    read_backup_manifest,
    resolve_db_path_from_url,
    restore_backup,
    write_backup,
    write_backup_audit_rows,
)
from tulip_cli.backup import (
    load_master_key_from_env as _load_master_key,
)


def _tulip_version() -> str:
    try:
        return _pkg_version("tulip-cli")
    except Exception:  # pragma: no cover - missing package metadata
        return "0.0.0"


def _attachment_root() -> Path:
    raw = os.environ.get("TULIP_ATTACHMENT_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / ".local" / "share" / "tulip" / "attachments"


def _database_url() -> str:
    return os.environ.get("TULIP_DATABASE_URL", "sqlite:///./tulip.db")


def backup_command(
    ctx: typer.Context,
    out: Annotated[
        str,
        typer.Option(
            "--out",
            help=(
                "Destination path for the tar.gz backup. Use '-' to stream to "
                "stdout (pipeable into gpg/age/curl)."
            ),
        ),
    ],
) -> None:
    """Produce a portable backup of the database + attachments + manifest.

    The tarball contains: the SQLite snapshot (taken via the stdlib
    ``.backup`` API; concurrent-safe — the API process can stay running),
    the full encrypted attachment tree, and a manifest with a key envelope
    so restore can verify the right master key is in scope. The tarball
    itself is *not* re-encrypted (the high-confidentiality fields are
    already field-encrypted).
    """
    as_json: bool = ctx.obj["json"]
    try:
        master_key = _load_master_key()
        db_path = resolve_db_path_from_url(_database_url())
    except BackupError as exc:
        typer.echo(f"backup: {exc}", err=True)
        raise typer.Exit(2) from None

    if out == "-":
        manifest = write_backup(
            db_path=db_path,
            attachment_root=_attachment_root(),
            master_key=master_key,
            tulip_version=_tulip_version(),
            out=sys.stdout.buffer,
        )
        write_backup_audit_rows(
            db_path=db_path,
            action="backup.created",
            metadata={
                "out": "-",
                "format_version": manifest.format_version,
                "tulip_version": manifest.tulip_version,
                "alembic_head": manifest.alembic_head,
                "timestamp": manifest.timestamp,
            },
        )
        if not as_json:
            # Stderr so stdout is the tar bytes only.
            typer.echo(f"backup: streamed to stdout (timestamp {manifest.timestamp})", err=True)
        return

    out_path = Path(out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with out_path.open("wb") as f:
            manifest = write_backup(
                db_path=db_path,
                attachment_root=_attachment_root(),
                master_key=master_key,
                tulip_version=_tulip_version(),
                out=f,
            )
    except BackupError as exc:
        typer.echo(f"backup: {exc}", err=True)
        if out_path.exists():
            out_path.unlink()
        raise typer.Exit(2) from None

    write_backup_audit_rows(
        db_path=db_path,
        action="backup.created",
        metadata={
            "out": str(out_path),
            "format_version": manifest.format_version,
            "tulip_version": manifest.tulip_version,
            "alembic_head": manifest.alembic_head,
            "timestamp": manifest.timestamp,
            "size_bytes": out_path.stat().st_size,
        },
    )

    if as_json:
        sys.stdout.write(
            _json.dumps(
                {
                    "out": str(out_path),
                    "format_version": manifest.format_version,
                    "tulip_version": manifest.tulip_version,
                    "alembic_head": manifest.alembic_head,
                    "timestamp": manifest.timestamp,
                    "size_bytes": out_path.stat().st_size,
                }
            )
            + "\n"
        )
        return
    typer.echo(
        f"backup: wrote {out_path} ({out_path.stat().st_size} bytes, "
        f"alembic head {manifest.alembic_head or 'unknown'}, "
        f"timestamp {manifest.timestamp})."
    )


def restore_command(
    ctx: typer.Context,
    in_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the tar.gz backup to restore.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            metavar="BACKUP_PATH",
        ),
    ],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help=(
                "Overwrite the database file + attachment root if they're "
                "already populated. Without this flag, restore refuses rather "
                "than silently destroying existing data."
            ),
        ),
    ] = False,
) -> None:
    """Restore a backup onto the configured database + attachment root.

    Refuses if the master-key envelope doesn't match (wrong key in
    scope), if the schema differs from the current install (operator must
    migrate the restored DB explicitly), or if existing data would be
    overwritten without ``--force``.
    """
    as_json: bool = ctx.obj["json"]
    try:
        master_key = _load_master_key()
        db_path = resolve_db_path_from_url(_database_url())
    except BackupError as exc:
        typer.echo(f"restore: {exc}", err=True)
        raise typer.Exit(2) from None

    current_head = alembic_head_from_db(db_path) if db_path.exists() else None

    try:
        manifest = restore_backup(
            in_path=in_path,
            db_path=db_path,
            attachment_root=_attachment_root(),
            master_key=master_key,
            current_alembic_head=current_head,
            force=force,
        )
    except RestoreError as exc:
        typer.echo(f"restore: {exc}", err=True)
        raise typer.Exit(2) from None

    write_backup_audit_rows(
        db_path=db_path,
        action="backup.restored",
        metadata={
            "in_path": str(in_path),
            "format_version": manifest.format_version,
            "tulip_version": manifest.tulip_version,
            "alembic_head": manifest.alembic_head,
            "hostname": manifest.hostname,
            "timestamp": manifest.timestamp,
        },
    )

    if as_json:
        sys.stdout.write(
            _json.dumps(
                {
                    "restored_to_db": str(db_path),
                    "restored_to_attachments": str(_attachment_root()),
                    "manifest": {
                        "format_version": manifest.format_version,
                        "tulip_version": manifest.tulip_version,
                        "alembic_head": manifest.alembic_head,
                        "hostname": manifest.hostname,
                        "timestamp": manifest.timestamp,
                    },
                }
            )
            + "\n"
        )
        return
    typer.echo(
        f"restore: restored {in_path} to {db_path} + {_attachment_root()} "
        f"(backup taken on {manifest.hostname} at {manifest.timestamp})."
    )


def manifest_command(
    ctx: typer.Context,
    in_path: Annotated[
        Path,
        typer.Argument(
            help="Path to a tar.gz backup to inspect.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            metavar="BACKUP_PATH",
        ),
    ],
) -> None:
    """Print the manifest of a backup without extracting anything."""
    as_json: bool = ctx.obj["json"]
    try:
        manifest = read_backup_manifest(in_path)
    except RestoreError as exc:
        typer.echo(f"backup-inspect: {exc}", err=True)
        raise typer.Exit(2) from None

    if as_json:
        sys.stdout.write(manifest.to_json().decode("utf-8") + "\n")
        return
    typer.echo(
        f"backup {in_path}:\n"
        f"  format_version: {manifest.format_version}\n"
        f"  tulip_version:  {manifest.tulip_version}\n"
        f"  alembic_head:   {manifest.alembic_head or '<unknown>'}\n"
        f"  hostname:       {manifest.hostname}\n"
        f"  timestamp:      {manifest.timestamp}"
    )

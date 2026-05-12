"""``tulip import ofx`` — upload a statement file to /v1/imports.

Pure CLI surface over the imports API endpoint. Reads the file from
disk, resolves ``--account`` to a UUID via the shared resolver, and
issues a multipart POST through ``TulipClient.post_multipart``.

Per ADR-0004 §"Module layout", the importer's parsing logic lives in
``tulip_importers.ofx`` and the CLI never invokes it directly — the
API does. This keeps the CLI a pure network client (architecture
test in ``tulip-cli/tests/test_architecture.py`` enforces this).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.commands.accounts import _resolve_account
from tulip_cli.commands.csv_profiles import csv_profiles_app
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

imports_app = typer.Typer(
    name="imports",
    help="Upload statement files (OFX, QIF, CSV) and manage CSV profiles.",
    no_args_is_help=True,
)
imports_app.add_typer(csv_profiles_app, name="profiles")


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


def _render_summary(body: dict[str, Any]) -> None:
    typer.echo(
        f"Imported {body.get('statement_line_count', 0)} statement lines "
        f"into batch {body.get('id', '')} "
        f"({body.get('source_format', 'ofx')} from {body.get('source_filename', '')})."
    )


def _do_import(
    ctx: typer.Context,
    *,
    file_path: Path,
    account: str,
    source_format: str,
    content_type: str,
    extra_form: dict[str, str] | None = None,
) -> None:
    """Shared upload flow: resolve account, read file, multipart POST.

    ``extra_form`` carries format-specific form fields (e.g.,
    ``profile_id`` for CSV uploads) merged with the standard
    ``account_id``/``source_format`` pair.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    try:
        with _client(config, as_json=as_json) as client:
            account_record = _resolve_account(client, account)
            account_id = str(account_record["id"])
            raw_bytes = file_path.read_bytes()
            data: dict[str, str] = {
                "account_id": account_id,
                "source_format": source_format,
            }
            if extra_form:
                data.update(extra_form)
            response = client.post_multipart(
                "/v1/imports",
                files={"file": (file_path.name, raw_bytes, content_type)},
                data=data,
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    _render_summary(response.json())


def _resolve_profile_id(client: TulipClient, profile: str) -> str:
    """Resolve a CSV profile name (or UUID) to a UUID via the API."""
    response = client.get(f"/v1/imports/profiles/{profile}", authenticated=True)
    return str(response.json()["id"])


@imports_app.command("ofx")
def import_ofx(
    ctx: typer.Context,
    file_path: Annotated[
        Path,
        typer.Argument(
            help="Path to an OFX 1.x SGML or 2.x XML statement file.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            metavar="FILE",
        ),
    ],
    account: Annotated[
        str,
        typer.Option(
            "--account",
            help=(
                "Account this statement belongs to. UUID or code (resolved "
                "the same way as `accounts show`)."
            ),
        ),
    ],
) -> None:
    """Upload an OFX file; the API parses it and persists a batch."""
    _do_import(
        ctx,
        file_path=file_path,
        account=account,
        source_format="ofx",
        content_type="application/x-ofx",
    )


@imports_app.command("csv")
def import_csv(
    ctx: typer.Context,
    file_path: Annotated[
        Path,
        typer.Argument(
            help="Path to a CSV statement file.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            metavar="FILE",
        ),
    ],
    account: Annotated[
        str,
        typer.Option(
            "--account",
            help=(
                "Account this statement belongs to. UUID or code. The "
                "account's currency is applied to every line."
            ),
        ),
    ],
    profile: Annotated[
        str,
        typer.Option(
            "--profile",
            help=(
                "CSV column-mapping profile (UUID or name). Resolved client-side before the upload."
            ),
        ),
    ],
) -> None:
    """Upload a CSV file with the named profile; the API parses it."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            profile_id = _resolve_profile_id(client, profile)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    _do_import(
        ctx,
        file_path=file_path,
        account=account,
        source_format="csv",
        content_type="text/csv",
        extra_form={"profile_id": profile_id},
    )


@imports_app.command("show")
def show_import(
    ctx: typer.Context,
    batch_id: Annotated[
        str,
        typer.Argument(
            help="Import batch UUID returned by `tulip imports ofx/qif/csv`.",
            metavar="BATCH_ID",
        ),
    ],
) -> None:
    """Render an import batch's header + parsed statement lines."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get(f"/v1/imports/{batch_id}", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    _render_batch(response.json())


def _render_batch(body: dict[str, Any]) -> None:
    """Render an ``ImportBatchRead`` body to stdout."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    header_lines = [
        f"Batch:    {body.get('id', '')}",
        f"Source:   {body.get('source_filename', '')} ({body.get('source_format', '?').upper()})",
        f"Account:  {body.get('account_id', '')}",
        f"Status:   {body.get('status', '?')}",
        f"Counts:   imported={body.get('imported_count', 0)}  "
        f"skipped={body.get('skipped_count', 0)}  "
        f"errors={body.get('error_count', 0)}",
        f"Created:  {body.get('created_at', '')}",
    ]
    applied_at = body.get("applied_at")
    if applied_at:
        header_lines.append(f"Applied:  {applied_at}")
    reverted_at = body.get("reverted_at")
    if reverted_at:
        header_lines.append(f"Reverted: {reverted_at}")
    for line in header_lines:
        typer.echo(line)

    lines = body.get("lines") or []
    if not lines:
        typer.echo("\n(no statement lines)")
        return

    table = Table(title=f"\nStatement lines ({len(lines)})", show_header=True)
    table.add_column("#", justify="right")
    table.add_column("date")
    table.add_column("amount", justify="right")
    table.add_column("ccy")
    table.add_column("description")
    table.add_column("flag")
    for line in lines:
        flag_bits: list[str] = []
        if line.get("is_excluded"):
            flag_bits.append("excluded")
        if line.get("reconciliation_match_id"):
            flag_bits.append("reconciled")
        table.add_row(
            str(line.get("line_number", "")),
            str(line.get("posted_date", "")),
            str(line.get("amount", "")),
            str(line.get("currency", "")),
            str(line.get("description", "") or ""),
            ", ".join(flag_bits),
        )
    console.print(table)


@imports_app.command("apply")
def apply_import(
    ctx: typer.Context,
    batch_id: Annotated[
        str,
        typer.Argument(
            help="Import batch UUID returned by `tulip imports ofx/qif/csv`.",
            metavar="BATCH_ID",
        ),
    ],
    no_categorize: Annotated[
        bool,
        typer.Option(
            "--no-categorize",
            help=(
                "Skip the AI categorizer; route every line to the "
                "household's Imbalance:Unknown account (auto-created per "
                "currency on first use). Useful for bulk migrations from "
                "another tool where you'll assign categories manually."
            ),
        ),
    ] = False,
) -> None:
    """Apply a parsed batch: every non-excluded line becomes a PENDING ledger transaction."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    path = f"/v1/imports/{batch_id}/apply"
    if no_categorize:
        path += "?no_categorize=true"
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post(path, authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    body = response.json()
    typer.echo(
        f"Applied batch {body['batch_id']}: created {body['created_count']} "
        f"PENDING transactions, skipped {body['skipped_count']} lines."
    )


@imports_app.command("qif")
def import_qif(
    ctx: typer.Context,
    file_path: Annotated[
        Path,
        typer.Argument(
            help="Path to a QIF (Quicken Interchange Format) statement file.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            metavar="FILE",
        ),
    ],
    account: Annotated[
        str,
        typer.Option(
            "--account",
            help=(
                "Account this statement belongs to. UUID or code. The "
                "account's currency is applied to every line — QIF doesn't "
                "carry currency in the file itself."
            ),
        ),
    ],
) -> None:
    """Upload a QIF file; the API parses it and persists a batch."""
    _do_import(
        ctx,
        file_path=file_path,
        account=account,
        source_format="qif",
        content_type="application/qif",
    )

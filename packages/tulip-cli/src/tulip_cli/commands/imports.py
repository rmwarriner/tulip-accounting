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
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

imports_app = typer.Typer(
    name="import",
    help="Upload statement files (OFX in P5.2.a; QIF and CSV in later slices).",
    no_args_is_help=True,
)


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
) -> None:
    """Shared upload flow: resolve account, read file, multipart POST."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    try:
        with _client(config, as_json=as_json) as client:
            account_record = _resolve_account(client, account)
            account_id = str(account_record["id"])
            raw_bytes = file_path.read_bytes()
            response = client.post_multipart(
                "/v1/imports",
                files={"file": (file_path.name, raw_bytes, content_type)},
                data={"account_id": account_id, "source_format": source_format},
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    _render_summary(response.json())


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

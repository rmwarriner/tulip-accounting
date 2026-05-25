"""``tulip pta {export,import}`` — wrappers over ``/v1/pta/*`` (#415).

Replaces the former ``tulip journal {export,import}`` surface.  The
``pta`` namespace reserves room for ``--format ledger`` / ``beancount``
support planned in #34 without a second rename.

* ``tulip pta export`` calls ``GET /v1/pta/export`` and writes the
  hledger-formatted journal to stdout or ``--output``.
* ``tulip pta import FILE`` posts the file's bytes to
  ``POST /v1/pta/import`` as ``text/plain``. The API parses, resolves
  account paths, and creates PENDING transactions through the
  ``TransactionRepository.save_balanced`` chokepoint — matching the
  OFX / QIF / CSV importer convention (#74).
"""

from __future__ import annotations

import sys
from datetime import date as date_type
from pathlib import Path
from typing import Annotated, Any

import typer

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

pta_app = typer.Typer(
    name="pta",
    help="Plain-text accounting (PTA) export + import.",
    no_args_is_help=True,
)


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


def _validate_date(value: str | None, flag: str) -> None:
    if value is None:
        return
    try:
        date_type.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"{flag} must be YYYY-MM-DD") from exc


@pta_app.command("export")
def export(
    ctx: typer.Context,
    format: Annotated[
        str,
        typer.Option(
            "--format",
            help=(
                "Output format. Only 'hledger' is supported today; "
                "'ledger' and 'beancount' are planned for #34."
            ),
        ),
    ] = "hledger",
    start: Annotated[
        str | None,
        typer.Option("--start", help="Inclusive earliest tx date (YYYY-MM-DD)."),
    ] = None,
    end: Annotated[
        str | None,
        typer.Option("--end", help="Inclusive latest tx date (YYYY-MM-DD)."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="Write the PTA text to this file instead of stdout.",
        ),
    ] = None,
) -> None:
    """Render the household ledger as plain-text accounting (hledger format)."""
    _validate_date(start, "--start")
    _validate_date(end, "--end")
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    params: dict[str, str] = {"format": format}
    if start is not None:
        params["start"] = start
    if end is not None:
        params["end"] = end
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get("/v1/pta/export", authenticated=True, params=params)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if output is not None:
        output.write_bytes(response.content)
        if not as_json:
            typer.echo(f"Wrote {len(response.content)} bytes to {output}")
        return
    sys.stdout.write(response.text)
    if not response.text.endswith("\n"):
        sys.stdout.write("\n")


@pta_app.command("import")
def import_(
    ctx: typer.Context,
    file_path: Annotated[
        Path,
        typer.Argument(
            help="Path to an hledger-format journal file to import.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            metavar="FILE",
        ),
    ],
) -> None:
    """Upload a PTA file; create PENDING transactions for review."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    body = file_path.read_bytes()
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post_raw(
                "/v1/pta/import",
                body=body,
                content_type="text/plain; charset=utf-8",
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    payload: dict[str, Any] = response.json()
    typer.echo(f"Imported {payload.get('created', 0)} PENDING transaction(s) from {file_path}.")


__all__ = ["pta_app"]

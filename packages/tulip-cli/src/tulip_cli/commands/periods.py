"""``tulip periods`` — list, close, reopen accounting periods (#136).

Soft-close is the v1 model: closed periods reject new transactions via
the existing ``period.closed`` 400 path. This module just exposes the
status-flip surface so users can run a month-end loop without falling
back to the JSON API.
"""

from __future__ import annotations

import sys
from typing import Annotated
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

periods_app = typer.Typer(
    name="periods",
    help="List and soft-close / reopen accounting periods.",
    no_args_is_help=True,
)


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


def _render_table(periods: list[dict[str, object]]) -> None:
    table = Table(show_header=True, show_lines=False)
    table.add_column("id")
    table.add_column("start")
    table.add_column("end")
    table.add_column("status")
    table.add_column("closed_at")
    for p in periods:
        status_str = str(p.get("status", ""))
        if status_str == "soft_closed":
            status_styled = f"[red]{status_str}[/red]"
        else:
            status_styled = f"[green]{status_str}[/green]"
        table.add_row(
            str(p.get("id", "")),
            str(p.get("start_date", "")),
            str(p.get("end_date", "")),
            status_styled,
            str(p.get("closed_at") or "—"),
        )
    Console().print(table)


@periods_app.command("list")
def list_periods(ctx: typer.Context) -> None:
    """List all periods for the household, newest first."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get("/v1/periods", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    periods = response.json()
    if not periods:
        typer.echo("No periods.")
        return
    _render_table(periods)


@periods_app.command("close")
def close_period(
    ctx: typer.Context,
    period_id: Annotated[UUID, typer.Argument(help="Period UUID to soft-close.")],
) -> None:
    """Soft-close a period. Idempotent on already-closed periods."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post(f"/v1/periods/{period_id}/close", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    body = response.json()
    typer.echo(
        f"Period {body['id']} ({body['start_date']} to {body['end_date']}) is now {body['status']}."
    )


@periods_app.command("reopen")
def reopen_period(
    ctx: typer.Context,
    period_id: Annotated[UUID, typer.Argument(help="Period UUID to reopen.")],
) -> None:
    """Re-open a soft-closed period. Idempotent on already-open periods."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post(f"/v1/periods/{period_id}/reopen", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    body = response.json()
    typer.echo(
        f"Period {body['id']} ({body['start_date']} to {body['end_date']}) is now {body['status']}."
    )

"""``tulip notifications`` — daily-insights inbox (P6.3)."""

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

notifications_app = typer.Typer(
    name="notifications",
    help="List + dismiss daily-insights notifications.",
    no_args_is_help=True,
)


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


@notifications_app.command("list")
def list_notifications(
    ctx: typer.Context,
    include_dismissed: Annotated[
        bool,
        typer.Option(
            "--include-dismissed",
            help="Also show dismissed rows. Default: active only.",
        ),
    ] = False,
) -> None:
    """List the household's notifications, newest first."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get(
                "/v1/notifications",
                authenticated=True,
                params={"include_dismissed": "true"} if include_dismissed else None,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    rows = response.json()
    if not rows:
        typer.echo("Inbox empty.")
        return
    table = Table(show_header=True, show_lines=False)
    table.add_column("id")
    table.add_column("kind")
    table.add_column("severity")
    table.add_column("title")
    table.add_column("dismissed")
    for r in rows:
        sev = str(r.get("severity", ""))
        sev_styled = (
            f"[red]{sev}[/red]"
            if sev == "critical"
            else f"[yellow]{sev}[/yellow]"
            if sev == "warning"
            else sev
        )
        table.add_row(
            str(r.get("id", ""))[:8],
            str(r.get("kind", "")),
            sev_styled,
            str(r.get("title", "")),
            "yes" if r.get("dismissed_at") else "no",
        )
    Console().print(table)


@notifications_app.command("dismiss")
def dismiss_notification(
    ctx: typer.Context,
    notification_id: Annotated[UUID, typer.Argument(help="Notification UUID to dismiss.")],
) -> None:
    """Stamp the notification as handled. Idempotent on already-dismissed."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post(
                f"/v1/notifications/{notification_id}/dismiss",
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    body = response.json()
    typer.echo(f"Dismissed notification {body['id']} ({body['kind']}).")

"""``tulip register`` — create a new household and its first user."""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient


def register(
    ctx: typer.Context,
    email: Annotated[str, typer.Option("--email", prompt=True, help="Login email.")],
    display_name: Annotated[
        str,
        typer.Option("--display-name", prompt="Display name", help="Your display name."),
    ],
    household: Annotated[
        str,
        typer.Option("--household", prompt="Household name", help="Household name."),
    ],
    password_stdin: Annotated[
        bool,
        typer.Option(
            "--password-stdin",
            help="Read the password from stdin (one line, no confirmation). For scripts.",
        ),
    ] = False,
) -> None:
    """Create a new household and its first (admin) user."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    if password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)

    body = {
        "email": email,
        "password": password,
        "display_name": display_name,
        "household_name": household,
    }
    try:
        with TulipClient(config, as_json=as_json) as client:
            response = client.post("/v1/auth/register", json=body)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    payload = response.json()
    typer.echo(
        f"Registered {email} as {payload.get('role', 'user')} of household {household}.\n"
        "Run `tulip auth login` to sign in."
    )

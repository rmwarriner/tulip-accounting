"""Typer entry point for the Tulip CLI.

P3.1 ships the runtime skeleton only: ``--help``, ``--version``, the
``--json`` global flag, and a ``ping`` command that exercises the HTTP
client + error renderer end to end. Domain commands (auth, accounts,
transactions) land in subsequent slices.
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from tulip_cli import __version__
from tulip_cli.commands.accounts import accounts_app
from tulip_cli.commands.auth import auth_app
from tulip_cli.commands.balance import balance as balance_command
from tulip_cli.commands.register import register as register_command
from tulip_cli.commands.transactions import add as add_command
from tulip_cli.commands.transactions import transactions_app
from tulip_cli.config import Config, load_config
from tulip_cli.errors import EXIT_OK, CliError
from tulip_cli.http import TulipClient

app = typer.Typer(
    name="tulip",
    help="Tulip Accounting CLI — talk to a Tulip API server.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit(EXIT_OK)


@app.callback()
def _root(
    ctx: typer.Context,
    api_url: Annotated[
        str | None,
        typer.Option(
            "--api-url",
            help="Override the API base URL (otherwise TULIP_API_URL or the config file).",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit raw JSON to stdout instead of pretty output."),
    ] = False,
    _version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the CLI version and exit.",
        ),
    ] = None,
) -> None:
    """Resolve config and stash shared state on the Typer context."""
    config = load_config(api_url_override=api_url)
    ctx.ensure_object(dict)
    ctx.obj["config"] = config
    ctx.obj["json"] = json_output


@app.command()
def ping(ctx: typer.Context) -> None:
    """Hit the API's ``/health`` endpoint and report the result."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with TulipClient(config, as_json=as_json) as client:
            response = client.get("/health")
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    typer.echo(f"OK ({response.status_code}) — {config.api_url}")


app.command("register")(register_command)
app.command("balance")(balance_command)
app.command("add")(add_command)
app.add_typer(auth_app, name="auth")
app.add_typer(accounts_app, name="accounts")
app.add_typer(transactions_app, name="transactions")


if __name__ == "__main__":
    app()

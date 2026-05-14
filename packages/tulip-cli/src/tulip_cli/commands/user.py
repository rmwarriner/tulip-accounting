"""``tulip user`` — user-scoped commands: GDPR Art. 15 data export (#241)."""

from __future__ import annotations

import json
import sys

import typer

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

user_app = typer.Typer(
    name="user",
    help="User-scoped operations — currently your GDPR data export.",
    no_args_is_help=True,
)


@user_app.command("export")
def export(ctx: typer.Context) -> None:
    """Export everything Tulip holds about you (GDPR Art. 15 / CCPA access right).

    Prints the full JSON envelope to stdout — redirect it to a file to
    keep a copy. The access is recorded in the audit log.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with TulipClient(config, token_store=default_token_store(), as_json=as_json) as client:
            response = client.get("/v1/users/me/export", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None
    sys.stdout.write(json.dumps(response.json(), indent=2) + "\n")

"""``tulip household`` — household-admin commands: member data export (#241)."""

from __future__ import annotations

import json
import sys
from typing import Annotated
from uuid import UUID

import typer

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

household_app = typer.Typer(
    name="household",
    help="Household-admin operations — member data exports.",
    no_args_is_help=True,
)


@household_app.command("member-export")
def member_export(
    ctx: typer.Context,
    user_id: Annotated[
        UUID,
        typer.Argument(
            help="UUID of the household member to export.",
            metavar="USER_ID",
        ),
    ],
) -> None:
    """Admin: export everything Tulip holds about a member of your household.

    Fulfils a GDPR Art. 15 access request on a member's behalf. Prints
    the full JSON envelope to stdout; the access is recorded in the
    audit log, attributing both you and the subject.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with TulipClient(config, token_store=default_token_store(), as_json=as_json) as client:
            response = client.get(f"/v1/users/{user_id}/export", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None
    sys.stdout.write(json.dumps(response.json(), indent=2) + "\n")

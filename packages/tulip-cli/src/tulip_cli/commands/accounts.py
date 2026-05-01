"""``tulip accounts`` — list and show.

Read-only commands consume ``GET /v1/accounts`` and
``GET /v1/accounts/{id}``. Write paths (``add``, ``deactivate``) land in
P3.4 (#21).

The ``show`` command resolves an identifier as a UUID first and falls
back to a code lookup over the listed accounts. ``code`` has no
uniqueness constraint server-side, so duplicates produce an ambiguous-id
error rather than silently picking the first match.
"""

from __future__ import annotations

import json
import sys
from typing import Annotated, Any
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.config import Config
from tulip_cli.errors import EXIT_USER, CliError
from tulip_cli.http import TulipClient

accounts_app = typer.Typer(
    name="accounts",
    help="List and inspect accounts.",
    no_args_is_help=True,
)


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


def _ambiguous_code_problem(identifier: str, count: int) -> dict[str, object]:
    return {
        "type": "/.well-known/errors/account.ambiguous_code",
        "title": "Account code matches multiple accounts",
        "status": 0,
        "detail": (
            f"{count} accounts have code {identifier!r}. "
            "Use the account UUID to disambiguate, or rename one of the duplicates."
        ),
        "instance": "",
        "code": "account.ambiguous_code",
    }


def _not_found_problem(identifier: str) -> dict[str, object]:
    return {
        "type": "/.well-known/errors/account.not_found",
        "title": "Account not found",
        "status": 0,
        "detail": (
            f"No account with code or id {identifier!r} is visible to this user. "
            "Run `tulip accounts list` to see what's available."
        ),
        "instance": "",
        "code": "account.not_found",
    }


def _resolve_account(client: TulipClient, identifier: str) -> dict[str, Any]:
    """Return a single account dict by UUID or by ``code``.

    UUID-shaped strings are looked up via ``GET /v1/accounts/{id}``.
    Anything else is matched against the listed accounts' ``code`` field.
    """
    try:
        UUID(identifier)
    except ValueError:
        # Not a UUID — fall through to code lookup.
        pass
    else:
        response = client.get(f"/v1/accounts/{identifier}", authenticated=True)
        return dict(response.json())

    response = client.get("/v1/accounts", authenticated=True)
    accounts = response.json()
    matches = [a for a in accounts if a.get("code") == identifier]
    if not matches:
        raise CliError(
            problem=_not_found_problem(identifier),
            as_json=False,
            exit_code=EXIT_USER,
        )
    if len(matches) > 1:
        raise CliError(
            problem=_ambiguous_code_problem(identifier, len(matches)),
            as_json=False,
            exit_code=EXIT_USER,
        )
    return dict(matches[0])


def _render_table(accounts: list[dict[str, Any]]) -> None:
    """Render a list of account dicts as a Rich table to stdout."""
    table = Table(show_header=True, show_lines=False)
    table.add_column("code")
    table.add_column("name")
    table.add_column("type")
    table.add_column("currency")
    table.add_column("visibility")
    for a in accounts:
        table.add_row(
            a.get("code") or "—",
            a.get("name") or "",
            a.get("type") or "",
            a.get("currency") or "",
            a.get("visibility") or "",
        )
    Console().print(table)


def _render_account(account: dict[str, Any]) -> None:
    """Render a single account dict as a vertical key/value list."""
    typer.echo(f"id:           {account.get('id', '')}")
    typer.echo(f"code:         {account.get('code') or '—'}")
    typer.echo(f"name:         {account.get('name', '')}")
    typer.echo(f"type:         {account.get('type', '')}")
    if account.get("subtype"):
        typer.echo(f"subtype:      {account['subtype']}")
    typer.echo(f"currency:     {account.get('currency', '')}")
    typer.echo(f"visibility:   {account.get('visibility', '')}")
    typer.echo(f"is_active:    {account.get('is_active', '')}")
    if account.get("parent_account_id"):
        typer.echo(f"parent:       {account['parent_account_id']}")


@accounts_app.command("list")
def list_accounts(ctx: typer.Context) -> None:
    """List active accounts visible to the logged-in user."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get("/v1/accounts", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    accounts = response.json()
    if not accounts:
        typer.echo("No accounts. Run `tulip accounts add` to create one (P3.4 — coming soon).")
        return
    _render_table(accounts)


@accounts_app.command("show")
def show_account(
    ctx: typer.Context,
    identifier: Annotated[
        str,
        typer.Argument(
            help="Account code (e.g. assets:checking) or UUID.",
            metavar="ACCOUNT",
        ),
    ],
) -> None:
    """Show one account by code or UUID."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            account = _resolve_account(client, identifier)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(json.dumps(account) + "\n")
        return
    _render_account(account)

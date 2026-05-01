"""``tulip balance`` — single-account balance or trial-balance summary.

Without an argument, calls ``GET /v1/reports/trial-balance`` and renders
a per-account table plus per-currency totals. With an account code or
UUID, calls ``GET /v1/accounts/{id}/balance`` for that one account.

Both shapes accept ``--as-of YYYY-MM-DD`` to query a point in time.
"""

from __future__ import annotations

import sys
from datetime import date as date_type
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.commands.accounts import _resolve_account
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


def _render_account_balance(body: dict[str, Any]) -> None:
    """Render a single ``AccountBalanceRead`` body."""
    code = body.get("code") or "—"
    typer.echo(f"{code} — {body.get('name', '')}")
    typer.echo(f"  balance: {body.get('balance', '')} {body.get('currency', '')}")
    typer.echo(f"  as of:   {body.get('as_of', '')}")


def _render_trial_balance(body: dict[str, Any]) -> None:
    """Render a ``TrialBalanceRead`` body as a table + totals."""
    rows = body.get("rows") or []
    if not rows:
        typer.echo(f"No postings on or before {body.get('as_of', 'today')}.")
        return

    table = Table(title=f"Trial balance as of {body.get('as_of', '')}", show_header=True)
    table.add_column("code")
    table.add_column("name")
    table.add_column("type")
    table.add_column("currency")
    table.add_column("balance", justify="right")
    for r in rows:
        table.add_row(
            r.get("code") or "—",
            r.get("name") or "",
            r.get("type") or "",
            r.get("currency") or "",
            r.get("balance") or "",
        )
    console = Console()
    console.print(table)

    totals = body.get("totals_by_currency") or []
    for t in totals:
        currency = t.get("currency", "")
        debits = t.get("debits", "")
        credits = t.get("credits", "")
        marker = "✓" if debits == credits else "⚠"
        console.print(
            f"  {currency}: debits {debits}, credits {credits} {marker}",
        )


def balance(
    ctx: typer.Context,
    account: Annotated[
        str | None,
        typer.Argument(
            help="Account code (e.g. assets:cash) or UUID. Omit for a trial-balance summary.",
            metavar="ACCOUNT",
        ),
    ] = None,
    as_of: Annotated[
        str | None,
        typer.Option(
            "--as-of",
            help="Point-in-time date (YYYY-MM-DD). Defaults to today.",
        ),
    ] = None,
) -> None:
    """Show a single account's balance, or the household trial balance."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    if as_of is not None:
        try:
            date_type.fromisoformat(as_of)
        except ValueError as exc:
            raise typer.BadParameter("--as-of must be YYYY-MM-DD") from exc

    params = {"as_of": as_of} if as_of else None

    try:
        with _client(config, as_json=as_json) as client:
            if account is None:
                response = client.get(
                    "/v1/reports/trial-balance",
                    authenticated=True,
                    params=params,
                )
            else:
                resolved = _resolve_account(client, account)
                response = client.get(
                    f"/v1/accounts/{resolved['id']}/balance",
                    authenticated=True,
                    params=params,
                )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    body = response.json()
    if account is None:
        _render_trial_balance(body)
    else:
        _render_account_balance(body)

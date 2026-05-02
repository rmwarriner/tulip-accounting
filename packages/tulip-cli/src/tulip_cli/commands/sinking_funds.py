"""``tulip sinking-funds`` — CRUD over /v1/sinking-funds.

Mirror of :mod:`tulip_cli.commands.envelopes` with the goal-bounded field
set (``target_amount``, ``target_date``, ``contribution_strategy``,
``contribution_amount``). The API rejects ``contribution_amount`` for
``even_split`` / ``percentage_of_income`` strategies — the CLI doesn't
duplicate that validation, just renders the API's Problem Details.
"""

from __future__ import annotations

import json
import sys
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.commands._pools import _resolve_sinking_fund
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

sinking_funds_app = typer.Typer(
    name="sinking-funds",
    help="Create and inspect sinking funds.",
    no_args_is_help=True,
)


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


def _render_table(sfs: list[dict[str, Any]]) -> None:
    table = Table(show_header=True, show_lines=False)
    table.add_column("name")
    table.add_column("currency")
    table.add_column("target")
    table.add_column("target_date")
    table.add_column("strategy")
    for sf in sfs:
        table.add_row(
            sf.get("name") or "",
            sf.get("currency") or "",
            sf.get("target_amount") or "",
            sf.get("target_date") or "",
            sf.get("contribution_strategy") or "",
        )
    Console().print(table)


def _render_sinking_fund(sf: dict[str, Any], balance: dict[str, Any] | None = None) -> None:
    typer.echo(f"id:                     {sf.get('id', '')}")
    typer.echo(f"name:                   {sf.get('name', '')}")
    typer.echo(f"currency:               {sf.get('currency', '')}")
    typer.echo(f"visibility:             {sf.get('visibility', '')}")
    typer.echo(f"is_active:              {sf.get('is_active', '')}")
    typer.echo(f"target_amount:          {sf.get('target_amount', '')}")
    typer.echo(f"target_date:            {sf.get('target_date', '')}")
    typer.echo(f"contribution_strategy:  {sf.get('contribution_strategy', '')}")
    typer.echo(f"contribution_amount:    {sf.get('contribution_amount') or '—'}")
    if balance is not None:
        typer.echo(
            f"balance:                {balance.get('balance', '')} "
            f"(as of {balance.get('as_of', '')})"
        )


@sinking_funds_app.command("list")
def list_sinking_funds(ctx: typer.Context) -> None:
    """List active sinking funds visible to the logged-in user."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get("/v1/sinking-funds", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    sfs = response.json()
    if not sfs:
        typer.echo("No sinking funds. Run `tulip sinking-funds add` to create one.")
        return
    _render_table(sfs)


@sinking_funds_app.command("add")
def add_sinking_fund(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="Sinking-fund name.")],
    currency: Annotated[str, typer.Option("--currency", help="ISO 4217 code.")],
    target_amount: Annotated[
        str,
        typer.Option(
            "--target-amount",
            help="Goal amount in the chosen currency (e.g. 3000.00).",
        ),
    ],
    target_date: Annotated[
        str,
        typer.Option("--target-date", help="ISO date (YYYY-MM-DD)."),
    ],
    contribution_strategy: Annotated[
        str,
        typer.Option(
            "--contribution-strategy",
            help="One of: manual, even_split, percentage_of_income.",
        ),
    ],
    contribution_amount: Annotated[
        str | None,
        typer.Option(
            "--contribution-amount",
            help=(
                "Optional fixed contribution amount; only valid with "
                "strategy=manual. The API rejects it for the other strategies."
            ),
        ),
    ] = None,
    visibility: Annotated[
        str,
        typer.Option("--visibility", help="'shared' (default) or 'private'."),
    ] = "shared",
) -> None:
    """Create a new sinking fund."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    body: dict[str, Any] = {
        "name": name,
        "currency": currency,
        "target_amount": target_amount,
        "target_date": target_date,
        "contribution_strategy": contribution_strategy,
        "visibility": visibility,
    }
    if contribution_amount is not None:
        body["contribution_amount"] = contribution_amount

    try:
        with _client(config, as_json=as_json) as client:
            response = client.post("/v1/sinking-funds", json=body, authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    payload = response.json()
    typer.echo(f"Created sinking fund {payload.get('id', '')}")
    _render_sinking_fund(payload)


@sinking_funds_app.command("show")
def show_sinking_fund(
    ctx: typer.Context,
    identifier: Annotated[
        str,
        typer.Argument(help="Sinking-fund name or UUID.", metavar="SINKING_FUND"),
    ],
) -> None:
    """Show one sinking fund (header + derived balance)."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    balance: dict[str, Any] | None = None
    try:
        with _client(config, as_json=as_json) as client:
            sf = _resolve_sinking_fund(client, identifier)
            try:
                balance_response = client.get(
                    f"/v1/sinking-funds/{sf['id']}/balance",
                    authenticated=True,
                )
                balance = dict(balance_response.json())
            except CliError:
                balance = None
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        out = dict(sf)
        if balance is not None:
            out["balance_detail"] = balance
        sys.stdout.write(json.dumps(out) + "\n")
        return
    _render_sinking_fund(sf, balance=balance)


@sinking_funds_app.command("edit")
def edit_sinking_fund(
    ctx: typer.Context,
    identifier: Annotated[
        str,
        typer.Argument(help="Sinking-fund name or UUID.", metavar="SINKING_FUND"),
    ],
    name: Annotated[
        str | None,
        typer.Option("--name", help="New display name."),
    ] = None,
    visibility: Annotated[
        str | None,
        typer.Option("--visibility", help="'shared' or 'private'."),
    ] = None,
    target_amount: Annotated[
        str | None,
        typer.Option("--target-amount", help="New goal amount."),
    ] = None,
    target_date: Annotated[
        str | None,
        typer.Option("--target-date", help="New ISO date (YYYY-MM-DD)."),
    ] = None,
    contribution_strategy: Annotated[
        str | None,
        typer.Option(
            "--contribution-strategy",
            help="One of: manual, even_split, percentage_of_income.",
        ),
    ] = None,
    contribution_amount: Annotated[
        str | None,
        typer.Option(
            "--contribution-amount",
            help="New fixed contribution amount (only valid with manual).",
        ),
    ] = None,
) -> None:
    """Update mutable fields on a sinking fund."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if visibility is not None:
        body["visibility"] = visibility
    if target_amount is not None:
        body["target_amount"] = target_amount
    if target_date is not None:
        body["target_date"] = target_date
    if contribution_strategy is not None:
        body["contribution_strategy"] = contribution_strategy
    if contribution_amount is not None:
        body["contribution_amount"] = contribution_amount

    try:
        with _client(config, as_json=as_json) as client:
            target = _resolve_sinking_fund(client, identifier)
            response = client.patch(
                f"/v1/sinking-funds/{target['id']}",
                json=body,
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    payload = response.json()
    typer.echo(f"Updated sinking fund {payload.get('id', '')}")
    _render_sinking_fund(payload)


@sinking_funds_app.command("deactivate")
def deactivate_sinking_fund(
    ctx: typer.Context,
    identifier: Annotated[
        str,
        typer.Argument(help="Sinking-fund name or UUID.", metavar="SINKING_FUND"),
    ],
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip the interactive confirmation prompt.",
        ),
    ] = False,
) -> None:
    """Soft-delete (deactivate) a sinking fund. Admin-only on the API side."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    try:
        with _client(config, as_json=as_json) as client:
            target = _resolve_sinking_fund(client, identifier)
            if not yes:
                label = target.get("name") or str(target["id"])
                if not typer.confirm(
                    f"Deactivate sinking fund {label}? It will disappear from "
                    "`tulip sinking-funds list`.",
                    default=False,
                ):
                    typer.echo("Aborted; no changes made.")
                    return
            client.delete(f"/v1/sinking-funds/{target['id']}", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(json.dumps({"deactivated": str(target["id"])}) + "\n")
        return
    typer.echo(f"Deactivated sinking fund {target.get('name') or target['id']}.")

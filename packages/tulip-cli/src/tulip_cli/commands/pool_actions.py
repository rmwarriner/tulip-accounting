"""Top-level action commands: ``tulip refill``, ``tulip transfer``, ``tulip budget-inflow``.

These wrap the user-initiated shadow-tx endpoints from P4.1.b. Each
command resolves the relevant pool(s), POSTs the request, and prints the
new balance the API returns. ``--json`` passes through the raw API body.
"""

from __future__ import annotations

import sys
from typing import Annotated, Any

import typer

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.commands._pools import _resolve_envelope, _resolve_pool
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


def refill(
    ctx: typer.Context,
    envelope: Annotated[
        str,
        typer.Argument(help="Envelope name or UUID.", metavar="ENVELOPE"),
    ],
    amount: Annotated[
        str,
        typer.Option("--amount", help="Positive decimal amount to add (e.g. 250.00)."),
    ],
    date: Annotated[
        str,
        typer.Option("--date", help="ISO date for the refill (YYYY-MM-DD)."),
    ],
    description: Annotated[
        str,
        typer.Option(
            "--description",
            "-m",
            help="Human-readable description for the audit log.",
        ),
    ],
    memo: Annotated[
        str | None,
        typer.Option("--memo", help="Optional per-leg memo."),
    ] = None,
) -> None:
    """Refill an envelope from Unallocated.

    Posts a 2-leg shadow transaction (Unallocated -X / envelope +X). The
    Unallocated system pool for the envelope's currency is lazy-created
    if it doesn't yet exist in the household.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    body: dict[str, Any] = {
        "amount": amount,
        "date": date,
        "description": description,
    }
    if memo is not None:
        body["memo"] = memo

    try:
        with _client(config, as_json=as_json) as client:
            target = _resolve_envelope(client, envelope)
            response = client.post(
                f"/v1/envelopes/{target['id']}/refill",
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
    new_balance = payload.get("balance", "")
    typer.echo(
        f"Refilled {target.get('name') or target['id']} by {amount}; new balance: {new_balance}"
    )


def transfer(
    ctx: typer.Context,
    src: Annotated[
        str,
        typer.Option("--from", help="Source pool name or UUID."),
    ],
    dest: Annotated[
        str,
        typer.Option("--to", help="Destination pool name or UUID."),
    ],
    amount: Annotated[
        str,
        typer.Option("--amount", help="Positive decimal amount to move."),
    ],
    date: Annotated[
        str,
        typer.Option("--date", help="ISO date for the transfer (YYYY-MM-DD)."),
    ],
    description: Annotated[
        str,
        typer.Option(
            "--description",
            "-m",
            help="Human-readable description for the audit log.",
        ),
    ],
    memo: Annotated[
        str | None,
        typer.Option("--memo", help="Optional per-leg memo."),
    ] = None,
) -> None:
    """Move money between two pools.

    Source and destination must both be in the caller's household, both
    active, both visible, **same currency**, and **both user pools**
    (envelope or sinking fund — system-pool transfers are rejected). Same
    pool both sides is also rejected.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    body: dict[str, Any] = {
        "amount": amount,
        "date": date,
        "description": description,
    }
    if memo is not None:
        body["memo"] = memo

    try:
        with _client(config, as_json=as_json) as client:
            src_pool = _resolve_pool(client, src)
            dest_pool = _resolve_pool(client, dest)
            body["dest_pool_id"] = dest_pool["id"]
            response = client.post(
                f"/v1/pools/{src_pool['id']}/transfer",
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
    new_balance = payload.get("balance", "")
    typer.echo(
        f"Transferred {amount} from {src_pool.get('name') or src_pool['id']} "
        f"to {dest_pool.get('name') or dest_pool['id']}; "
        f"new destination balance: {new_balance}"
    )


def budget_inflow(
    ctx: typer.Context,
    amount: Annotated[
        str,
        typer.Option("--amount", help="Positive decimal amount of inflow to declare."),
    ],
    currency: Annotated[
        str,
        typer.Option("--currency", help="ISO 4217 three-letter code (e.g. USD)."),
    ],
    date: Annotated[
        str,
        typer.Option("--date", help="ISO date for the inflow (YYYY-MM-DD)."),
    ],
    description: Annotated[
        str,
        typer.Option(
            "--description",
            "-m",
            help="Human-readable description for the audit log.",
        ),
    ],
    memo: Annotated[
        str | None,
        typer.Option("--memo", help="Optional per-leg memo."),
    ] = None,
) -> None:
    """Declare new money available to budget.

    Posts a shadow transaction with reason ``budget_inflow`` of the form
    ``Inflow -X / Unallocated +X``. The household's Inflow / Unallocated
    / Spent system pools are lazy-created for the currency if any are
    missing — supports adding new currencies after household registration.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    body: dict[str, Any] = {
        "amount": amount,
        "currency": currency,
        "date": date,
        "description": description,
    }
    if memo is not None:
        body["memo"] = memo

    try:
        with _client(config, as_json=as_json) as client:
            response = client.post("/v1/pools/budget-inflow", json=body, authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    payload = response.json()
    new_balance = payload.get("balance", "")
    typer.echo(f"Declared inflow of {amount} {currency}; new Unallocated balance: {new_balance}")

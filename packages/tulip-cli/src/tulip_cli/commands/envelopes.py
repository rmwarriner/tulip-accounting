"""``tulip envelopes`` — CRUD over /v1/envelopes.

Mirrors :mod:`tulip_cli.commands.accounts`'s shape: list / show / add /
edit / deactivate. Identifier resolution falls back to ``name`` instead
of ``code`` (envelopes have no code field). The ``balance`` sub-route is
fetched on ``show`` only — list intentionally skips per-row balance
fetches to avoid N+1 calls against a household with many envelopes.

Refill rules are intentionally not exposed as CLI flags in P4.2; the
structured-only constraint from ADR-0001 calls for an editor flow that
lands as a follow-up.
"""

from __future__ import annotations

import json
import sys
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.commands._pools import _resolve_envelope, _summarize_refill_rule
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

envelopes_app = typer.Typer(
    name="envelopes",
    help="Create and inspect budgeting envelopes.",
    no_args_is_help=True,
)


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


def _render_table(
    envelopes: list[dict[str, Any]],
    balances: dict[str, str] | None = None,
) -> None:
    """Render envelopes as a Rich table; balance comes from the batched lookup (#137)."""
    balances = balances or {}
    table = Table(show_header=True, show_lines=False)
    table.add_column("name")
    table.add_column("currency")
    table.add_column("period")
    table.add_column("rollover")
    table.add_column("budget")
    table.add_column("balance", justify="right")
    table.add_column("refill")
    for env in envelopes:
        table.add_row(
            env.get("name") or "",
            env.get("currency") or "",
            env.get("budget_period") or "",
            env.get("rollover_policy") or "",
            env.get("budget_amount") or "—",
            balances.get(env.get("id", ""), "—"),
            _summarize_refill_rule(env.get("refill_rule")),
        )
    Console().print(table)


def _fetch_balances(client: TulipClient, pool_ids: list[str]) -> dict[str, str]:
    """POST /v1/pools/balances and flatten to ``{pool_id: balance_str}``. Empty on no ids."""
    if not pool_ids:
        return {}
    response = client.post(
        "/v1/pools/balances",
        json={"pool_ids": pool_ids},
        authenticated=True,
    )
    return {row["pool_id"]: str(row["balance"]) for row in response.json()}


def _render_envelope(envelope: dict[str, Any], balance: dict[str, Any] | None = None) -> None:
    """Render a single envelope vertically; balance is shown on its own line if present."""
    typer.echo(f"id:               {envelope.get('id', '')}")
    typer.echo(f"name:             {envelope.get('name', '')}")
    typer.echo(f"currency:         {envelope.get('currency', '')}")
    typer.echo(f"visibility:       {envelope.get('visibility', '')}")
    typer.echo(f"is_active:        {envelope.get('is_active', '')}")
    typer.echo(f"budget_period:    {envelope.get('budget_period', '')}")
    typer.echo(f"rollover_policy:  {envelope.get('rollover_policy', '')}")
    typer.echo(f"budget_amount:    {envelope.get('budget_amount') or '—'}")
    if envelope.get("refill_rule") is not None:
        typer.echo(f"refill_rule:      {json.dumps(envelope['refill_rule'])}")
    if balance is not None:
        typer.echo(
            f"balance:          {balance.get('balance', '')} (as of {balance.get('as_of', '')})"
        )


@envelopes_app.command("list")
def list_envelopes(ctx: typer.Context) -> None:
    """List active envelopes visible to the logged-in user (#137: with inline balances)."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get("/v1/envelopes", authenticated=True)
            envelopes = response.json()
            balances = _fetch_balances(client, [e["id"] for e in envelopes])
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        for env in envelopes:
            env["balance"] = balances.get(env.get("id", ""))
        sys.stdout.write(json.dumps(envelopes) + "\n")
        return

    if not envelopes:
        typer.echo("No envelopes. Run `tulip envelopes add` to create one.")
        return
    _render_table(envelopes, balances)


@envelopes_app.command("add")
def add_envelope(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="Envelope name.")],
    currency: Annotated[
        str,
        typer.Option("--currency", help="ISO 4217 three-letter code (e.g. USD)."),
    ],
    budget_period: Annotated[
        str,
        typer.Option(
            "--budget-period",
            help="One of: weekly, biweekly, monthly, quarterly, annual, custom.",
        ),
    ],
    rollover_policy: Annotated[
        str,
        typer.Option(
            "--rollover-policy",
            help="One of: reset, accumulate, cap_at_budget.",
        ),
    ],
    budget_amount: Annotated[
        str | None,
        typer.Option(
            "--budget-amount",
            help="Optional decimal amount per period (e.g. 250.00).",
        ),
    ] = None,
    visibility: Annotated[
        str,
        typer.Option("--visibility", help="'shared' (default) or 'private'."),
    ] = "shared",
) -> None:
    """Create a new envelope."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    body: dict[str, Any] = {
        "name": name,
        "currency": currency,
        "budget_period": budget_period,
        "rollover_policy": rollover_policy,
        "visibility": visibility,
    }
    if budget_amount is not None:
        body["budget_amount"] = budget_amount

    try:
        with _client(config, as_json=as_json) as client:
            response = client.post("/v1/envelopes", json=body, authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    payload = response.json()
    typer.echo(f"Created envelope {payload.get('id', '')}")
    _render_envelope(payload)


@envelopes_app.command("show")
def show_envelope(
    ctx: typer.Context,
    identifier: Annotated[
        str,
        typer.Argument(help="Envelope name or UUID.", metavar="ENVELOPE"),
    ],
) -> None:
    """Show one envelope (header + derived balance)."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    balance: dict[str, Any] | None = None
    try:
        with _client(config, as_json=as_json) as client:
            envelope = _resolve_envelope(client, identifier)
            try:
                balance_response = client.get(
                    f"/v1/envelopes/{envelope['id']}/balance",
                    authenticated=True,
                )
                balance = dict(balance_response.json())
            except CliError:
                # Graceful: render the envelope without balance rather than
                # crashing if the balance endpoint fails. Mirrors how
                # `accounts show` handles parent-fetch failures.
                balance = None
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        out = dict(envelope)
        if balance is not None:
            out["balance_detail"] = balance
        sys.stdout.write(json.dumps(out) + "\n")
        return
    _render_envelope(envelope, balance=balance)


@envelopes_app.command("edit")
def edit_envelope(
    ctx: typer.Context,
    identifier: Annotated[
        str,
        typer.Argument(help="Envelope name or UUID.", metavar="ENVELOPE"),
    ],
    name: Annotated[
        str | None,
        typer.Option("--name", help="New display name."),
    ] = None,
    visibility: Annotated[
        str | None,
        typer.Option("--visibility", help="'shared' or 'private'."),
    ] = None,
    budget_period: Annotated[
        str | None,
        typer.Option(
            "--budget-period",
            help="One of: weekly, biweekly, monthly, quarterly, annual, custom.",
        ),
    ] = None,
    budget_amount: Annotated[
        str | None,
        typer.Option(
            "--budget-amount",
            help="New decimal amount per period (e.g. 250.00).",
        ),
    ] = None,
    rollover_policy: Annotated[
        str | None,
        typer.Option(
            "--rollover-policy",
            help="One of: reset, accumulate, cap_at_budget.",
        ),
    ] = None,
) -> None:
    """Update mutable fields on an envelope. Only explicitly-passed flags are sent."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if visibility is not None:
        body["visibility"] = visibility
    if budget_period is not None:
        body["budget_period"] = budget_period
    if budget_amount is not None:
        body["budget_amount"] = budget_amount
    if rollover_policy is not None:
        body["rollover_policy"] = rollover_policy

    try:
        with _client(config, as_json=as_json) as client:
            target = _resolve_envelope(client, identifier)
            response = client.patch(
                f"/v1/envelopes/{target['id']}",
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
    typer.echo(f"Updated envelope {payload.get('id', '')}")
    _render_envelope(payload)


@envelopes_app.command("deactivate")
def deactivate_envelope(
    ctx: typer.Context,
    identifier: Annotated[
        str,
        typer.Argument(help="Envelope name or UUID.", metavar="ENVELOPE"),
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
    """Soft-delete (deactivate) an envelope. Admin-only on the API side."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    try:
        with _client(config, as_json=as_json) as client:
            target = _resolve_envelope(client, identifier)
            if not yes:
                label = target.get("name") or str(target["id"])
                if not typer.confirm(
                    f"Deactivate envelope {label}? It will disappear from `tulip envelopes list`.",
                    default=False,
                ):
                    typer.echo("Aborted; no changes made.")
                    return
            client.delete(f"/v1/envelopes/{target['id']}", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(json.dumps({"deactivated": str(target["id"])}) + "\n")
        return
    typer.echo(f"Deactivated envelope {target.get('name') or target['id']}.")

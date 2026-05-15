"""``tulip refills`` — schedule, list, cancel, and trigger envelope refill schedules.

Surfaces the P4.3.c API endpoints (#70). Each command resolves the
envelope by UUID-or-name (mirroring ``tulip envelopes``), then issues
the corresponding REST call.
"""

from __future__ import annotations

import json
import sys
from typing import Annotated

import typer
from rich.table import Table

from tulip_cli._console import make_console
from tulip_cli.auth.tokens import default_token_store
from tulip_cli.commands._pools import _resolve_envelope
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

refills_app = typer.Typer(
    name="refills",
    help="Manage envelope refill schedules.",
    no_args_is_help=True,
)


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


@refills_app.command("schedule")
def schedule_refill(
    ctx: typer.Context,
    identifier: Annotated[
        str,
        typer.Argument(help="Envelope name or UUID.", metavar="ENVELOPE"),
    ],
    rrule: Annotated[
        str,
        typer.Option(
            "--rrule",
            help=(
                "RFC 5545 RRULE string (e.g. 'FREQ=MONTHLY;BYMONTHDAY=1'). Validated server-side."
            ),
        ),
    ],
    start_at: Annotated[
        str,
        typer.Option(
            "--start",
            help=("ISO 8601 datetime for the schedule anchor (e.g. '2026-06-01T00:00:00+00:00')."),
        ),
    ],
) -> None:
    """Register a recurring auto-refill for an envelope.

    The envelope must already have a ``refill_rule`` set
    (via ``tulip envelopes add --refill-rule`` or PATCH the envelope).
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            envelope = _resolve_envelope(client, identifier)
            response = client.post(
                f"/v1/envelopes/{envelope['id']}/refill-schedule",
                json={"rrule": rrule, "start_at": start_at},
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    payload = response.json()
    typer.echo(
        f"Scheduled {envelope.get('name') or envelope['id']} "
        f"with RRULE {rrule!r}; next run: {payload.get('next_run_at', '')}"
    )


@refills_app.command("list")
def list_refills(ctx: typer.Context) -> None:
    """List all scheduled jobs in the household (cross-kind, ops view)."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get("/v1/scheduled-jobs", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    jobs = response.json()
    if not jobs:
        typer.echo("No scheduled jobs. Use `tulip refills schedule ENVELOPE` to add one.")
        return

    table = Table(show_header=True, show_lines=False)
    table.add_column("kind")
    table.add_column("rrule")
    table.add_column("next_run_at")
    table.add_column("last_run_at")
    table.add_column("idempotency_key")
    for j in jobs:
        table.add_row(
            j.get("kind") or "",
            j.get("rrule") or "—",
            str(j.get("next_run_at") or ""),
            str(j.get("last_run_at") or "—"),
            j.get("idempotency_key") or "—",
        )
    make_console().print(table)


@refills_app.command("show")
def show_refill(
    ctx: typer.Context,
    identifier: Annotated[
        str,
        typer.Argument(help="Envelope name or UUID.", metavar="ENVELOPE"),
    ],
) -> None:
    """Show the active refill schedule for an envelope, if any."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            envelope = _resolve_envelope(client, identifier)
            response = client.get(
                f"/v1/envelopes/{envelope['id']}/refill-schedule",
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    payload = response.json()
    typer.echo(f"id:           {payload.get('id', '')}")
    typer.echo(f"envelope_id:  {payload.get('envelope_id', '')}")
    typer.echo(f"rrule:        {payload.get('rrule', '')}")
    typer.echo(f"dtstart:      {payload.get('dtstart', '')}")
    typer.echo(f"next_run_at:  {payload.get('next_run_at', '')}")
    typer.echo(f"last_run_at:  {payload.get('last_run_at') or '—'}")
    typer.echo(f"is_active:    {payload.get('is_active', '')}")


@refills_app.command("cancel")
def cancel_refill(
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
    """Cancel an envelope's refill schedule."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            envelope = _resolve_envelope(client, identifier)
            if not yes:
                label = envelope.get("name") or str(envelope["id"])
                if not typer.confirm(
                    f"Cancel refill schedule for envelope {label}? Future auto-refills will stop.",
                    default=False,
                ):
                    typer.echo("Aborted; schedule unchanged.")
                    return
            client.delete(
                f"/v1/envelopes/{envelope['id']}/refill-schedule",
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(json.dumps({"cancelled": str(envelope["id"])}) + "\n")
        return
    typer.echo(f"Cancelled refill schedule for {envelope.get('name') or envelope['id']}.")


@refills_app.command("run-due")
def run_due(ctx: typer.Context) -> None:
    """Force the runner to fire any jobs that are due now (admin only)."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post("/v1/scheduled-jobs/run-due", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    fired = response.json().get("fired", 0)
    typer.echo(f"Ran {fired} due job(s).")

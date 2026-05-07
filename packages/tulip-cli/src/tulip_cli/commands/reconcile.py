"""``tulip reconcile`` — server-side reconciliation envelope CRUD via CLI (P5.4.d).

Wraps the /v1/reconciliations endpoints. Per the locked decisions:

- imperative subcommands (no Textual TUI for v1)
- ``--line`` / ``--tx`` / ``--batch`` accept UUIDs only (no human-friendly
  identifiers); ``--account`` reuses the resolver from ``commands.accounts``
  so UUID-or-code works
- ``--tx UUID`` is repeatable for ``carry-forward``
- ``tulip reconcile show`` always renders all four sections (envelope +
  matches + unmatched lines + unmatched ledger txs); empty sections render
  ``(none)`` rather than being omitted
"""

from __future__ import annotations

import json as _json
import sys
from datetime import date as _date
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.commands.accounts import _resolve_account
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

reconcile_app = typer.Typer(
    name="reconcile",
    help=(
        "Manage reconciliation envelopes (open, auto-match, manually match, "
        "carry-forward, complete)."
    ),
    no_args_is_help=True,
)


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


def _parse_period(value: str) -> tuple[_date, _date]:
    """Parse ``--period START..END`` into a ``(start_date, end_date)`` tuple.

    Format: ``YYYY-MM-DD..YYYY-MM-DD``. Used as a Typer parameter callback.
    """
    if value is None:
        raise typer.BadParameter("--period is required (format: YYYY-MM-DD..YYYY-MM-DD)")
    try:
        start_str, end_str = value.split("..", 1)
        start = _date.fromisoformat(start_str.strip())
        end = _date.fromisoformat(end_str.strip())
    except ValueError as exc:
        raise typer.BadParameter(
            f"--period must be 'YYYY-MM-DD..YYYY-MM-DD' (got {value!r})"
        ) from exc
    if start > end:
        raise typer.BadParameter(f"--period start ({start}) must be <= end ({end})")
    return start, end


def _render_section_header(console: Console, title: str, count: int) -> None:
    console.print(f"\n[bold]{title}[/bold] ({count})")


def _render_envelope(console: Console, recon: dict[str, Any]) -> None:
    console.print(f"[bold]Reconciliation[/bold] {recon['id']}")
    console.print(
        f"  account: {recon['account_id']}  currency: {recon['currency']}  "
        f"status: {recon['status']}"
    )
    console.print(f"  period: {recon['statement_period_start']}..{recon['statement_period_end']}")
    console.print(
        f"  starting: {recon['statement_starting_balance']}  "
        f"ending: {recon['statement_ending_balance']}"
    )
    if recon.get("completed_at"):
        console.print(f"  completed_at: {recon['completed_at']}")


def _render_matches(console: Console, matches: list[dict[str, Any]]) -> None:
    _render_section_header(console, "Matches", len(matches))
    if not matches:
        console.print("  (none)")
        return
    table = Table()
    table.add_column("match_id")
    table.add_column("line_id")
    table.add_column("tx_id")
    table.add_column("amount")
    table.add_column("confidence")
    table.add_column("source")
    for m in matches:
        source = "auto" if m.get("matcher_version") else "manual"
        table.add_row(
            m["id"],
            m["statement_line_id"],
            m["ledger_transaction_id"],
            str(m["match_amount"]),
            (m.get("confidence") or "—"),
            source,
        )
    console.print(table)


def _render_unmatched_lines(console: Console, lines: list[dict[str, Any]]) -> None:
    _render_section_header(console, "Unmatched statement lines", len(lines))
    if not lines:
        console.print("  (none)")
        return
    table = Table()
    table.add_column("line_id")
    table.add_column("date")
    table.add_column("amount")
    table.add_column("description")
    for line in lines:
        table.add_row(
            line["id"],
            str(line["posted_date"]),
            str(line["amount"]),
            line["description"][:60],
        )
    console.print(table)


def _render_unmatched_txs(console: Console, txs: list[dict[str, Any]]) -> None:
    _render_section_header(console, "Unmatched ledger transactions", len(txs))
    if not txs:
        console.print("  (none)")
        return
    table = Table()
    table.add_column("tx_id")
    table.add_column("date")
    table.add_column("description")
    table.add_column("status")
    for tx in txs:
        table.add_row(
            tx["id"],
            str(tx["date"]),
            tx["description"][:60],
            tx["status"],
        )
    console.print(table)


def _render_inbox(body: dict[str, Any]) -> None:
    """Render the four-section reconciliation review pane."""
    console = Console()
    _render_envelope(console, body["reconciliation"])
    _render_matches(console, body["matches"])
    _render_unmatched_lines(console, body["unmatched_statement_lines"])
    _render_unmatched_txs(console, body["unmatched_ledger_transactions"])


# ---- create ---------------------------------------------------------------


@reconcile_app.command("create")
def create_command(
    ctx: typer.Context,
    account: Annotated[
        str,
        typer.Option(
            "--account",
            help="Account this reconciliation belongs to. UUID or code.",
        ),
    ],
    batch: Annotated[
        UUID,
        typer.Option(
            "--batch",
            help="Source import batch UUID (returned by `tulip imports ofx/qif/csv`).",
        ),
    ],
    period: Annotated[
        str,
        typer.Option(
            "--period",
            help="Statement period as YYYY-MM-DD..YYYY-MM-DD.",
        ),
    ],
    starting: Annotated[
        str,
        typer.Option(
            "--starting",
            help="Statement starting balance (decimal).",
        ),
    ],
    ending: Annotated[
        str,
        typer.Option(
            "--ending",
            help="Statement ending balance (decimal).",
        ),
    ],
    currency: Annotated[
        str,
        typer.Option("--currency", help="Currency code (3 chars)."),
    ] = "USD",
) -> None:
    """Open a new reconciliation envelope."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    period_start, period_end = _parse_period(period)
    try:
        starting_dec = Decimal(starting)
        ending_dec = Decimal(ending)
    except (ValueError, ArithmeticError) as exc:
        raise typer.BadParameter(
            f"--starting / --ending must be decimal numbers (got {starting!r}, {ending!r})"
        ) from exc
    try:
        with _client(config, as_json=as_json) as client:
            account_record = _resolve_account(client, account)
            response = client.post(
                "/v1/reconciliations",
                authenticated=True,
                json={
                    "account_id": str(account_record["id"]),
                    "source_import_batch_id": str(batch),
                    "statement_period_start": period_start.isoformat(),
                    "statement_period_end": period_end.isoformat(),
                    "statement_starting_balance": str(starting_dec),
                    "statement_ending_balance": str(ending_dec),
                    "currency": currency,
                },
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    body = response.json()
    typer.echo(
        f"Created reconciliation {body['id']} for account "
        f"{account_record.get('code') or account_record['id']} "
        f"({body['statement_period_start']}..{body['statement_period_end']})."
    )


# ---- list -----------------------------------------------------------------


@reconcile_app.command("list")
def list_command(
    ctx: typer.Context,
    account: Annotated[
        str | None,
        typer.Option(
            "--account",
            help="Filter to one account (UUID or code).",
        ),
    ] = None,
    status: Annotated[
        str | None,
        typer.Option(
            "--status",
            help="Filter to one status (in_progress / complete / abandoned).",
        ),
    ] = None,
) -> None:
    """List reconciliations, newest statement period first."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    params: dict[str, str] = {}
    try:
        with _client(config, as_json=as_json) as client:
            if account is not None:
                account_record = _resolve_account(client, account)
                params["account_id"] = str(account_record["id"])
            if status is not None:
                params["status"] = status
            response = client.get(
                "/v1/reconciliations",
                authenticated=True,
                params=params,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    items = response.json()["items"]
    if not items:
        typer.echo("No reconciliations.")
        return
    table = Table()
    table.add_column("id")
    table.add_column("account_id")
    table.add_column("period")
    table.add_column("status")
    table.add_column("ending")
    for item in items:
        table.add_row(
            item["id"],
            item["account_id"],
            f"{item['statement_period_start']}..{item['statement_period_end']}",
            item["status"],
            str(item["statement_ending_balance"]),
        )
    Console().print(table)


# ---- show -----------------------------------------------------------------


@reconcile_app.command("show")
def show_command(
    ctx: typer.Context,
    reconciliation_id: Annotated[
        UUID,
        typer.Argument(help="Reconciliation UUID.", metavar="RECONCILIATION_ID"),
    ],
) -> None:
    """Show the reconciliation envelope + the four-section review pane."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.get(f"/v1/reconciliations/{reconciliation_id}", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    _render_inbox(response.json())


# ---- auto-match -----------------------------------------------------------


@reconcile_app.command("auto-match")
def auto_match_command(
    ctx: typer.Context,
    reconciliation_id: Annotated[
        UUID,
        typer.Argument(help="Reconciliation UUID.", metavar="RECONCILIATION_ID"),
    ],
) -> None:
    """Run the matcher; persist candidate matches."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post(
                f"/v1/reconciliations/{reconciliation_id}/auto-match",
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    body = response.json()
    summary = body["candidate_summary"]
    typer.echo(
        f"Auto-matched: {body['matches_created']} matches "
        f"(high={summary['high']}, medium={summary['medium']}, low={summary['low']})."
    )


# ---- match (manual) -------------------------------------------------------


@reconcile_app.command("match")
def match_command(
    ctx: typer.Context,
    reconciliation_id: Annotated[
        UUID,
        typer.Argument(help="Reconciliation UUID.", metavar="RECONCILIATION_ID"),
    ],
    line: Annotated[UUID, typer.Option("--line", help="Statement line UUID.")],
    tx: Annotated[UUID, typer.Option("--tx", help="Ledger transaction UUID.")],
    amount: Annotated[
        str,
        typer.Option("--amount", help="Match amount (must equal line.amount)."),
    ],
    currency: Annotated[
        str,
        typer.Option("--currency", help="Currency code (3 chars)."),
    ] = "USD",
) -> None:
    """Create a manual match between a statement line and a ledger transaction."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        amount_dec = Decimal(amount)
    except (ValueError, ArithmeticError) as exc:
        raise typer.BadParameter(f"--amount must be a decimal number (got {amount!r})") from exc
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post(
                f"/v1/reconciliations/{reconciliation_id}/matches",
                authenticated=True,
                json={
                    "statement_line_id": str(line),
                    "ledger_transaction_id": str(tx),
                    "match_amount": str(amount_dec),
                    "currency": currency,
                },
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    body = response.json()
    typer.echo(
        f"Manual match created: {body['id']} "
        f"(line {body['statement_line_id']} ↔ tx {body['ledger_transaction_id']})."
    )


# ---- reject ---------------------------------------------------------------


@reconcile_app.command("reject")
def reject_command(
    ctx: typer.Context,
    reconciliation_id: Annotated[
        UUID,
        typer.Argument(help="Reconciliation UUID.", metavar="RECONCILIATION_ID"),
    ],
    match_id: Annotated[
        UUID,
        typer.Argument(help="Match UUID to reject.", metavar="MATCH_ID"),
    ],
) -> None:
    """Reject (delete) a single match. The line + transaction return to unmatched."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            client.post(
                f"/v1/reconciliations/{reconciliation_id}/matches/{match_id}/reject",
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(_json.dumps({"rejected": str(match_id)}) + "\n")
        return
    typer.echo(f"Rejected match {match_id}.")


# ---- carry-forward --------------------------------------------------------


@reconcile_app.command("carry-forward")
def carry_forward_command(
    ctx: typer.Context,
    reconciliation_id: Annotated[
        UUID,
        typer.Argument(help="Reconciliation UUID.", metavar="RECONCILIATION_ID"),
    ],
    tx: Annotated[
        list[UUID],
        typer.Option(
            "--tx",
            help="Ledger transaction UUID to carry forward. Repeat for multiple.",
        ),
    ],
) -> None:
    """Mark in-period ledger transactions as carry-forward to the next reconciliation."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post(
                f"/v1/reconciliations/{reconciliation_id}/carry-forward",
                authenticated=True,
                json={"transaction_ids": [str(t) for t in tx]},
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    body = response.json()
    typer.echo(
        f"Carried forward {len(body['transaction_ids'])} transaction(s) "
        f"in reconciliation {body['reconciliation_id']}."
    )


# ---- carry-forward-remove -------------------------------------------------


@reconcile_app.command("carry-forward-remove")
def carry_forward_remove_command(
    ctx: typer.Context,
    reconciliation_id: Annotated[
        UUID,
        typer.Argument(help="Reconciliation UUID.", metavar="RECONCILIATION_ID"),
    ],
    transaction_id: Annotated[
        UUID,
        typer.Argument(help="Transaction UUID to un-carry-forward.", metavar="TRANSACTION_ID"),
    ],
) -> None:
    """Un-mark a transaction's carry-forward link."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            client.delete(
                f"/v1/reconciliations/{reconciliation_id}/carry-forward/{transaction_id}",
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(_json.dumps({"removed_carry_forward": str(transaction_id)}) + "\n")
        return
    typer.echo(f"Removed carry-forward for transaction {transaction_id}.")


# ---- complete -------------------------------------------------------------


@reconcile_app.command("complete")
def complete_command(
    ctx: typer.Context,
    reconciliation_id: Annotated[
        UUID,
        typer.Argument(help="Reconciliation UUID.", metavar="RECONCILIATION_ID"),
    ],
) -> None:
    """Finalise the reconciliation; denormalise reconciled_at onto matched txs."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            response = client.post(
                f"/v1/reconciliations/{reconciliation_id}/complete",
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    body = response.json()
    typer.echo(
        f"Completed reconciliation {body['reconciliation_id']}: "
        f"{body['affected_transaction_count']} transaction(s) marked reconciled."
    )


# ---- delete ---------------------------------------------------------------


@reconcile_app.command("delete")
def delete_command(
    ctx: typer.Context,
    reconciliation_id: Annotated[
        UUID,
        typer.Argument(help="Reconciliation UUID.", metavar="RECONCILIATION_ID"),
    ],
    cascade: Annotated[
        bool,
        typer.Option(
            "--cascade",
            help="Required: confirms cascade-deletion of matches and nulling of "
            "transactions.reconciliation_id + reconciled_at.",
        ),
    ] = False,
) -> None:
    """Un-reconcile: delete matches, null tx denorms, delete the envelope."""
    if not cascade:
        typer.echo(
            "Reverting a reconciliation is destructive. Pass --cascade to confirm.",
            err=True,
        )
        raise typer.Exit(2)

    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        with _client(config, as_json=as_json) as client:
            client.delete(
                f"/v1/reconciliations/{reconciliation_id}?cascade=true",
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(_json.dumps({"deleted": str(reconciliation_id)}) + "\n")
        return
    typer.echo(f"Deleted reconciliation {reconciliation_id}.")

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


# ---- paper-statement (no-OFX) flow (#275) ---------------------------------


@reconcile_app.command("start")
def start_command(
    ctx: typer.Context,
    account: Annotated[
        str,
        typer.Option(
            "--account",
            help="Account this reconciliation belongs to. UUID or code.",
        ),
    ],
    statement_date: Annotated[
        str,
        typer.Option(
            "--statement-date",
            help=(
                "Statement period end date (YYYY-MM-DD). Period start defaults "
                "to the previous reconciliation's end + 1 day (or 30 days "
                "prior if no prior reconciliation)."
            ),
        ),
    ],
    closing_balance: Annotated[
        str,
        typer.Option(
            "--closing-balance",
            help="Statement ending balance (decimal).",
        ),
    ],
    starting_balance: Annotated[
        str,
        typer.Option(
            "--starting-balance",
            help=(
                "Statement starting balance (decimal). Defaults to '0.00' — "
                "first paper reconciliation should set this to the account's "
                "opening balance on --statement-date - 30d."
            ),
        ),
    ] = "0.00",
    period_start: Annotated[
        str | None,
        typer.Option(
            "--period-start",
            help=(
                "Override the period start date (YYYY-MM-DD). Defaults to "
                "30 days before --statement-date."
            ),
        ),
    ] = None,
    currency: Annotated[
        str,
        typer.Option("--currency", help="Currency code (3 chars)."),
    ] = "USD",
) -> None:
    """Open a paper-statement reconciliation envelope (no imported batch).

    Per issue #275: the user has a physical statement and wants to tick
    off ledger transactions one at a time. No --batch — the reconciliation
    is opened with ``source_import_batch_id IS NULL``.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    try:
        end_date = _date.fromisoformat(statement_date)
    except ValueError as exc:
        raise typer.BadParameter(
            f"--statement-date must be YYYY-MM-DD (got {statement_date!r})"
        ) from exc
    if period_start is None:
        from datetime import timedelta

        start_date = end_date - timedelta(days=30)
    else:
        try:
            start_date = _date.fromisoformat(period_start)
        except ValueError as exc:
            raise typer.BadParameter(
                f"--period-start must be YYYY-MM-DD (got {period_start!r})"
            ) from exc
    if start_date > end_date:
        raise typer.BadParameter(
            f"--period-start ({start_date}) must be <= --statement-date ({end_date})"
        )
    try:
        starting_dec = Decimal(starting_balance)
        ending_dec = Decimal(closing_balance)
    except (ValueError, ArithmeticError) as exc:
        raise typer.BadParameter(
            f"--starting-balance / --closing-balance must be decimal numbers "
            f"(got {starting_balance!r}, {closing_balance!r})"
        ) from exc
    try:
        with _client(config, as_json=as_json) as client:
            account_record = _resolve_account(client, account)
            response = client.post(
                "/v1/reconciliations",
                authenticated=True,
                json={
                    "account_id": str(account_record["id"]),
                    "statement_period_start": start_date.isoformat(),
                    "statement_period_end": end_date.isoformat(),
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
        f"Opened paper reconciliation {body['id']} for account "
        f"{account_record.get('code') or account_record['id']} "
        f"({body['statement_period_start']}..{body['statement_period_end']}). "
        f"Closing balance asserted: {body['statement_ending_balance']} "
        f"{body['currency']}."
    )
    typer.echo(
        f"Run `tulip reconcile walk {body['id']}` to step through ledger "
        f"transactions, then `tulip reconcile complete {body['id']}`."
    )


def _read_walk_choice() -> str:
    """Read one line from stdin for the paper-walk wizard.

    Returns the first lowercased char of input. Empty input (just Enter)
    returns ``""`` so the caller can default. EOF returns ``"q"`` so a
    piped script with too few inputs cleanly quits.
    """
    try:
        line = input("[m]atch  [s]kip  [q]uit > ").strip().lower()
    except EOFError:
        return "q"
    return line[:1]


@reconcile_app.command("walk")
def walk_command(
    ctx: typer.Context,
    reconciliation_id: Annotated[
        UUID,
        typer.Argument(help="Reconciliation UUID.", metavar="RECONCILIATION_ID"),
    ],
) -> None:
    """Walk posted ledger txs in the recon's period; tick off matches one at a time.

    Designed for paper-statement reconciliation (#275): the user has a
    physical statement and wants to mark each ledger tx ``[m]atch`` /
    ``[s]kip`` / ``[q]uit`` against it. Match flips a per-tx flag (persisted
    as a ``ReconciliationMatch`` with ``statement_line_id IS NULL``).

    Quit at any time — the envelope keeps all accepted matches; run
    ``tulip reconcile complete`` when ready.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    if as_json:
        raise typer.BadParameter("--json is incompatible with the paper-walk wizard")

    console = Console()
    try:
        with _client(config, as_json=as_json) as client:
            inbox = client.get(
                f"/v1/reconciliations/{reconciliation_id}",
                authenticated=True,
            ).json()
            recon = inbox["reconciliation"]
            if recon.get("source_import_batch_id") is not None:
                typer.echo(
                    "This reconciliation has a source import batch; use "
                    f"`tulip reconcile interactive {reconciliation_id}` instead.",
                    err=True,
                )
                raise typer.Exit(2)
            unmatched_txs = inbox["unmatched_ledger_transactions"]
            if not unmatched_txs:
                typer.echo("No unmatched ledger transactions in the period.")
                typer.echo(
                    f"Run `tulip reconcile complete {reconciliation_id}` "
                    f"to finalise, or post the missing transactions first."
                )
                return
            matched, skipped = _run_paper_walk(
                client,
                console,
                reconciliation_id,
                unmatched_txs,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    typer.echo(f"\nReviewed: {matched} matched, {skipped} skipped.")
    typer.echo(
        f"Run `tulip reconcile complete {reconciliation_id}` when ready "
        "to finalise (closing-balance assertion will be checked)."
    )


def _run_paper_walk(
    client: TulipClient,
    console: Console,
    reconciliation_id: UUID,
    unmatched_txs: list[dict[str, Any]],
) -> tuple[int, int]:
    """Iterate posted ledger txs; return ``(matched, skipped)``."""
    matched = skipped = 0
    total = len(unmatched_txs)
    for idx, tx in enumerate(unmatched_txs, start=1):
        console.print(f"\n[bold][{idx}/{total}][/bold]")
        console.print(
            f"  ledger tx: {str(tx.get('id', ''))[:8]}  {tx.get('date', '?')}  "
            f"{(tx.get('description') or '')[:60]}  status={tx.get('status', '?')}"
        )
        choice = _read_walk_choice()
        if choice == "" or choice == "m":
            client.post(
                f"/v1/reconciliations/{reconciliation_id}/paper-matches",
                authenticated=True,
                json={"ledger_transaction_id": tx["id"]},
            )
            matched += 1
            console.print("  [green]✓ matched[/green]")
        elif choice == "s":
            skipped += 1
            console.print("  skipped")
        elif choice == "q":
            console.print("  quit")
            break
        else:
            console.print(f"  unknown choice {choice!r}; skipping")
            skipped += 1
    return matched, skipped


# ---- interactive (guided wizard) ------------------------------------------


def _read_wizard_choice() -> str:
    """Read one line from stdin and return the first character, lowercased.

    Empty input (just Enter) returns ``""`` so the caller can supply its own
    default. EOF returns ``"q"`` so a piped script with too few inputs cleanly
    quits instead of hanging.
    """
    try:
        line = input("[A]ccept  [R]eject  [S]kip  [Q]uit > ").strip().lower()
    except EOFError:
        return "q"
    return line[:1]


@reconcile_app.command("interactive")
def interactive_command(
    ctx: typer.Context,
    reconciliation_id: Annotated[
        UUID,
        typer.Argument(help="Reconciliation UUID.", metavar="RECONCILIATION_ID"),
    ],
) -> None:
    """Walk through auto-matched proposals one at a time (accept / reject / skip / quit).

    Designed for the migration / monthly-review flow: rather than copying UUIDs
    out of ``reconcile show`` and pasting them into ``match``, this loop renders
    each auto-matched proposal with its statement-line and ledger-transaction
    context side by side and accepts one-keystroke decisions. Manual-only
    matches (no ``matcher_version``) are skipped — they're already user-decided.

    On ``Q``, the reconciliation envelope is left in its current state with all
    accepted matches intact; run ``tulip reconcile complete`` separately when
    you're ready to finalise.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]
    if as_json:
        raise typer.BadParameter("--json is incompatible with the interactive wizard")

    console = Console()
    try:
        with _client(config, as_json=as_json) as client:
            inbox = client.get(
                f"/v1/reconciliations/{reconciliation_id}",
                authenticated=True,
            ).json()
            auto_matches = [m for m in inbox["matches"] if m.get("matcher_version")]
            if not auto_matches:
                if inbox["matches"]:
                    typer.echo("All matches in this reconciliation are manual; nothing to review.")
                else:
                    typer.echo(
                        f"No matches to review yet. Run `tulip reconcile auto-match "
                        f"{reconciliation_id}` first."
                    )
                return

            recon = inbox["reconciliation"]
            lines_by_id, txs_by_id = _build_match_context_lookups(client, recon)
            accepted, rejected, skipped = _run_wizard_loop(
                client,
                console,
                reconciliation_id,
                auto_matches,
                lines_by_id,
                txs_by_id,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    typer.echo(f"\nReviewed: {accepted} accepted, {rejected} rejected, {skipped} skipped.")
    typer.echo(f"Run `tulip reconcile complete {reconciliation_id}` when ready to finalise.")


def _build_match_context_lookups(
    client: TulipClient,
    recon: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Two GETs to populate (line_id → line) and (tx_id → tx) lookups.

    Matched items have been removed from the inbox's ``unmatched_*`` arrays,
    so to render their detail we re-fetch from the source batch and the
    transactions endpoint scoped to the recon's account + period.
    """
    lines_by_id: dict[str, dict[str, Any]] = {}
    batch_id = recon.get("source_import_batch_id")
    if batch_id:
        batch = client.get(f"/v1/imports/{batch_id}", authenticated=True).json()
        lines_by_id = {ln["id"]: ln for ln in batch.get("lines", [])}

    txs = client.get(
        "/v1/transactions",
        authenticated=True,
        params={
            "account_id": recon["account_id"],
            "from": recon["statement_period_start"],
            "to": recon["statement_period_end"],
        },
    ).json()
    txs_by_id = {tx["id"]: tx for tx in txs}
    return lines_by_id, txs_by_id


def _run_wizard_loop(
    client: TulipClient,
    console: Console,
    reconciliation_id: UUID,
    auto_matches: list[dict[str, Any]],
    lines_by_id: dict[str, dict[str, Any]],
    txs_by_id: dict[str, dict[str, Any]],
) -> tuple[int, int, int]:
    """Iterate auto-matches; return ``(accepted, rejected, skipped)``."""
    accepted = rejected = skipped = 0
    total = len(auto_matches)
    for idx, match in enumerate(auto_matches, start=1):
        line = lines_by_id.get(match["statement_line_id"], {})
        tx = txs_by_id.get(match["ledger_transaction_id"], {})
        confidence = str(match.get("confidence") or "?").upper()
        console.print(f"\n[bold][{idx}/{total}] {confidence} confidence[/bold]")
        from tulip_cli._money_format import format_amount

        line_currency = str(line.get("currency", ""))
        line_amount_raw = line.get("amount")
        line_amount = (
            format_amount(line_amount_raw, line_currency) if line_amount_raw is not None else "?"
        )
        console.print(
            f"  statement: {line.get('posted_date', '?')}  "
            f"{(line.get('description') or '')[:50]}  "
            f"{line_amount} {line_currency}"
        )
        console.print(
            f"  ledger tx: {str(tx.get('id', ''))[:8]}  {tx.get('date', '?')}  "
            f"{(tx.get('description') or '')[:50]}  status={tx.get('status', '?')}"
        )

        choice = _read_wizard_choice()
        if choice == "" or choice == "a":
            accepted += 1
            console.print("  [green]✓ accepted[/green]")
        elif choice == "r":
            client.post(
                f"/v1/reconciliations/{reconciliation_id}/matches/{match['id']}/reject",
                authenticated=True,
            )
            rejected += 1
            console.print("  [yellow]✗ rejected[/yellow]")
        elif choice == "s":
            skipped += 1
            console.print("  skipped")
        elif choice == "q":
            console.print("  quit")
            break
        else:
            console.print(f"  unknown choice {choice!r}; skipping")
            skipped += 1
    return accepted, rejected, skipped


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

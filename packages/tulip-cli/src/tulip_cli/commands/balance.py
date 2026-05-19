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
from rich.table import Table

from tulip_cli._account_path import account_path
from tulip_cli._console import make_console
from tulip_cli._tables import add_numeric_column
from tulip_cli.auth.tokens import default_token_store
from tulip_cli.commands.accounts import _resolve_account
from tulip_cli.commands.transactions import _load_accounts_by_id
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


def _render_account_balance(
    body: dict[str, Any],
    accounts_by_id: dict[str, dict[str, Any]],
) -> None:
    """Render a single ``AccountBalanceRead`` body with the account's full path."""
    from tulip_cli._money_format import format_amount

    account_id = body.get("account_id")
    path = account_path(str(account_id), accounts_by_id) if account_id else (body.get("name") or "")
    currency = body.get("currency", "")
    balance = format_amount(body.get("balance"), currency)
    pending_included = bool(body.get("pending_included"))
    label = "balance (incl. pending)" if pending_included else "balance"
    typer.echo(path)
    typer.echo(f"  {label}: {balance} {currency}")
    typer.echo(f"  as of:   {body.get('as_of', '')}")
    if pending_included:
        count = body.get("pending_count", 0)
        plural = "" if count == 1 else "s"
        typer.echo(f"  includes {count} pending transaction{plural}")


def _render_trial_balance(
    body: dict[str, Any],
    accounts_by_id: dict[str, dict[str, Any]],
) -> None:
    """Render a ``TrialBalanceRead`` body as a path-keyed table + totals (#300)."""
    from tulip_cli._money_format import format_amount

    rows = body.get("rows") or []
    if not rows:
        typer.echo(f"No postings on or before {body.get('as_of', 'today')}.")
        return

    pending_included = bool(body.get("pending_included"))
    title = f"Trial balance as of {body.get('as_of', '')}"
    if pending_included:
        count = body.get("pending_count", 0)
        plural = "" if count == 1 else "s"
        title += f"  (incl. {count} pending transaction{plural})"
    balance_header = "balance (incl. pending)" if pending_included else "balance"

    table = Table(title=title, show_header=True)
    table.add_column("account")
    table.add_column("currency")
    add_numeric_column(table, balance_header)
    for r in rows:
        currency = r.get("currency") or ""
        account_id = r.get("account_id")
        label = (
            account_path(str(account_id), accounts_by_id) if account_id else (r.get("name") or "")
        )
        # A row that drew a PENDING posting gets a (P) marker — only
        # meaningful when the caller opted into --pending.
        if pending_included and r.get("has_pending"):
            label = f"{label} (P)"
        table.add_row(
            label,
            currency,
            format_amount(r.get("balance"), currency),
        )
    console = make_console()
    console.print(table)

    totals = body.get("totals_by_currency") or []
    for t in totals:
        currency = t.get("currency", "")
        debits_raw = t.get("debits", "")
        credits_raw = t.get("credits", "")
        debits = format_amount(debits_raw, currency) if debits_raw != "" else ""
        credits = format_amount(credits_raw, currency) if credits_raw != "" else ""
        # Compare the raw (full-precision) values so the equal/unequal marker
        # isn't fooled by quantization (e.g. .005 vs .004 both round to .00).
        marker = "✓" if debits_raw == credits_raw else "⚠"
        console.print(
            f"  {currency}: debits {debits}, credits {credits} {marker}",
        )


def balance(
    ctx: typer.Context,
    account: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Account UUID, code, name, or hierarchical path "
                "(e.g. 'Assets:Current Assets:Checking'). Omit for a "
                "trial-balance summary."
            ),
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
    include_pending: Annotated[
        bool,
        typer.Option(
            "--pending/--no-pending",
            help=(
                "Fold PENDING transactions into the balance — the "
                "'what if all pending is real' view. Default is the "
                "posted-only ledger. When on, the output is clearly "
                "labelled and pending-affected rows carry a (P) marker."
            ),
        ),
    ] = False,
) -> None:
    """Show a single account's balance, or the household trial balance.

    By default only POSTED + RECONCILED transactions count, matching the
    trial-balance convention. ``--pending`` widens the view to include
    PENDING transactions — useful right after an import, before the
    batch has been reviewed. The pending-inclusive output is always
    labelled so it's never mistaken for the posted ledger.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    if as_of is not None:
        try:
            date_type.fromisoformat(as_of)
        except ValueError as exc:
            raise typer.BadParameter("--as-of must be YYYY-MM-DD") from exc

    params: dict[str, str] = {}
    if as_of:
        params["as_of"] = as_of
    if include_pending:
        params["include_pending"] = "true"

    try:
        with _client(config, as_json=as_json) as client:
            if account is None:
                response = client.get(
                    "/v1/reports/trial-balance",
                    authenticated=True,
                    params=params or None,
                )
            else:
                resolved = _resolve_account(client, account)
                response = client.get(
                    f"/v1/accounts/{resolved['id']}/balance",
                    authenticated=True,
                    params=params or None,
                )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    body = response.json()
    # One round-trip to /v1/accounts so path rendering walks the
    # parent chain locally. Failure is non-fatal — ``account_path``
    # falls back to the raw UUID on an empty map (#300).
    try:
        with _client(config, as_json=False) as client:
            accounts_by_id = _load_accounts_by_id(client)
    except CliError:
        accounts_by_id = {}

    if account is None:
        _render_trial_balance(body, accounts_by_id)
    else:
        _render_account_balance(body, accounts_by_id)

"""``tulip add`` (transaction create) and ``tulip transactions`` (read).

Two input modes for creation:

* **Flag mode** (the original P3.4 surface) — repeated
  ``--post account=amount[@CURRENCY]`` flags. Scriptable, unambiguous.
* **Editor mode** (``--edit``, #43) — opens ``$VISUAL`` / ``$EDITOR``
  with a prefilled ledger-subset template. Friendlier for >2 postings
  and for human-driven entry. Reopens on parse / balance / unknown-
  account errors with the message in a banner; saving an empty buffer
  aborts cleanly.

Why ``account=amount`` and not space-separated parts in flag mode:
codes contain colons (e.g. ``assets:checking``), so a colon-delimited
``account:amount`` syntax is ambiguous. ``=`` is unambiguous and we
``rsplit`` on it so codes-with-colons round-trip.

Read commands (``tulip transactions list`` / ``show``) consume
``GET /v1/transactions`` (with the filter query params landed in P3.6)
and ``GET /v1/transactions/{id}``. ``list`` renders a Rich table by
default; ``show`` renders header-plus-postings.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import date as date_type
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from tulip_cli.auth.tokens import default_token_store
from tulip_cli.commands._editor import edit_buffer
from tulip_cli.commands._ledger import LedgerParseError, parse_ledger_text
from tulip_cli.commands.accounts import _resolve_account
from tulip_cli.config import Config
from tulip_cli.errors import CliError
from tulip_cli.http import TulipClient

_CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")


@dataclass(frozen=True, slots=True)
class ParsedPosting:
    """One ``--post`` value, parsed but not yet resolved against the API."""

    account: str  # code or UUID; resolution happens later
    amount: Decimal
    currency: str | None  # None means "inherit from the account"


def parse_posting(value: str) -> ParsedPosting:
    """Parse one ``--post`` value into a :class:`ParsedPosting`.

    Format: ``account=amount[@CURRENCY]``. ``account`` may contain
    colons; we split on the **last** ``=``. ``CURRENCY`` is exactly
    three ASCII letters per ISO 4217.

    Raises :class:`ValueError` on any malformed shape.
    """
    if "=" not in value:
        raise ValueError(f"--post must be 'account=amount[@CURRENCY]', got {value!r}")
    account, _, rhs = value.rpartition("=")
    account = account.strip()
    rhs = rhs.strip()
    if not account:
        raise ValueError(f"--post {value!r}: account is empty")
    if not rhs:
        raise ValueError(f"--post {value!r}: amount is empty")

    currency: str | None = None
    if "@" in rhs:
        amount_str, _, currency = rhs.partition("@")
        amount_str = amount_str.strip()
        currency = currency.strip()
        if not _CURRENCY_RE.fullmatch(currency):
            raise ValueError(
                f"--post {value!r}: currency must be three ASCII letters (got {currency!r})"
            )
        currency = currency.upper()
    else:
        amount_str = rhs

    try:
        amount = Decimal(amount_str)
    except InvalidOperation as exc:
        raise ValueError(
            f"--post {value!r}: amount {amount_str!r} is not a decimal number"
        ) from exc

    return ParsedPosting(account=account, amount=amount, currency=currency)


def _client(config: Config, *, as_json: bool) -> TulipClient:
    return TulipClient(config, token_store=default_token_store(), as_json=as_json)


def _render_transaction(body: dict[str, Any]) -> None:
    typer.echo(f"Created transaction {body.get('id', '')}")
    typer.echo(f"  date:        {body.get('date', '')}")
    typer.echo(f"  description: {body.get('description', '')}")
    typer.echo(f"  status:      {body.get('status', '')}")
    typer.echo("  postings:")
    for p in body.get("postings", []):
        amount = p.get("amount", "")
        currency = p.get("currency", "")
        account = p.get("account_id", "")
        typer.echo(f"    {account}: {amount} {currency}")


def add(
    ctx: typer.Context,
    edit: Annotated[
        bool,
        typer.Option(
            "--edit",
            help=(
                "Open $EDITOR with a ledger-style template instead of taking "
                "--date / --description / --post flags. Reopens on parse or "
                "balance errors; save an empty buffer to abort."
            ),
        ),
    ] = False,
    tx_date: Annotated[
        str | None,
        typer.Option(
            "--date",
            help="Transaction date (YYYY-MM-DD). Required in flag mode.",
        ),
    ] = None,
    description: Annotated[
        str | None,
        typer.Option(
            "--description",
            "-m",
            help="Short human-readable description. Required in flag mode.",
        ),
    ] = None,
    posts: Annotated[
        list[str] | None,
        typer.Option(
            "--post",
            help=(
                "Posting in the form 'account=amount[@CURRENCY]'. "
                "Repeat for each leg of the transaction. Account is a "
                "code (e.g. assets:checking) or a UUID. Currency is "
                "inherited from the account when omitted. Required in "
                "flag mode."
            ),
        ),
    ] = None,
    reference: Annotated[
        str | None,
        typer.Option(
            "--reference",
            help="Optional external reference (check number, statement id, etc.).",
        ),
    ] = None,
) -> None:
    """Create a balanced transaction.

    Default flag mode requires ``--date`` / ``--description`` / repeated
    ``--post``. ``--edit`` opens an editor with a prefilled template.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    if edit:
        _add_via_editor(config=config, as_json=as_json, reference=reference)
        return

    if tx_date is None or description is None or not posts:
        raise typer.BadParameter("flag mode requires --date, --description, and one or more --post")

    try:
        date_type.fromisoformat(tx_date)
    except ValueError as exc:
        raise typer.BadParameter("--date must be YYYY-MM-DD") from exc

    parsed: list[ParsedPosting] = []
    for raw in posts:
        try:
            parsed.append(parse_posting(raw))
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

    try:
        with _client(config, as_json=as_json) as client:
            postings_body = _resolve_postings(client, parsed)
            body = _build_tx_body(tx_date, description, postings_body, reference)
            response = client.post("/v1/transactions", json=body, authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    _render_transaction(response.json())


def _resolve_postings(client: TulipClient, parsed: list[ParsedPosting]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in parsed:
        resolved = _resolve_account(client, p.account)
        currency = p.currency or resolved.get("currency")
        if not isinstance(currency, str):
            raise typer.BadParameter(
                f"posting {p.account}: account has no currency and none specified"
            )
        out.append(
            {
                "account_id": resolved["id"],
                "amount": str(p.amount),
                "currency": currency,
            }
        )
    return out


def _build_tx_body(
    tx_date: str,
    description: str,
    postings: list[dict[str, Any]],
    reference: str | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "date": tx_date,
        "description": description,
        "postings": postings,
    }
    if reference is not None:
        body["reference"] = reference
    return body


_EDITOR_TEMPLATE_HEADER = (
    "# Tulip transaction. Lines starting with # or ; are comments.\n"
    "# Save and quit to post; quit without saving (or save an empty\n"
    "# buffer) to abort. The format is a strict subset of hledger:\n"
    "#\n"
    "#   YYYY-MM-DD <description>\n"
    "#     <account>  <amount> [<currency>]\n"
    "#     <account>  <amount> [<currency>]\n"
    "#\n"
)


def _initial_template() -> str:
    today = date_type.today().isoformat()
    return f"{_EDITOR_TEMPLATE_HEADER}\n{today} \n  \n  \n"


def _add_via_editor(
    *,
    config: Config,
    as_json: bool,
    reference: str | None,
) -> None:
    """Editor loop: edit → parse → resolve → post; on error reopen with banner."""
    buffer = _initial_template()
    while True:
        edited = edit_buffer(buffer)
        # Treat a buffer with no header / no postings as an explicit abort.
        if _looks_empty(edited):
            typer.echo("No transaction posted (empty buffer).")
            return
        try:
            parsed_tx = parse_ledger_text(edited)
        except LedgerParseError as exc:
            buffer = _with_banner(edited, str(exc))
            continue
        try:
            with _client(config, as_json=as_json) as client:
                resolved_postings = _resolve_postings(
                    client,
                    [ParsedPosting(p.account, p.amount, p.currency) for p in parsed_tx.postings],
                )
                body = _build_tx_body(
                    parsed_tx.date.isoformat(),
                    parsed_tx.description,
                    resolved_postings,
                    reference,
                )
                response = client.post("/v1/transactions", json=body, authenticated=True)
        except CliError as err:
            problem_code = str(err.problem.get("code", ""))
            if problem_code in _RECOVERABLE_CODES:
                detail = str(err.problem.get("detail") or err.problem.get("title"))
                buffer = _with_banner(edited, detail)
                continue
            err.render()
            raise typer.Exit(err.exit_code) from None

        if as_json:
            sys.stdout.write(response.text + "\n")
            return
        _render_transaction(response.json())
        return


_RECOVERABLE_CODES = frozenset(
    {
        "transaction.unbalanced",
        "transaction.invalid",
        "account.not_found",
        "account.unknown",
        "account.ambiguous_code",
        "validation.failed",
        "request.body_invalid",
        "period.closed",
    }
)


def _looks_empty(text: str) -> bool:
    """A buffer with only comments / blank lines aborts the edit."""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith(";"):
            continue
        return False
    return True


def _with_banner(prior: str, message: str) -> str:
    """Prepend an error banner to ``prior`` so the next reopen shows the diagnosis."""
    banner_lines = [
        "# ERROR: " + line if line else "#" for line in message.splitlines() or [message]
    ]
    banner = (
        "# ──────────────────────────────────────────────────────────\n"
        + "\n".join(banner_lines)
        + "\n"
        "# Fix the lines below and save again, or save an empty buffer\n"
        "# to abort.\n"
        "# ──────────────────────────────────────────────────────────\n"
    )
    # Strip any prior banner so they don't pile up across iterations.
    body = "\n".join(
        line
        for line in prior.splitlines()
        if not line.startswith("# ERROR:")
        and not line.startswith("# ────")
        and not line.startswith("# Fix the lines below")
    )
    return banner + body


# ---- read commands: tulip transactions list / show -------------------------


transactions_app = typer.Typer(
    name="transactions",
    help="List and inspect existing transactions.",
    no_args_is_help=True,
)


def _validate_iso_date(value: str | None, *, flag: str) -> str | None:
    if value is None:
        return None
    try:
        date_type.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"{flag} must be YYYY-MM-DD") from exc
    return value


_VALID_STATUSES = ("pending", "posted", "reconciled")


def _resolve_account_id_for_filter(client: TulipClient, identifier: str) -> str:
    """Resolve ``--account`` to a UUID string via the shared resolver."""
    resolved = _resolve_account(client, identifier)
    return str(resolved["id"])


def _render_tx_list_table(rows: list[dict[str, Any]]) -> None:
    table = Table(show_header=True, show_lines=False)
    table.add_column("id")
    table.add_column("date")
    table.add_column("description")
    table.add_column("reference")
    table.add_column("status")
    table.add_column("postings")
    for row in rows:
        postings = row.get("postings") or []
        summary_parts = []
        for p in postings:
            amount = p.get("amount", "")
            currency = p.get("currency", "")
            account = p.get("account_id", "")
            short = str(account)[:8] if account else "—"
            summary_parts.append(f"{short} {amount} {currency}")
        summary = "\n".join(summary_parts)
        tx_id = row.get("id") or ""
        table.add_row(
            str(tx_id)[:8] if tx_id else "—",
            str(row.get("date") or ""),
            row.get("description") or "",
            row.get("reference") or "—",
            row.get("status") or "",
            summary,
        )
    Console().print(table)


def _render_tx_detail(tx: dict[str, Any]) -> None:
    typer.echo(f"id:           {tx.get('id', '')}")
    typer.echo(f"date:         {tx.get('date', '')}")
    typer.echo(f"description:  {tx.get('description', '')}")
    typer.echo(f"reference:    {tx.get('reference') or '—'}")
    typer.echo(f"status:       {tx.get('status', '')}")
    typer.echo("postings:")
    table = Table(show_header=True, show_lines=False)
    table.add_column("account_id")
    table.add_column("amount", justify="right")
    table.add_column("currency")
    table.add_column("memo")
    for p in tx.get("postings") or []:
        table.add_row(
            str(p.get("account_id", "")),
            str(p.get("amount", "")),
            str(p.get("currency", "")),
            str(p.get("memo") or ""),
        )
    Console().print(table)


@transactions_app.command("list")
def list_transactions(
    ctx: typer.Context,
    account: Annotated[
        str | None,
        typer.Option(
            "--account",
            help=(
                "Filter to transactions touching this account (code or UUID). "
                "Resolved via the same UUID-or-code lookup as `accounts show`."
            ),
        ),
    ] = None,
    from_date: Annotated[
        str | None,
        typer.Option(
            "--from",
            help="Inclusive lower bound on transaction date (YYYY-MM-DD).",
        ),
    ] = None,
    to_date: Annotated[
        str | None,
        typer.Option(
            "--to",
            help="Inclusive upper bound on transaction date (YYYY-MM-DD).",
        ),
    ] = None,
    status_: Annotated[
        str | None,
        typer.Option(
            "--status",
            help="One of: pending, posted, reconciled.",
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            help="Cap on rows returned (1-1000). Omit for no limit.",
            min=1,
            max=1000,
        ),
    ] = None,
) -> None:
    """List transactions, newest first. All filters are optional and AND together."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    _validate_iso_date(from_date, flag="--from")
    _validate_iso_date(to_date, flag="--to")
    if status_ is not None and status_ not in _VALID_STATUSES:
        raise typer.BadParameter(
            f"--status must be one of {', '.join(_VALID_STATUSES)} (got {status_!r})"
        )

    params: dict[str, str] = {}
    try:
        with _client(config, as_json=as_json) as client:
            if account is not None:
                params["account_id"] = _resolve_account_id_for_filter(client, account)
            if from_date is not None:
                params["from"] = from_date
            if to_date is not None:
                params["to"] = to_date
            if status_ is not None:
                params["status"] = status_
            if limit is not None:
                params["limit"] = str(limit)
            response = client.get("/v1/transactions", authenticated=True, params=params)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return

    rows = response.json()
    if not rows:
        typer.echo("No transactions match.")
        return
    _render_tx_list_table(rows)


@transactions_app.command("show")
def show_transaction(
    ctx: typer.Context,
    tx_id: Annotated[
        str,
        typer.Argument(
            help="Transaction UUID.",
            metavar="TXID",
        ),
    ],
) -> None:
    """Show one transaction (header + postings) by UUID."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    try:
        UUID(tx_id)
    except ValueError as exc:
        raise typer.BadParameter("TXID must be a UUID") from exc

    try:
        with _client(config, as_json=as_json) as client:
            response = client.get(f"/v1/transactions/{tx_id}", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    _render_tx_detail(response.json())


@transactions_app.command("void")
def void_transaction(
    ctx: typer.Context,
    tx_id: Annotated[
        str,
        typer.Argument(help="Transaction UUID to void.", metavar="TXID"),
    ],
    reason: Annotated[
        str,
        typer.Option(
            "--reason",
            "-r",
            help="Reason for the void; surfaced in the reversal's description.",
        ),
    ],
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip confirmation prompt.",
        ),
    ] = False,
    reversal_date: Annotated[
        str | None,
        typer.Option(
            "--date",
            help=(
                "Reversal date (YYYY-MM-DD). Defaults to today. The reversal "
                "date is checked against open periods, not the source's date."
            ),
        ),
    ] = None,
) -> None:
    """Void a POSTED transaction by posting a sign-flipped sibling reversal."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    try:
        UUID(tx_id)
    except ValueError as exc:
        raise typer.BadParameter("TXID must be a UUID") from exc

    if reversal_date is not None:
        _validate_iso_date(reversal_date, flag="--date")

    if not yes:
        confirmed = typer.confirm(
            f"Void transaction {tx_id}? This posts a reversal sibling.",
            default=False,
        )
        if not confirmed:
            typer.echo("Aborted; no changes made.")
            return

    body: dict[str, Any] = {"reason": reason}
    if reversal_date is not None:
        body["reversal_date"] = reversal_date

    try:
        with _client(config, as_json=as_json) as client:
            response = client.post(
                f"/v1/transactions/{tx_id}/void",
                json=body,
                authenticated=True,
            )
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    out = response.json()
    typer.echo(f"Voided {out['source_id']}; reversal posted as {out['reversal_id']}.")
    if out.get("paired_shadow_tx_id_voided"):
        typer.echo(f"  Paired shadow transaction {out['paired_shadow_tx_id_voided']} auto-voided.")


@transactions_app.command("delete")
def delete_transaction(
    ctx: typer.Context,
    tx_id: Annotated[
        str,
        typer.Argument(help="Transaction UUID to delete.", metavar="TXID"),
    ],
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip confirmation prompt.",
        ),
    ] = False,
) -> None:
    """Hard-delete a PENDING transaction. Use ``void`` for POSTED transactions."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    try:
        UUID(tx_id)
    except ValueError as exc:
        raise typer.BadParameter("TXID must be a UUID") from exc

    if not yes:
        confirmed = typer.confirm(
            f"Hard-delete transaction {tx_id}? Only PENDING transactions can be deleted.",
            default=False,
        )
        if not confirmed:
            typer.echo("Aborted; no changes made.")
            return

    try:
        with _client(config, as_json=as_json) as client:
            client.delete(f"/v1/transactions/{tx_id}", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write('{"deleted": "' + tx_id + '"}\n')
        return
    typer.echo(f"Deleted transaction {tx_id}.")


@transactions_app.command("edit")
def edit_transaction(
    ctx: typer.Context,
    tx_id: Annotated[
        str,
        typer.Argument(help="Transaction UUID to edit.", metavar="TXID"),
    ],
) -> None:
    """Edit a PENDING transaction in ``$EDITOR`` (hledger format).

    POSTED / RECONCILED transactions cannot be edited; use ``void``
    + create a corrected entry instead.
    """
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    try:
        UUID(tx_id)
    except ValueError as exc:
        raise typer.BadParameter("TXID must be a UUID") from exc

    # Pre-flight: load the existing transaction so we can render it into the
    # editor buffer and reject early when it's not PENDING.
    try:
        with _client(config, as_json=as_json) as client:
            current = client.get(f"/v1/transactions/{tx_id}", authenticated=True).json()
            if current.get("status") != "pending":
                from tulip_cli.errors import CliError as _CliError

                raise _CliError(
                    problem={
                        "code": "transaction.not_editable",
                        "title": "Transaction is not editable",
                        "status": 409,
                        "detail": (
                            "Only PENDING transactions can be edited. Use "
                            "`tulip transactions void` for posted transactions."
                        ),
                    },
                    as_json=as_json,
                )

            postings = client.get("/v1/accounts", authenticated=True).json()
            accounts_by_id = {a["id"]: a for a in postings}

            buffer = _render_tx_for_edit(current, accounts_by_id)
            while True:
                edited = edit_buffer(buffer)
                if _looks_empty(edited):
                    typer.echo("No changes saved (empty buffer).")
                    return
                try:
                    parsed = parse_ledger_text(edited)
                except LedgerParseError as exc:
                    buffer = _with_banner(edited, str(exc))
                    continue
                try:
                    resolved = _resolve_postings(
                        client,
                        [ParsedPosting(p.account, p.amount, p.currency) for p in parsed.postings],
                    )
                    body = {
                        "date": parsed.date.isoformat(),
                        "description": parsed.description,
                        "postings": resolved,
                    }
                    response = client.patch(
                        f"/v1/transactions/{tx_id}",
                        json=body,
                        authenticated=True,
                    )
                except CliError as err:
                    code = str(err.problem.get("code", ""))
                    if code in _RECOVERABLE_CODES:
                        detail = str(err.problem.get("detail") or err.problem.get("title"))
                        buffer = _with_banner(edited, detail)
                        continue
                    raise

                if as_json:
                    sys.stdout.write(response.text + "\n")
                    return
                _render_transaction(response.json())
                return
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None


def _render_tx_for_edit(tx: dict[str, Any], accounts_by_id: dict[str, dict[str, Any]]) -> str:
    """Render an existing transaction back into the hledger-subset format.

    Uses account ``code`` when available; falls back to UUID. Inverse of
    :func:`tulip_cli.commands._ledger.parse_ledger_text`.
    """
    lines: list[str] = [_EDITOR_TEMPLATE_HEADER, ""]
    lines.append(f"{tx.get('date', '')} {tx.get('description', '')}")
    for p in tx.get("postings", []):
        account_ref = accounts_by_id.get(p.get("account_id", ""), {}).get("code") or p.get(
            "account_id", ""
        )
        amount = p.get("amount", "")
        currency = p.get("currency", "")
        lines.append(f"  {account_ref}  {amount} {currency}")
    return "\n".join(lines) + "\n"

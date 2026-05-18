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
from rich.table import Table

from tulip_cli._console import make_console
from tulip_cli._picker import is_interactive, pick
from tulip_cli._preferences import (
    get_reconciled_edit_confirm,
    set_reconciled_edit_confirm,
)
from tulip_cli._tables import add_numeric_column
from tulip_cli.auth.tokens import default_token_store
from tulip_cli.commands._edit_decision import EditAction, decide_edit_action
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
    from tulip_cli._money_format import format_amount

    typer.echo(f"Created transaction {body.get('id', '')}")
    typer.echo(f"  date:        {body.get('date', '')}")
    typer.echo(f"  description: {body.get('description', '')}")
    typer.echo(f"  status:      {body.get('status', '')}")
    typer.echo("  postings:")
    for p in body.get("postings", []):
        currency = p.get("currency", "")
        amount = format_amount(p.get("amount"), currency)
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
    tags: Annotated[
        list[str] | None,
        typer.Option(
            "--tag",
            help=(
                "Free-form label to attach to this transaction (#39). "
                "Repeat the flag for multiple tags. Tags are case-"
                "insensitive and deduplicated server-side."
            ),
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
            if tags:
                body["tags"] = list(tags)
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


_HEX_PREFIX_CHARS = frozenset("0123456789abcdefABCDEF-")


def _resolve_tx_id(client: TulipClient, identifier: str, *, as_json: bool) -> UUID:
    """Resolve a TXID argument to a full UUID.

    Fast path: a valid UUID string is returned unchanged with no API
    call. Otherwise the identifier is treated as a hex prefix and
    looked up via ``GET /v1/transactions?id_prefix=…``. Zero matches
    raises ``transaction.not_found``; multiple matches raises
    ``transaction.ambiguous_id_prefix`` with a sample so the user
    can lengthen the prefix.
    """
    try:
        return UUID(identifier)
    except ValueError:
        pass
    if not identifier or not all(c in _HEX_PREFIX_CHARS for c in identifier):
        raise typer.BadParameter("TXID must be a UUID or hex prefix (0-9, a-f, -)")
    response = client.get(
        "/v1/transactions",
        authenticated=True,
        params={"id_prefix": identifier},
    )
    rows = response.json()
    if len(rows) == 0:
        raise CliError(
            problem={
                "type": "/.well-known/errors/transaction.not_found",
                "title": "No transaction matches that id prefix",
                "status": 404,
                "detail": f"No transaction's id begins with {identifier!r}.",
                "code": "transaction.not_found",
            },
            as_json=as_json,
        )
    if len(rows) > 1:
        sample = ", ".join(str(r["id"])[:12] for r in rows[:5])
        raise CliError(
            problem={
                "type": "/.well-known/errors/transaction.ambiguous_id_prefix",
                "title": "Ambiguous transaction id prefix",
                "status": 400,
                "detail": (
                    f"Prefix {identifier!r} matched {len(rows)} transactions "
                    f"(e.g. {sample}). Use more characters."
                ),
                "code": "transaction.ambiguous_id_prefix",
            },
            as_json=as_json,
        )
    return UUID(str(rows[0]["id"]))


def _format_account_label(
    accounts_by_id: dict[str, dict[str, Any]],
    account_id: str,
) -> str:
    """Render a human-readable label for a posting's ``account_id`` (#214).

    Preferred form is ``<code>:<name>`` when both are set
    (e.g. ``5100:Groceries`` or ``expenses:rent:Rent``). When the account
    has no code we fall back to ``<name>``. An ``account_id`` that isn't
    in ``accounts_by_id`` (an orphaned posting — shouldn't happen, but
    the issue calls it out as a graceful-degrade requirement) renders
    as the raw UUID string so the row is still printable.
    """
    account = accounts_by_id.get(account_id)
    if account is None:
        return account_id
    code = account.get("code")
    name = account.get("name")
    if code and name:
        return f"{code}:{name}"
    if name:
        return str(name)
    return account_id


def _load_accounts_by_id(client: TulipClient) -> dict[str, dict[str, Any]]:
    """Fetch ``/v1/accounts`` once and key it by ``id`` for label resolution.

    The accounts list per household is small (dozens, not thousands), so
    one round-trip per command beats N+1 ``/v1/accounts/{id}`` lookups
    while rendering a multi-row table. Failures are non-fatal: an empty
    map causes :func:`_format_account_label` to fall through to the raw
    UUID, which preserves today's behaviour rather than aborting the
    render.
    """
    try:
        response = client.get("/v1/accounts", authenticated=True)
    except CliError:
        return {}
    rows = response.json()
    return {str(a["id"]): a for a in rows if "id" in a}


def _render_tx_list_table(
    rows: list[dict[str, Any]],
    accounts_by_id: dict[str, dict[str, Any]],
) -> None:
    from tulip_cli._money_format import format_amount

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
            currency = p.get("currency", "")
            amount = format_amount(p.get("amount"), currency)
            label = _format_account_label(accounts_by_id, str(p.get("account_id", "")))
            summary_parts.append(f"{label} {amount} {currency}")
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
    make_console().print(table)


def _render_tx_detail(
    tx: dict[str, Any],
    accounts_by_id: dict[str, dict[str, Any]],
) -> None:
    from tulip_cli._money_format import format_amount

    typer.echo(f"id:           {tx.get('id', '')}")
    typer.echo(f"date:         {tx.get('date', '')}")
    typer.echo(f"description:  {tx.get('description', '')}")
    typer.echo(f"reference:    {tx.get('reference') or '—'}")
    typer.echo(f"status:       {tx.get('status', '')}")
    notes = tx.get("notes")
    if notes:
        # Indent multi-line notes so the "Notes:" header is unambiguous.
        first, *rest = str(notes).splitlines() or [""]
        typer.echo(f"Notes:        {first}")
        for line in rest:
            typer.echo(f"              {line}")
    typer.echo("postings:")
    table = Table(show_header=True, show_lines=False)
    table.add_column("account")
    add_numeric_column(table, "amount")
    table.add_column("currency")
    table.add_column("memo")
    for p in tx.get("postings") or []:
        currency = str(p.get("currency", ""))
        table.add_row(
            _format_account_label(accounts_by_id, str(p.get("account_id", ""))),
            format_amount(p.get("amount"), currency),
            currency,
            str(p.get("memo") or ""),
        )
    make_console().print(table)


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
    tag: Annotated[
        str | None,
        typer.Option(
            "--tag",
            help=(
                "Filter to transactions carrying this label (#39 v1). "
                "Case-insensitive. Single-tag filter in v1."
            ),
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
            if tag is not None:
                params["tag"] = tag
            if limit is not None:
                params["limit"] = str(limit)
            response = client.get("/v1/transactions", authenticated=True, params=params)
            # Resolve account UUIDs → human labels once per render (#214).
            # Loaded inside the `_client` context so it shares the HTTP
            # client and token handling; only fetched when we actually
            # need to render a table.
            if as_json:
                accounts_by_id: dict[str, dict[str, Any]] = {}
            else:
                accounts_by_id = _load_accounts_by_id(client)
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
    _render_tx_list_table(rows, accounts_by_id)


def _format_tx_picker_label(item: dict[str, Any]) -> str:
    """One-line label for a transaction row in the picker."""
    tx_id = str(item.get("id") or "")
    date = str(item.get("date") or "")
    desc = str(item.get("description") or "")
    if len(desc) > 48:
        desc = desc[:45] + "..."
    status = str(item.get("status") or "")
    return f"{tx_id[:8] if tx_id else '—'}  {date}  {status:<10}  {desc}"


def _pick_tx_id(config: Config, *, as_json: bool) -> str | None:
    """Fetch recent transactions and prompt the user to pick one.

    Returns the picked UUID string, or ``None`` when suppressed
    (``--json`` / non-TTY) or the user cancels.
    """
    if as_json or not is_interactive():
        typer.echo(
            "Missing argument TXID. Run `tulip transactions list` to find "
            "a transaction, then re-run with its id or prefix.",
            err=True,
        )
        return None
    try:
        with _client(config, as_json=as_json) as client:
            # 20 is the picker cap; ask the API for exactly that.
            response = client.get(
                "/v1/transactions",
                authenticated=True,
                params={"limit": "20"},
            )
    except CliError as err:
        err.render()
        return None
    rows = response.json()
    return pick(
        rows,
        label=_format_tx_picker_label,
        title="Pick a recent transaction:",
        empty_message=(
            "No transactions yet. Use `tulip add` to create one or "
            "`tulip imports apply` to land an imported batch."
        ),
        overflow_hint=(
            "  …showing 20 most recent; narrow with "
            "`tulip transactions list --account <id> --from <date>`."
        ),
    )


@transactions_app.command("show")
def show_transaction(
    ctx: typer.Context,
    tx_id: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Transaction UUID or unambiguous hex prefix. Omit to pick "
                "interactively from recent transactions (TTY only — scripts "
                "still get the usage error)."
            ),
            metavar="TXID",
        ),
    ] = None,
) -> None:
    """Show one transaction (header + postings) by UUID or prefix."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    if tx_id is None:
        tx_id = _pick_tx_id(config, as_json=as_json)
        if tx_id is None:
            raise typer.Exit(2)

    try:
        with _client(config, as_json=as_json) as client:
            resolved = _resolve_tx_id(client, tx_id, as_json=as_json)
            response = client.get(f"/v1/transactions/{resolved}", authenticated=True)
            # Resolve account UUIDs → human labels (#214). ``--json`` mode
            # skips the extra round-trip since it just re-emits the API
            # body verbatim.
            if as_json:
                accounts_by_id: dict[str, dict[str, Any]] = {}
            else:
                accounts_by_id = _load_accounts_by_id(client)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    _render_tx_detail(response.json(), accounts_by_id)


@transactions_app.command("void")
def void_transaction(
    ctx: typer.Context,
    tx_id: Annotated[
        str,
        typer.Argument(
            help="Transaction UUID or unambiguous hex prefix to void.",
            metavar="TXID",
        ),
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

    if reversal_date is not None:
        _validate_iso_date(reversal_date, flag="--date")

    body: dict[str, Any] = {"reason": reason}
    if reversal_date is not None:
        body["reversal_date"] = reversal_date

    try:
        with _client(config, as_json=as_json) as client:
            resolved = _resolve_tx_id(client, tx_id, as_json=as_json)
            if not yes:
                confirmed = typer.confirm(
                    f"Void transaction {resolved}? This posts a reversal sibling.",
                    default=False,
                )
                if not confirmed:
                    typer.echo("Aborted; no changes made.")
                    return
            response = client.post(
                f"/v1/transactions/{resolved}/void",
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
        typer.Argument(
            help="Transaction UUID or unambiguous hex prefix to delete.",
            metavar="TXID",
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
) -> None:
    """Hard-delete a PENDING transaction. Use ``void`` for POSTED transactions."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    try:
        with _client(config, as_json=as_json) as client:
            resolved = _resolve_tx_id(client, tx_id, as_json=as_json)
            if not yes:
                confirmed = typer.confirm(
                    f"Hard-delete transaction {resolved}? "
                    "Only PENDING transactions can be deleted.",
                    default=False,
                )
                if not confirmed:
                    typer.echo("Aborted; no changes made.")
                    return
            client.delete(f"/v1/transactions/{resolved}", authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write('{"deleted": "' + str(resolved) + '"}\n')
        return
    typer.echo(f"Deleted transaction {resolved}.")


# Module-level so the "[S] yes, don't ask again this session" answer
# from the RECONCILED-edit prompt persists across multiple ``tulip
# transactions edit`` calls within the same Typer invocation chain
# (issue #209b). Reset to False on each fresh ``tulip`` process — a
# new shell invocation gets a clean slate.
_SESSION_RECONCILED_EDIT_CONFIRMED: bool = False


def _reset_session_confirmation_for_tests() -> None:
    """Test seam: reset the in-process RECONCILED-confirmation flag."""
    global _SESSION_RECONCILED_EDIT_CONFIRMED
    _SESSION_RECONCILED_EDIT_CONFIRMED = False


def _reconciled_edit_required_confirmation_problem() -> dict[str, Any]:
    return {
        "type": "/.well-known/errors/transaction.reconciled_edit_requires_confirmation",
        "title": "Editing a reconciled transaction requires explicit confirmation",
        "status": 400,
        "detail": (
            "This transaction is RECONCILED; editing breaks the "
            "reconciliation linkage. In machine-readable mode "
            "(--json) you must explicitly opt in by re-running with "
            "--force-reconciled-edit. To opt in once at an "
            "interactive TTY (and persist for future sessions), run "
            "`tulip transactions edit TXID` without --json and answer "
            "[A]lways on the prompt."
        ),
        "instance": "",
        "code": "transaction.reconciled_edit_requires_confirmation",
    }


def _prompt_reconciled_edit(reconciliation_hint: str) -> str:
    """Render the Y/N/S/A prompt; return one of ``yes``, ``no``, ``session``, ``always``.

    The ``reconciliation_hint`` is a human-readable string telling the
    user *which* reconciliation the linkage will break (e.g., the
    statement's period). Today it's the transaction's id-prefix; a
    later slice could surface the reconciliation envelope id directly
    when the API exposes it on TransactionRead.
    """
    typer.echo(
        f"This transaction is RECONCILED ({reconciliation_hint}). Editing will "
        "break the reconciliation linkage; the statement line will return to "
        "the unmatched pool.",
        err=True,
    )
    typer.echo(
        "  [Y]es        proceed this time",
        err=True,
    )
    typer.echo(
        "  [N]o         cancel the edit (default)",
        err=True,
    )
    typer.echo(
        "  [S]          proceed and don't ask again this session",
        err=True,
    )
    typer.echo(
        "  [A]lways     proceed and don't ask again ever (persisted)",
        err=True,
    )
    try:
        raw = typer.prompt("Edit anyway?", default="n", show_default=True)
    except (typer.Abort, EOFError):
        return "no"
    answer = raw.strip().lower()
    if answer in ("y", "yes"):
        return "yes"
    if answer in ("s",):
        return "session"
    if answer in ("a", "always"):
        return "always"
    return "no"


@transactions_app.command("edit")
def edit_transaction(
    ctx: typer.Context,
    tx_id: Annotated[
        str,
        typer.Argument(
            help="Transaction UUID or unambiguous hex prefix to edit.",
            metavar="TXID",
        ),
    ],
    force_reconciled_edit: Annotated[
        bool,
        typer.Option(
            "--force-reconciled-edit",
            help=(
                "Skip the interactive 'this transaction is RECONCILED' "
                "prompt and proceed with the void+recreate immediately. "
                "Required in --json mode to edit a RECONCILED transaction; "
                "optional at a TTY. Has no effect on PENDING / POSTED "
                "transactions."
            ),
        ),
    ] = False,
) -> None:
    """Edit a transaction in ``$EDITOR`` (hledger format).

    Behaviour depends on the transaction's current status:

    * **PENDING** — in-place PATCH, same as before.
    * **POSTED** — transparent void + recreate via
      ``POST /v1/transactions/{id}/replace`` (#209a). No prompt; the
      reversal carries an "Edited via ``tulip transactions edit``"
      reason for the audit trail.
    * **RECONCILED** — same void + recreate, but at an interactive TTY
      we first warn that the reconciliation linkage will break and
      prompt ``[Y]es`` / ``[N]o`` (default) / ``[S]`` (don't ask again
      this session) / ``[A]lways`` (persisted to
      ``~/.config/tulip/preferences.json``). In ``--json`` mode without
      ``--force-reconciled-edit``, the command rejects with the
      ``transaction.reconciled_edit_requires_confirmation`` problem
      detail rather than blocking on a prompt.
    """
    global _SESSION_RECONCILED_EDIT_CONFIRMED

    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

    try:
        with _client(config, as_json=as_json) as client:
            resolved = _resolve_tx_id(client, tx_id, as_json=as_json)
            current = client.get(f"/v1/transactions/{resolved}", authenticated=True).json()
            status = str(current.get("status", "")).lower()
            if status not in ("pending", "posted", "reconciled"):
                # E.g. a voided transaction. The /replace endpoint
                # would reject anyway with transaction.already_voided;
                # surface the same clear error up front so we don't
                # waste an editor cycle.
                from tulip_cli.errors import CliError as _CliError

                raise _CliError(
                    problem={
                        "type": "/.well-known/errors/transaction.not_editable",
                        "title": "Transaction is not editable",
                        "status": 409,
                        "detail": (
                            f"Cannot edit a transaction with status {status!r}. "
                            "Only PENDING / POSTED / RECONCILED transactions "
                            "are editable."
                        ),
                        "instance": "",
                        "code": "transaction.not_editable",
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
                stripped, notes_value = _extract_notes_block(edited)
                try:
                    parsed = parse_ledger_text(stripped)
                except LedgerParseError as exc:
                    buffer = _with_banner(edited, str(exc))
                    continue

                action = decide_edit_action(
                    status=status,
                    json_mode=as_json,
                    force=force_reconciled_edit,
                    session_confirmed=_SESSION_RECONCILED_EDIT_CONFIRMED,
                    persisted_pref=get_reconciled_edit_confirm(),
                )

                if action is EditAction.REJECT_JSON_MODE:
                    from tulip_cli.errors import CliError as _CliError

                    raise _CliError(
                        problem=_reconciled_edit_required_confirmation_problem(),
                        as_json=as_json,
                    )

                if action is EditAction.PROMPT_REQUIRED:
                    answer = _prompt_reconciled_edit(reconciliation_hint=str(resolved)[:8])
                    if answer == "no":
                        typer.echo("Cancelled; transaction unchanged.")
                        return
                    if answer == "session":
                        _SESSION_RECONCILED_EDIT_CONFIRMED = True
                    elif answer == "always":
                        set_reconciled_edit_confirm("never_ask")
                    action = EditAction.REPLACE_AFTER_PROMPT

                try:
                    resolved_postings = _resolve_postings(
                        client,
                        [ParsedPosting(p.account, p.amount, p.currency) for p in parsed.postings],
                    )
                    body: dict[str, Any] = {
                        "date": parsed.date.isoformat(),
                        "description": parsed.description,
                        "postings": resolved_postings,
                    }
                    if action is EditAction.PATCH:
                        if not isinstance(notes_value, _UNSET_TYPE):
                            body["notes"] = notes_value
                        response = client.patch(
                            f"/v1/transactions/{resolved}",
                            json=body,
                            authenticated=True,
                        )
                    else:  # REPLACE_SILENT or REPLACE_AFTER_PROMPT
                        body["reason"] = "Edited via `tulip transactions edit`"
                        if not isinstance(notes_value, _UNSET_TYPE) and notes_value is not None:
                            # /replace creates a new tx; only thread notes
                            # through when the user explicitly set them.
                            # An explicit clear (None) is meaningless for
                            # a fresh tx and is dropped.
                            body["notes"] = notes_value
                        response = client.post(
                            f"/v1/transactions/{resolved}/replace",
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
                if action is EditAction.PATCH:
                    _render_transaction(response.json())
                else:
                    body_out = response.json()
                    typer.echo(
                        f"Replaced transaction {body_out['source_id']} → "
                        f"{body_out['replacement_id']} "
                        f"(reversal {body_out['reversal_id']})."
                    )
                return
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None


def _account_display_for_edit(account_id: str, accounts_by_id: dict[str, dict[str, Any]]) -> str:
    """Pick the most-readable label for an account in the editor buffer (#304).

    Fallback order:

    1. ``code`` — already-namespaced and parser-safe (matches the user's
       chart-of-accounts shorthand).
    2. ``name`` — works for the common case of code-less accounts created
       by an importer (e.g., "Checking"). Names with spaces survive the
       round-trip because the parser accepts the hledger two-space
       account/amount separator.
    3. ``account_id`` — last-resort, for orphaned references where the
       account row didn't come back in the lookup. The user will see a
       UUID and can fix or delete the row by hand.
    """
    account = accounts_by_id.get(account_id, {})
    code = account.get("code")
    if isinstance(code, str) and code:
        return code
    name = account.get("name")
    if isinstance(name, str) and name:
        return name
    return account_id


def _render_tx_for_edit(tx: dict[str, Any], accounts_by_id: dict[str, dict[str, Any]]) -> str:
    """Render an existing transaction back into the hledger-subset format.

    Inverse of :func:`tulip_cli.commands._ledger.parse_ledger_text`.
    Account labels use the fallback chain in
    :func:`_account_display_for_edit` so code-less accounts render as
    their human-readable name rather than a bare UUID (#304). The
    account/amount separator is two spaces — the hledger convention the
    parser also accepts so names containing single spaces round-trip.
    Notes (if any) are rendered as a bracketed comment block at the
    bottom of the buffer; see :func:`_extract_notes_block`.
    """
    lines: list[str] = [_EDITOR_TEMPLATE_HEADER, ""]
    lines.append(f"{tx.get('date', '')} {tx.get('description', '')}")
    for p in tx.get("postings", []):
        account_ref = _account_display_for_edit(str(p.get("account_id", "")), accounts_by_id)
        amount = p.get("amount", "")
        currency = p.get("currency", "")
        lines.append(f"  {account_ref}  {amount} {currency}")
    notes = tx.get("notes")
    lines.append("")
    lines.extend(_render_notes_block(notes if isinstance(notes, str) else None))
    return "\n".join(lines) + "\n"


# Markers that bracket the multi-line notes block in the editor buffer.
# Anything BETWEEN these two lines is extracted as notes plaintext (each
# content line is expected to be prefixed by ``# `` so the ledger parser
# treats the section as a no-op block of comments).
_NOTES_BLOCK_START = "# ─── notes (delete the lines below to clear, edit to change) ───"
_NOTES_BLOCK_END = "# ─── end notes ───"
_NOTES_PREFIX = "# "


def _render_notes_block(notes: str | None) -> list[str]:
    """Render the notes block lines (markers + comment-prefixed content)."""
    block: list[str] = [_NOTES_BLOCK_START]
    if notes:
        for line in notes.splitlines() or [notes]:
            block.append(f"{_NOTES_PREFIX}{line}")
    block.append(_NOTES_BLOCK_END)
    return block


def _extract_notes_block(text: str) -> tuple[str, str | None | _UNSET_TYPE]:
    """Strip the notes block from ``text`` and return ``(stripped, notes)``.

    Returns:
        stripped: ``text`` with the notes block (markers + content) removed.
            Safe to feed to :func:`parse_ledger_text`.
        notes: the extracted plaintext, ``None`` if the block is empty
            (meaning "clear"), or ``_UNSET`` if no block was present in
            the buffer (meaning "leave column alone"). The ``_UNSET``
            sentinel is local — the caller maps it to "omit the ``notes``
            field from the PATCH body".

    """
    lines = text.splitlines()
    try:
        start = lines.index(_NOTES_BLOCK_START)
    except ValueError:
        return text, _UNSET
    try:
        end_offset = lines[start + 1 :].index(_NOTES_BLOCK_END)
    except ValueError:
        # Opener without closer — treat the rest of the buffer as notes
        # content, but only the comment-prefixed lines.
        content_lines = lines[start + 1 :]
        end = len(lines)
    else:
        content_lines = lines[start + 1 : start + 1 + end_offset]
        end = start + 1 + end_offset + 1  # include the end marker

    note_text_lines: list[str] = []
    for raw in content_lines:
        if raw.startswith(_NOTES_PREFIX):
            note_text_lines.append(raw[len(_NOTES_PREFIX) :])
        elif raw.strip() == "#":
            # Bare ``#`` represents an empty line inside notes.
            note_text_lines.append("")
        # Non-comment lines inside the block are ignored — keeps the
        # parser focused on lines the user explicitly marked as notes.
    notes_value: str | None
    if note_text_lines:
        notes_value = "\n".join(note_text_lines).strip("\n")
        if not notes_value:
            notes_value = None
    else:
        notes_value = None

    stripped_lines = lines[:start] + lines[end:]
    return "\n".join(stripped_lines), notes_value


class _UNSET_TYPE:
    """Sentinel for "notes block absent from the buffer entirely"."""


_UNSET: _UNSET_TYPE = _UNSET_TYPE()

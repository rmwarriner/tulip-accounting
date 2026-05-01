"""``tulip add`` — create a balanced transaction.

Two input modes:

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
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import date as date_type
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

import typer

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

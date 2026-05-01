"""``tulip add`` — create a balanced transaction.

Postings are passed as repeated ``--post account=amount[@CURRENCY]`` flags.
The account portion is either an ``Account.code`` or a UUID; we resolve
it via the same helper ``tulip accounts show`` uses (``_resolve_account``).
``CURRENCY`` is optional — when omitted, the resolved account's primary
currency is used.

Why ``account=amount`` and not space-separated parts: codes contain
colons (e.g. ``assets:checking``), so a colon-delimited ``account:amount``
syntax is ambiguous. ``=`` is unambiguous and we ``rsplit`` on it so
codes-with-colons round-trip. Editor-driven input (the ``$EDITOR``
template alternative) is a future slice if it's wanted; the issue
recommends shipping the flag form first.
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
    tx_date: Annotated[
        str,
        typer.Option(
            "--date",
            help="Transaction date (YYYY-MM-DD).",
        ),
    ],
    description: Annotated[
        str,
        typer.Option(
            "--description",
            "-m",
            help="Short human-readable description.",
        ),
    ],
    posts: Annotated[
        list[str],
        typer.Option(
            "--post",
            help=(
                "Posting in the form 'account=amount[@CURRENCY]'. "
                "Repeat for each leg of the transaction. Account is a "
                "code (e.g. assets:checking) or a UUID. Currency is "
                "inherited from the account when omitted."
            ),
        ),
    ],
    reference: Annotated[
        str | None,
        typer.Option(
            "--reference",
            help="Optional external reference (check number, statement id, etc.).",
        ),
    ] = None,
) -> None:
    """Create a balanced transaction from one or more ``--post`` flags."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["json"]

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
            postings_body: list[dict[str, Any]] = []
            for p in parsed:
                resolved = _resolve_account(client, p.account)
                currency = p.currency or resolved.get("currency")
                if not isinstance(currency, str):
                    raise typer.BadParameter(
                        f"posting {p.account}: account has no currency and none specified"
                    )
                postings_body.append(
                    {
                        "account_id": resolved["id"],
                        "amount": str(p.amount),
                        "currency": currency,
                    }
                )

            body: dict[str, Any] = {
                "date": tx_date,
                "description": description,
                "postings": postings_body,
            }
            if reference is not None:
                body["reference"] = reference
            response = client.post("/v1/transactions", json=body, authenticated=True)
    except CliError as err:
        err.render()
        raise typer.Exit(err.exit_code) from None

    if as_json:
        sys.stdout.write(response.text + "\n")
        return
    _render_transaction(response.json())

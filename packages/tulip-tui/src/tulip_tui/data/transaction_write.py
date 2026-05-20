"""Transaction-mutation data adapter (P9.6.c).

Thin wrappers around the POST/PATCH/DELETE/void endpoints used by the
add/edit/void modal. Posting input uses the same ``account=amount[@CUR]``
shape as ``tulip add --post`` so users have one syntax to remember.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import cast

from tulip_cli.http import TulipClient

_POSTING_RE = re.compile(
    r"^\s*(?P<account>.+?)\s*=\s*"
    r"(?P<amount>-?\d+(?:\.\d+)?)"
    r"\s*(?:@\s*(?P<currency>[A-Za-z]{3}))?\s*$"
)


@dataclass(frozen=True, slots=True)
class ParsedPosting:
    """One parsed posting: account ref + amount + optional currency."""

    account: str
    amount: Decimal
    currency: str | None


@dataclass(frozen=True, slots=True)
class TransactionDraft:
    """The full content of the add/edit modal, parsed and ready to POST."""

    date: str
    description: str
    reference: str | None
    postings: tuple[ParsedPosting, ...]


def parse_posting_line(line: str) -> ParsedPosting:
    """Parse one ``account=amount[@CUR]`` line.

    Mirrors :func:`tulip_cli.commands.transactions.parse_posting` —
    same syntax, no surprises. Raises :class:`ValueError` on any
    malformed shape so the modal can render the message inline.
    """
    match = _POSTING_RE.match(line)
    if match is None:
        raise ValueError(f"posting must be 'account=amount[@CURRENCY]', got {line.strip()!r}")
    try:
        amount = Decimal(match.group("amount"))
    except InvalidOperation as exc:
        raise ValueError(f"posting amount {match.group('amount')!r} is not decimal") from exc
    currency = match.group("currency")
    return ParsedPosting(
        account=match.group("account").strip(),
        amount=amount,
        currency=currency.upper() if currency else None,
    )


def parse_postings_block(block: str) -> tuple[ParsedPosting, ...]:
    """Parse a multi-line block of posting lines.

    Blank lines and lines starting with ``#`` are skipped — same
    affordance the CLI's ``--edit`` ledger parser offers, so users
    can leave themselves notes.
    """
    out: list[ParsedPosting] = []
    for idx, raw in enumerate(block.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            out.append(parse_posting_line(stripped))
        except ValueError as exc:
            raise ValueError(f"line {idx}: {exc}") from exc
    if len(out) < 2:
        raise ValueError("transactions must have at least two postings")
    return tuple(out)


def _resolve_account(client: TulipClient, identifier: str) -> str:
    """Resolve a code/name/UUID to an account UUID via /v1/accounts.

    Mirrors the lookup the CLI does inline — kept here so the modal
    doesn't need to import CLI internals.
    """
    accounts = cast(
        "list[dict[str, object]]",
        client.get("/v1/accounts", authenticated=True).json(),
    )
    by_code = {str(a.get("code", "")): a for a in accounts}
    by_id = {str(a.get("id", "")): a for a in accounts}
    if identifier in by_id:
        return identifier
    if identifier in by_code:
        return str(by_code[identifier]["id"])
    # Fallback: name match (case-insensitive).
    lowered = identifier.lower()
    name_matches = [
        a
        for a in accounts
        if isinstance(a.get("name"), str) and cast("str", a["name"]).lower() == lowered
    ]
    if len(name_matches) == 1:
        return str(name_matches[0]["id"])
    raise ValueError(f"unknown account {identifier!r}")


def create_transaction(client: TulipClient, draft: TransactionDraft) -> dict[str, object]:
    """Resolve account refs and ``POST /v1/transactions``."""
    postings: list[dict[str, object]] = []
    for p in draft.postings:
        posting: dict[str, object] = {
            "account_id": _resolve_account(client, p.account),
            "amount": str(p.amount),
        }
        if p.currency:
            posting["currency"] = p.currency
        postings.append(posting)
    body: dict[str, object] = {
        "date": draft.date,
        "description": draft.description,
        "postings": postings,
    }
    if draft.reference:
        body["reference"] = draft.reference
    resp = client.post("/v1/transactions", authenticated=True, json=body)
    return cast("dict[str, object]", resp.json())


def void_transaction(client: TulipClient, tx_id: str, *, reason: str) -> dict[str, object]:
    """Call ``POST /v1/transactions/{id}/void``."""
    resp = client.post(
        f"/v1/transactions/{tx_id}/void",
        authenticated=True,
        json={"reason": reason},
    )
    return cast("dict[str, object]", resp.json())


def delete_transaction(client: TulipClient, tx_id: str) -> None:
    """Call ``DELETE /v1/transactions/{id}`` (PENDING only)."""
    client.delete(f"/v1/transactions/{tx_id}", authenticated=True)


def update_transaction(
    client: TulipClient, tx_id: str, patch: dict[str, object]
) -> dict[str, object]:
    """Call ``PATCH /v1/transactions/{id}`` (PENDING only)."""
    resp = client.patch(f"/v1/transactions/{tx_id}", authenticated=True, json=patch)
    return cast("dict[str, object]", resp.json())


__all__: list[str] = [
    "ParsedPosting",
    "TransactionDraft",
    "create_transaction",
    "delete_transaction",
    "parse_posting_line",
    "parse_postings_block",
    "update_transaction",
    "void_transaction",
]

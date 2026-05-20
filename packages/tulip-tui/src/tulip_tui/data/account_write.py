"""Account-mutation data adapter (#431).

Thin wrappers around ``POST`` / ``PATCH /v1/accounts`` plus a
``ParentCandidate`` projection of the active accounts that match
a given (type, currency) pair, for the parent-picker in the modal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from tulip_cli.http import TulipClient


@dataclass(frozen=True, slots=True)
class AccountDraft:
    """The full content of the add / edit modal, ready to POST or PATCH."""

    name: str
    type: str  # asset / liability / equity / income / expense
    currency: str
    code: str | None
    subtype: str | None
    visibility: str  # shared / private
    parent_account_id: str | None


@dataclass(frozen=True, slots=True)
class ParentCandidate:
    """One row in the parent-picker.

    Subset of ``/v1/accounts`` the API will accept as a parent
    for the focused (type, currency) combination.
    """

    id: str
    code: str | None
    name: str
    type: str
    currency: str


def create_account(client: TulipClient, draft: AccountDraft) -> dict[str, object]:
    """``POST /v1/accounts``. Returns the created account row."""
    body: dict[str, object] = {
        "name": draft.name,
        "type": draft.type,
        "currency": draft.currency,
        "visibility": draft.visibility,
    }
    if draft.code:
        body["code"] = draft.code
    if draft.subtype:
        body["subtype"] = draft.subtype
    if draft.parent_account_id:
        body["parent_account_id"] = draft.parent_account_id
    resp = client.post("/v1/accounts", authenticated=True, json=body)
    return cast("dict[str, object]", resp.json())


def update_account(
    client: TulipClient, account_id: str, patch: dict[str, object]
) -> dict[str, object]:
    """``PATCH /v1/accounts/{id}`` with only the fields the caller wants to change."""
    resp = client.patch(f"/v1/accounts/{account_id}", authenticated=True, json=patch)
    return cast("dict[str, object]", resp.json())


def list_parent_candidates(
    client: TulipClient, *, account_type: str, currency: str
) -> tuple[ParentCandidate, ...]:
    """Fetch ``/v1/accounts`` and project to (type, currency)-matching rows.

    The API's parent-validation rules (#42.a) reject any combination where
    ``parent.type != child.type`` or ``parent.currency != child.currency``;
    pre-filtering on the client side keeps the picker honest and avoids
    a round-trip for any invalid choice. The empty-list case is what the
    screen renders when there's no valid parent yet — the modal still
    submits cleanly with ``parent_account_id=None``.
    """
    accounts = cast(
        "list[dict[str, object]]",
        client.get("/v1/accounts", authenticated=True).json(),
    )
    return tuple(
        ParentCandidate(
            id=str(row.get("id", "")),
            code=_optional_str(row.get("code")),
            name=str(row.get("name", "")),
            type=str(row.get("type", "")),
            currency=str(row.get("currency", "")),
        )
        for row in accounts
        if str(row.get("type", "")) == account_type and str(row.get("currency", "")) == currency
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


__all__: list[str] = [
    "AccountDraft",
    "ParentCandidate",
    "create_account",
    "list_parent_candidates",
    "update_account",
]

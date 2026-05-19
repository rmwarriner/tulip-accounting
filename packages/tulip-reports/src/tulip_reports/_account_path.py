r"""Reports-side ``account_id → Type:Name:...:Name`` path builder (#300).

Mirror of ``tulip_cli._account_path`` for the server-side report
rendering path. Both produce identical strings for identical chart
shapes so the user reads the same vocabulary on the CLI (where the
data comes from API JSON dicts) and in reports (where the data is
ORM ``Account`` objects). Architecture-boundary rules forbid sharing
a module across the two packages — the algorithm is replicated in
lock-step instead.

Rules:

- **Title-case type prefix** (``Asset:`` not ``asset:``) for visual
  consistency with the journal/PTA export's existing convention.
- **Backslash-escape ``:`` and ``\`` in segment names** so a name
  literally containing a colon (e.g. ``Imbalance:Unknown``) renders
  as a single segment that the CLI input resolver can parse back.
- **Graceful orphan fallback**: missing parent in the chain →
  rendered as ``?``; account itself missing from ``accounts_by_id``
  → returns the raw UUID string.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from tulip_storage.models import Account

_TYPE_DISPLAY: dict[str, str] = {
    "asset": "Asset",
    "liability": "Liability",
    "equity": "Equity",
    "income": "Income",
    "expense": "Expense",
}

_ORPHAN_PARENT_MARKER = "?"


def _escape_segment(name: str) -> str:
    r"""Backslash-escape ``\`` (first) and ``:`` in a segment."""
    return name.replace("\\", "\\\\").replace(":", "\\:")


def account_path(
    account_id: UUID,
    accounts_by_id: dict[UUID, Account],
) -> str:
    """Walk an account's parent chain and render the full path."""
    account = accounts_by_id.get(account_id)
    if account is None:
        return str(account_id)

    names: list[str] = []
    seen: set[UUID] = set()
    cur: Account | None = account
    while cur is not None:
        if cur.id in seen:
            break
        seen.add(cur.id)
        names.append(_escape_segment(cur.name))
        if cur.parent_account_id is None:
            cur = None
        else:
            parent = accounts_by_id.get(cur.parent_account_id)
            if parent is None:
                names.append(_ORPHAN_PARENT_MARKER)
                cur = None
            else:
                cur = parent
    names.reverse()

    type_str = account.type.value if hasattr(account.type, "value") else str(account.type)
    type_str = type_str.lower()
    type_display = _TYPE_DISPLAY.get(type_str) or (type_str.capitalize() if type_str else "?")

    return ":".join([type_display, *names])


__all__ = ["account_path"]

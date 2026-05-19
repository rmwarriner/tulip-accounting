r"""Render account hierarchies as ``Type:Name:...:Name`` paths (#300).

The output-side helper that closes the round trip with the input-side
resolver in ``commands.accounts._resolve_account`` (#197). Paths use:

- **Title-case type prefix** (``Asset:`` not ``asset:``) for human
  display. The resolver remains case-insensitive on input, so
  ``asset:current assets:checking`` still resolves.
- **Backslash-escape ``:`` and ``\`` in segment names** so names
  that literally contain a colon (``Imbalance:Unknown``, the
  no-categorize bucket from
  ``tulip_api.services.import_apply``) round-trip through the
  resolver. Without escapes such a name would be indistinguishable
  from a path separator.
- **Graceful orphan fallback**: an account whose
  ``parent_account_id`` points at a row missing from ``by_id``
  renders the gap as ``?`` (e.g. ``Asset:?:Checking``) so partial
  corruption is visible rather than silently skipped.
- **Raw UUID fallback**: an account_id missing from ``by_id``
  entirely renders as the raw UUID string. Matches
  ``_format_account_label``'s (#214) graceful-degrade semantic so
  an orphaned posting renders a row instead of crashing.

Per #300 the path replaces UUIDs everywhere a human reads account
labels. Machine output (``--json``) keeps UUIDs untouched.
"""

from __future__ import annotations

from typing import Any

#: Title-case display labels for the five stored account types. Stored
#: values are the lowercase singulars (``asset``, ``liability``, ...);
#: the resolver accepts both singular and plural lowercase forms via
#: ``_TYPE_ALIASES`` in ``commands.accounts``. Display picks the
#: Title-case singular for visual consistency with
#: ``tulip_reports.journal.export``.
_TYPE_DISPLAY: dict[str, str] = {
    "asset": "Asset",
    "liability": "Liability",
    "equity": "Equity",
    "income": "Income",
    "expense": "Expense",
}

#: Marker rendered in place of a missing parent in the chain.
_ORPHAN_PARENT_MARKER = "?"


def escape_segment(name: str) -> str:
    r"""Backslash-escape ``\`` (first) and ``:`` for path round-trip."""
    return name.replace("\\", "\\\\").replace(":", "\\:")


def unescape_segment(segment: str) -> str:
    """Reverse of :func:`escape_segment` — one already-split segment."""
    out: list[str] = []
    i = 0
    while i < len(segment):
        c = segment[i]
        if c == "\\" and i + 1 < len(segment):
            out.append(segment[i + 1])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def split_path(identifier: str) -> list[str] | None:
    r"""Split a colon-path into unescaped segments. ``None`` on empty segments.

    Honours ``\:`` as a literal colon and ``\\`` as a literal
    backslash. A trailing colon, leading colon, or empty middle
    segment makes the identifier un-resolvable as a path — returns
    ``None`` so the caller falls through to a not-found error.
    """
    raw_segments: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(identifier):
        c = identifier[i]
        if c == "\\" and i + 1 < len(identifier):
            current.append(identifier[i + 1])
            i += 2
            continue
        if c == ":":
            raw_segments.append("".join(current))
            current = []
            i += 1
            continue
        current.append(c)
        i += 1
    raw_segments.append("".join(current))
    stripped = [s.strip() for s in raw_segments]
    if any(not s for s in stripped):
        return None
    return stripped


def account_path(
    account_id: str,
    by_id: dict[str, dict[str, Any]],
) -> str:
    """Render an account's full ``Type:Name:...:Name`` path.

    See module docstring for the round-trip and fallback contract.
    """
    account = by_id.get(account_id)
    if account is None:
        return account_id

    names: list[str] = []
    seen: set[str] = set()
    cur: dict[str, Any] | None = account
    while cur is not None:
        cur_id = str(cur["id"])
        if cur_id in seen:
            break
        seen.add(cur_id)
        names.append(escape_segment(str(cur["name"])))
        parent_id = cur.get("parent_account_id")
        if parent_id is None:
            cur = None
        else:
            parent = by_id.get(str(parent_id))
            if parent is None:
                names.append(_ORPHAN_PARENT_MARKER)
                cur = None
            else:
                cur = parent
    names.reverse()

    type_str = str(account.get("type", "")).lower()
    type_display = _TYPE_DISPLAY.get(type_str) or (type_str.capitalize() if type_str else "?")

    return ":".join([type_display, *names])

"""Pure CLI-side helper to scan ``!Account`` blocks from a QIF file (#443).

The CLI cannot import ``tulip_importers`` per ARCHITECTURE.md §9
(enforced by ``test_tulip_cli_has_no_forbidden_imports``). The
server-side QIF parser lives in ``tulip_importers.qif``; this
file holds the minimal subset the CLI needs for the
``--auto-create-accounts`` flow (just scan ``!Account``
declarations, extract ``(name, qif_type)``).

Keep this lockstep with ``tulip_importers.qif.parser`` —
:func:`list_account_declarations` there has the same shape.
"""

from __future__ import annotations

from dataclasses import dataclass

_RECORD_TERMINATOR = "^"


@dataclass(frozen=True, slots=True)
class QifAccountDeclaration:
    """One ``!Account`` block as seen at the top of a QIF file."""

    name: str
    qif_type: str


def list_account_declarations(file_bytes: bytes) -> list[QifAccountDeclaration]:
    """Return the ``!Account`` blocks declared in a QIF file.

    Each entry pairs the account name (``N`` line) with the type
    token (``T`` line — ``Bank`` / ``CCard`` / etc.). De-duplicated
    by name (first-seen wins). Returns an empty list for files
    with no ``!Account`` blocks.
    """
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return []

    out: list[QifAccountDeclaration] = []
    seen: set[str] = set()

    in_account_block = False
    pending_name: str | None = None
    pending_type: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue
        if line.startswith("!"):
            in_account_block = line.strip().lower() == "!account"
            pending_name = None
            pending_type = None
            continue
        if not in_account_block:
            continue
        if line[0] == "N":
            pending_name = line[1:].strip() or None
        elif line[0] == "T":
            pending_type = line[1:].strip() or None
        elif line == _RECORD_TERMINATOR:
            if pending_name and pending_name not in seen:
                out.append(
                    QifAccountDeclaration(
                        name=pending_name,
                        qif_type=pending_type or "",
                    )
                )
                seen.add(pending_name)
            pending_name = None
            pending_type = None
    return out


__all__ = ["QifAccountDeclaration", "list_account_declarations"]

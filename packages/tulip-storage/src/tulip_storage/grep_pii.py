"""Post-delete erasure-verification scanner (#346 / privacy audit M-21).

The right-to-erasure flow (``DELETE /v1/users/{id}`` landed via #235)
cascades user rows + scrubs the deleted user's PII from ``audit_log``
JSON snapshots — but a long tail of free-text columns and JSON blobs
can still carry the user's identifiers in ways the schema-level
cascade doesn't reach: AI prompt bodies (when ``log_prompts=true``),
pending-proposal payloads, notification bodies, statement-line raw
JSON, etc.

``run_grep_pii`` scans every household-scoped table whose text/JSON
columns might mention a subject and returns per-match rows so the
operator can prove "yes, the data is actually gone" — or surface
what still needs erasure.

Out of scope:
- Encrypted columns (``accounts.notes_encrypted``, etc.) — would
  require decrypting every row; deferred until an operator actually
  needs it. Plaintext mention in adjacent columns is the common case.
- Cross-household scanning — every scan is tenant-scoped.
- Mutation — this is *verification*. The operator decides whether to
  redact, age-out, or accept the residue.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from tulip_storage.models import (
    AIInvocation,
    AuditLog,
    Notification,
    PendingProposal,
    StatementLine,
    Transaction,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

log = logging.getLogger("tulip_storage.grep_pii")

#: Snippet length on either side of a match — enough to be diagnostic
#: without leaking the rest of the row's content.
_SNIPPET_PAD: int = 40


@dataclass(frozen=True, slots=True)
class PiiMatch:
    """One match of a needle in a scanned column."""

    table: str
    column: str
    row_id: str
    snippet: str
    needle: str


def _snippet(haystack: str, needle: str) -> str:
    """Return a `…N chars before…<needle>…N chars after…` excerpt."""
    idx = haystack.lower().find(needle.lower())
    if idx < 0:
        return ""
    start = max(0, idx - _SNIPPET_PAD)
    end = min(len(haystack), idx + len(needle) + _SNIPPET_PAD)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(haystack) else ""
    return f"{prefix}{haystack[start:end]}{suffix}"


def _needles_from(*values: str | None) -> tuple[str, ...]:
    """Drop None / empty / whitespace-only entries; return distinct lower-cased needles."""
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v is None:
            continue
        clean = v.strip()
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return tuple(out)


def _scan_text_column(
    session: Session,
    *,
    household_id: UUID,
    model: Any,  # noqa: ANN401 — SQLAlchemy model class; ORM-typed at the call site
    column_attr: str,
    needles: tuple[str, ...],
    table_name: str,
) -> list[PiiMatch]:
    """Stream rows from ``model`` and match each haystack against every needle."""
    out: list[PiiMatch] = []
    stmt = select(model).where(model.household_id == household_id)
    for row in session.execute(stmt).scalars():
        haystack = getattr(row, column_attr) or ""
        # JSON columns come back as dicts/lists; coerce to a JSON string
        # so str-search applies uniformly.
        if not isinstance(haystack, str):
            try:
                haystack = json.dumps(haystack, ensure_ascii=False)
            except (TypeError, ValueError):
                continue
        if not haystack:
            continue
        lowered = haystack.lower()
        for needle in needles:
            if needle.lower() in lowered:
                out.append(
                    PiiMatch(
                        table=table_name,
                        column=column_attr,
                        row_id=str(row.id),
                        snippet=_snippet(haystack, needle),
                        needle=needle,
                    )
                )
    return out


def run_grep_pii(
    session: Session,
    *,
    household_id: UUID,
    user_id: UUID | str | None = None,
    email: str | None = None,
    display_name: str | None = None,
) -> list[PiiMatch]:
    """Scan household-scoped text/JSON columns for PII identifiers.

    At least one of ``user_id`` / ``email`` / ``display_name`` must be
    non-empty — the scan needs needles. Returns one ``PiiMatch`` per
    (row, needle) hit. Substring + case-insensitive.

    Coverage:
    - ``audit_log.before_snapshot`` + ``after_snapshot`` + ``metadata_``
    - ``ai_invocations.prompt_json`` + ``response_text`` (NULL when not
      opted in via ``log_prompts``)
    - ``pending_proposals.payload`` + ``rationale`` + ``decision_note``
    - ``notifications.body``
    - ``statement_lines.raw_json``
    - ``transactions.description`` + ``reference``
    """
    user_id_str = str(user_id) if user_id is not None else None
    needles = _needles_from(user_id_str, email, display_name)
    if not needles:
        raise ValueError("run_grep_pii requires at least one of user_id / email / display_name")

    matches: list[PiiMatch] = []
    # audit_log: three JSON-ish columns.
    for col in ("before_snapshot", "after_snapshot", "metadata_"):
        matches.extend(
            _scan_text_column(
                session,
                household_id=household_id,
                model=AuditLog,
                column_attr=col,
                needles=needles,
                table_name="audit_log",
            )
        )
    # ai_invocations: prompt + response (NULL unless log_prompts).
    for col in ("prompt_json", "response_text"):
        matches.extend(
            _scan_text_column(
                session,
                household_id=household_id,
                model=AIInvocation,
                column_attr=col,
                needles=needles,
                table_name="ai_invocations",
            )
        )
    # pending_proposals: payload JSON + free-text fields.
    for col in ("payload", "rationale", "decision_note"):
        matches.extend(
            _scan_text_column(
                session,
                household_id=household_id,
                model=PendingProposal,
                column_attr=col,
                needles=needles,
                table_name="pending_proposals",
            )
        )
    # notifications: body.
    matches.extend(
        _scan_text_column(
            session,
            household_id=household_id,
            model=Notification,
            column_attr="body",
            needles=needles,
            table_name="notifications",
        )
    )
    # statement_lines: raw_json carries the source bank-emitted fields.
    matches.extend(
        _scan_text_column(
            session,
            household_id=household_id,
            model=StatementLine,
            column_attr="raw_json",
            needles=needles,
            table_name="statement_lines",
        )
    )
    # transactions: free-text description + reference.
    for col in ("description", "reference"):
        matches.extend(
            _scan_text_column(
                session,
                household_id=household_id,
                model=Transaction,
                column_attr=col,
                needles=needles,
                table_name="transactions",
            )
        )

    log.info(
        "grep_pii.summary",
        extra={
            "household_id": str(household_id),
            "needles": len(needles),
            "matches": len(matches),
        },
    )
    return matches


__all__ = ["PiiMatch", "run_grep_pii"]

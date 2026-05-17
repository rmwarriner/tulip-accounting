"""Helpers for the audit_log BEFORE DELETE trigger carve-out (#333).

The migration that installs ``trg_audit_log_no_delete`` raises ABORT on
every DELETE — but two code paths legitimately need to remove rows:

- Household-erasure cascade (right-to-erasure, #235)
- Tiered retention prune (audit-retention handler, #245)

SQLite has a documented limit that triggers cannot reference TEMP
objects, so the temp-marker pattern used elsewhere isn't available.
Drop-and-recreate is the documented fallback. ``audit_log_deletion_allowed``
brackets a session-scoped block where audit_log DELETEs are permitted;
the trigger is dropped on enter and recreated on exit.

Usage::

    with audit_log_deletion_allowed(session):
        session.execute(sa_delete(Household).where(...))
        session.commit()

The context manager is sync — SQLAlchemy's Session is sync — and
guarantees the trigger is recreated even on exception, so a partial
delete never leaves the database with no protection.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session


#: DDL for the audit_log BEFORE DELETE / BEFORE UPDATE triggers.
#: Duplicated from the migration in
#: ``migrations/versions/20260517_1200_a6f1c9b3d8e4_*.py`` because
#: migration modules have non-identifier filenames (leading digits)
#: and can't be cleanly imported here. If the SQL changes, bump both;
#: the migration test asserts behavioural equivalence.
_TRIGGER_NO_DELETE_SQL: Final[str] = """
CREATE TRIGGER trg_audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only (M-22)');
END;
"""

_TRIGGER_NO_UPDATE_SQL: Final[str] = """
CREATE TRIGGER trg_audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only (M-22)');
END;
"""


@contextmanager
def audit_log_deletion_allowed(session: Session) -> Iterator[None]:
    """Yield with the audit_log DELETE trigger dropped; recreate on exit.

    Use ONLY at the two legitimate cascade-delete sites:
    - household-erasure (``routers/households.py``).
    - audit-retention prune (``runner/handlers/audit_retention.py``).

    Anywhere else, the trigger should fire — that's the point.
    """
    session.execute(text("DROP TRIGGER IF EXISTS trg_audit_log_no_delete"))
    try:
        yield
    finally:
        session.execute(text(_TRIGGER_NO_DELETE_SQL))


@contextmanager
def audit_log_pii_redaction_allowed(session: Session) -> Iterator[None]:
    """Yield with the audit_log UPDATE trigger dropped; recreate on exit.

    Use ONLY at the legitimate PII-scrub site:
    - user-erasure GDPR Art. 17 redaction (``routers/users.py``).

    The scrub is the documented carve-out from "audit_log is append-only":
    the row stays but its ``before_snapshot`` / ``after_snapshot`` /
    ``metadata_`` JSON blobs are nulled so the deleted user's PII doesn't
    survive in historic rows. The audit log keeps the structural fact of
    each event; the snapshot bodies are what carry PII.
    """
    session.execute(text("DROP TRIGGER IF EXISTS trg_audit_log_no_update"))
    try:
        yield
    finally:
        session.execute(text(_TRIGGER_NO_UPDATE_SQL))

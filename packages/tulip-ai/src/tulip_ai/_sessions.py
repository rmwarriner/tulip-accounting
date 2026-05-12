"""Session-sharing helper for AI capabilities (#199, #200).

Every AI capability used to open its own SQLAlchemy session via
``self._session_maker()`` for its DB work + audit write. That works fine
for standalone callers (the ``/v1/ai/*`` endpoints, one short transaction
per request), but breaks when the capability is invoked from inside a
caller's already-open write transaction: SQLite serialises writers, so
the capability's *separate* connection deadlocks on the caller's write
lock and fails with ``database is locked``.

This module gives capabilities an opt-in path to share the caller's
session. Callers that are mid-transaction (the import-apply path is the
motivating case) pass their session; the capability uses it for both the
domain reads and the ``ai_invocations`` write. The audit row then shares
the caller's commit/rollback fate — acceptable because the caller is the
one choosing to share.

Standalone callers continue to pass nothing; the capability opens its
own session as before.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session, sessionmaker


@contextmanager
def use_session_or_make_one(
    session: Session | None,
    session_maker: sessionmaker[Session],
) -> Iterator[tuple[Session, bool]]:
    """Yield ``(session, should_commit)``.

    If ``session`` is given, ``should_commit`` is ``False`` — the caller
    owns transaction boundaries and the capability must not commit. The
    session is not closed on exit.

    Otherwise the helper opens a fresh session via ``session_maker``,
    yields ``(session, True)``, and closes the session on exit. The
    capability is expected to commit when ``should_commit`` is ``True``.
    """
    if session is not None:
        yield session, False
        return
    with session_maker() as own:
        yield own, True


__all__ = ["use_session_or_make_one"]

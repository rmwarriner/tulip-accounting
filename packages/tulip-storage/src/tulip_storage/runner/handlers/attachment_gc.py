"""``attachment_gc`` runner handler — unlink orphaned attachment ciphertext.

Attachments persist their ciphertext at ``attachment_root / <content_hash>``
and a metadata row at ``attachments.content_hash``. When the last row
referencing a hash is deleted (e.g. via ``DELETE /v1/households/me``
cascade, or per-attachment delete), this handler is what removes the
file from disk.

The household-erasure path triggers an immediate GC pass; this scheduled
handler is the periodic safety net that catches any blob whose row
disappeared without a matching unlink (manual SQL, crash mid-delete, etc.).

Race-safety: a freshly written but not-yet-committed attachment file
would be visible on disk before its row appears in the DB. To avoid
deleting such files, the GC ignores any file whose mtime is younger
than ``_MIN_AGE_SECONDS`` (1 hour). The endpoint-driven path doesn't
have this window because it queries the row first; this handler is
the lazy backstop and is intentionally conservative.

See H-3 in #235.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select

from tulip_storage.models import Attachment

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from tulip_storage.models import ScheduledJob
    from tulip_storage.runner.clock import Clock
    from tulip_storage.runner.runner import HandlerCallback

log = logging.getLogger("tulip_storage.runner.attachment_gc")

#: Files newer than this are skipped to avoid racing with concurrent
#: attachment writes whose row hasn't committed yet.
_MIN_AGE_SECONDS: int = 60 * 60


def run_attachment_gc(
    session_maker: sessionmaker[Session],
    attachment_root: Path,
    *,
    now_seconds: float,
    min_age_seconds: int = _MIN_AGE_SECONDS,
) -> int:
    """Walk ``attachment_root`` and unlink files not referenced by any row.

    Pure-ish helper for tests: takes ``now_seconds`` explicitly so a test
    can simulate "old" files without sleeping. Returns the count of
    files deleted.
    """
    if not attachment_root.exists():
        return 0

    with session_maker() as session:
        referenced = {
            row[0] for row in session.execute(select(Attachment.content_hash).distinct()).all()
        }

    deleted = 0
    for path in attachment_root.iterdir():
        if not path.is_file():
            continue
        # Filenames are SHA-256 hex (64 chars); anything else is foreign.
        if len(path.name) != 64:
            continue
        if path.name in referenced:
            continue
        try:
            age = now_seconds - path.stat().st_mtime
        except FileNotFoundError:
            continue
        if age < min_age_seconds:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        deleted += 1
        log.info("attachment_gc.unlinked", extra={"content_hash": path.name})

    if deleted:
        log.info("attachment_gc.summary", extra={"deleted": deleted})
    return deleted


def make_attachment_gc_handler(
    session_maker: sessionmaker[Session],
    attachment_root: Path,
) -> HandlerCallback:
    """Build the ``attachment_gc`` handler bound to a session factory + root.

    Register at runner construction time alongside the other handlers::

        runner.register_handler(
            "attachment_gc",
            make_attachment_gc_handler(session_maker, settings.attachment_root),
        )
    """
    import time

    async def handle(job: ScheduledJob, clock: Clock) -> None:
        run_attachment_gc(
            session_maker,
            attachment_root,
            now_seconds=time.time(),
        )

    return handle

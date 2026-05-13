"""``GET /v1/system/diagnostics`` — surface area for ``tulip doctor`` (#135).

Unauthenticated by design — internal-beta self-hosters run the doctor
*before* registering / logging in to confirm the install is healthy.
The response only exposes booleans + a migration revision id; no paths,
no key bytes, no PII. The marginal information disclosure (a remote
caller can confirm "API up, key loaded, DB migrated") is comparable to
what ``GET /health`` already implies.
"""

from __future__ import annotations

import secrets
import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from sqlalchemy import text

from tulip_api.config import Settings, get_settings
from tulip_api.deps import get_session
from tulip_api.schemas.diagnostics import SystemDiagnosticsRead
from tulip_storage.migrations_meta import expected_alembic_head

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


router = APIRouter(prefix="/v1/system", tags=["meta"])


def _read_alembic_head_in_db(session: Session) -> str | None:
    """Return ``alembic_version.version_num`` or ``None`` if the table is absent."""
    try:
        row = session.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).first()
    except Exception:
        return None
    return str(row[0]) if row else None


def _probe_attachment_root_writable(settings: Settings) -> bool:
    """Create + delete a zero-byte file at ``attachment_root``.

    Creates the directory if it doesn't exist (matches the API's
    on-first-use behaviour for attachment uploads). Random filename to
    avoid collisions if multiple doctor probes race.
    """
    root = settings.attachment_root
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / f".tulip-doctor-{uuid.UUID(bytes=secrets.token_bytes(16)).hex}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return False
    return True


@router.get(
    "/diagnostics",
    response_model=SystemDiagnosticsRead,
)
def get_system_diagnostics(
    session: Session = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> SystemDiagnosticsRead:
    """Aggregate environment + storage probes consumed by ``tulip doctor``."""
    head_in_db = _read_alembic_head_in_db(session)
    head_expected = expected_alembic_head()
    return SystemDiagnosticsRead(
        alembic_head_in_db=head_in_db,
        alembic_head_expected=head_expected,
        alembic_head_match=head_in_db == head_expected,
        master_key_source=settings.master_key_source,
        master_key_loaded=settings.master_key_source != "ephemeral",
        jwt_secret_source=settings.jwt_secret_source,
        deployment_mode=settings.deployment_mode,
        attachment_root_writable=_probe_attachment_root_writable(settings),
    )

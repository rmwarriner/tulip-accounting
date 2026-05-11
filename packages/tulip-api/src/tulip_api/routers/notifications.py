"""HTTP surface for the notifications inbox (P6.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, status

from tulip_api.auth.deps import get_current_claims
from tulip_api.deps import get_session
from tulip_api.errors import TulipProblem, problem_response
from tulip_api.schemas.notification import NotificationRead
from tulip_storage.repositories import NotificationRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/notifications", tags=["notifications"])
log = structlog.get_logger("tulip_api.notifications")


class NotificationNotFoundError(TulipProblem):
    """The notification id doesn't belong to the caller's household."""

    def __init__(self) -> None:
        """Build the notification.not_found problem (P6.3)."""
        super().__init__(
            code="notification.not_found",
            title="Notification not found",
            status=404,
            detail="No notification with that ID exists in this household.",
        )


def _to_read(row: object) -> NotificationRead:
    return NotificationRead.model_validate(row, from_attributes=True)


@router.get(
    "",
    response_model=list[NotificationRead],
    responses={401: problem_response("auth.unauthorized")},
)
def list_notifications(
    include_dismissed: bool = Query(
        default=False,
        description="When true, dismissed rows are also returned.",
    ),
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> list[NotificationRead]:
    """List the household's notifications, newest first."""
    repo = NotificationRepository(session, claims.household_id)
    rows = repo.list_all() if include_dismissed else repo.list_active()
    return [_to_read(r) for r in rows]


@router.post(
    "/{notification_id}/dismiss",
    response_model=NotificationRead,
    status_code=status.HTTP_200_OK,
    responses={
        401: problem_response("auth.unauthorized"),
        404: problem_response("notification.not_found"),
        422: problem_response("validation.failed"),
    },
)
def dismiss_notification(
    notification_id: UUID,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> NotificationRead:
    """Stamp ``dismissed_at`` on the row. Idempotent on already-dismissed."""
    repo = NotificationRepository(session, claims.household_id)
    row = repo.dismiss(notification_id)
    if row is None:
        raise NotificationNotFoundError()
    session.commit()
    log.info("notification.dismissed", notification_id=str(notification_id))
    return _to_read(row)

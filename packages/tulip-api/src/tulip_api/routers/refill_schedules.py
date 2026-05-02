"""Refill-schedule endpoints (P4.3.c).

Routes:

- ``POST   /v1/envelopes/{id}/refill-schedule`` — register a recurring
  refill for the envelope. Creates a ``scheduled_jobs`` row with
  ``kind="envelope_refill"`` and ``idempotency_key=str(envelope_id)``.
- ``GET    /v1/envelopes/{id}/refill-schedule`` — fetch the schedule.
- ``DELETE /v1/envelopes/{id}/refill-schedule`` — cancel.
- ``GET    /v1/scheduled-jobs`` — list all of the household's schedules
  (admin / ops view, cross-kind).
- ``POST   /v1/scheduled-jobs/run-due`` — admin: force a poll tick. The
  runner is exposed for testing and manual catch-up; production use is
  limited because the runner polls automatically per ADR-0002.

The runner is pulled from ``app.state.runner`` via the ``get_runner``
dependency. Tests override that dependency to inject a runner bound to
the test's session factory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request, status

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.deps import get_session
from tulip_api.errors import (
    EnvelopeNotFoundError,
    RefillScheduleAlreadyExistsError,
    RefillScheduleEnvelopeHasNoRefillRuleError,
    RefillScheduleInvalidRRuleError,
    RefillScheduleNotFoundError,
    problem_response,
)
from tulip_api.routers._pool_helpers import filter_for_role
from tulip_api.schemas.refill_schedule import (
    RefillScheduleCreate,
    RefillScheduleRead,
    RunDueResponse,
    ScheduledJobRead,
)
from tulip_storage.repositories import (
    EnvelopeRepository,
    ScheduledJobRepository,
)
from tulip_storage.runner import IdempotencyKeyConflictError, Runner
from tulip_storage.runner.rrule import InvalidRRuleError, compute_next_fire

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims
    from tulip_storage.models import ScheduledJob


REFILL_KIND = "envelope_refill"

router = APIRouter(tags=["refill-schedules"])
log = structlog.get_logger("tulip_api.refill_schedules")


def get_runner(request: Request) -> Runner:
    """Resolve the runner from app.state. Tests override this dependency."""
    runner = getattr(request.app.state, "runner", None)
    if runner is None:
        # Production: runner is started in the lifespan hook. Tests:
        # provide one explicitly. Reaching here means a misconfigured
        # deploy or test setup, not user error → 500.
        from tulip_api.errors import InternalServerError

        raise InternalServerError()
    if not isinstance(runner, Runner):
        # Defensive: if app.state.runner was set to something else, the
        # caller is misconfigured, not the user.
        from tulip_api.errors import InternalServerError

        raise InternalServerError()
    return runner


def _to_refill_read(job: ScheduledJob) -> RefillScheduleRead:
    """Project a ``scheduled_jobs`` row to the refill-specific shape."""
    envelope_id_raw = job.payload.get("envelope_id")
    if not isinstance(envelope_id_raw, str):
        # Schedules created via the runner store envelope_id as a string
        # in the JSON payload. Reaching this branch means a malformed
        # row — fail fast rather than silently emit garbage.
        msg = f"scheduled_job {job.id} payload has non-string envelope_id: {envelope_id_raw!r}"
        raise ValueError(msg)
    envelope_id = UUID(envelope_id_raw)
    return RefillScheduleRead(
        id=job.id,
        envelope_id=envelope_id,
        rrule=job.rrule or "",
        dtstart=job.dtstart,
        next_run_at=job.next_run_at,
        last_run_at=job.last_run_at,
        is_active=job.is_active,
    )


def _to_scheduled_job_read(job: ScheduledJob) -> ScheduledJobRead:
    return ScheduledJobRead(
        id=job.id,
        kind=job.kind,
        rrule=job.rrule,
        dtstart=job.dtstart,
        next_run_at=job.next_run_at,
        last_run_at=job.last_run_at,
        is_active=job.is_active,
        idempotency_key=job.idempotency_key,
    )


# ---- /v1/envelopes/{id}/refill-schedule ----------------------------


@router.post(
    "/v1/envelopes/{envelope_id}/refill-schedule",
    response_model=RefillScheduleRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("envelope.not_found"),
        409: problem_response("refill_schedule.already_exists"),
        400: problem_response(
            "refill_schedule.envelope_has_no_refill_rule",
            "refill_schedule.invalid_rrule",
            "request.body_invalid",
        ),
        422: problem_response("validation.failed"),
    },
)
def create_refill_schedule(
    envelope_id: UUID,
    body: RefillScheduleCreate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    runner: Runner = Depends(get_runner),  # noqa: B008
) -> RefillScheduleRead:
    """Register a recurring refill for an envelope.

    The schedule's idempotency key is the envelope id, so a second POST
    for the same envelope returns ``409 refill_schedule.already_exists``
    rather than silently creating a duplicate.
    """
    # 1. Envelope must exist + be visible to the caller.
    found = EnvelopeRepository(session, claims.household_id).get(envelope_id)
    if found is None:
        raise EnvelopeNotFoundError()
    pool, env = found
    if not filter_for_role(pool, claims):
        raise EnvelopeNotFoundError()

    # 2. Envelope must carry a refill_rule for the runner to evaluate.
    if env.refill_rule_json is None:
        raise RefillScheduleEnvelopeHasNoRefillRuleError()

    # 3. RRULE must parse via dateutil.
    try:
        compute_next_fire(body.rrule, dtstart=body.start_at, after=body.start_at, inclusive=True)
    except InvalidRRuleError as exc:
        raise RefillScheduleInvalidRRuleError(reason=str(exc)) from exc
    except ValueError as exc:
        # `start_at` past the rule's UNTIL would make
        # ``compute_next_fire`` return None — the runner's
        # ``schedule_recurring`` raises ValueError in that case. Surface
        # as invalid_rrule.
        raise RefillScheduleInvalidRRuleError(reason=str(exc)) from exc

    # 4. Schedule via the runner. Idempotency conflicts surface as 409.
    try:
        job_id = runner.schedule_recurring(
            household_id=claims.household_id,
            kind=REFILL_KIND,
            payload={"envelope_id": str(envelope_id)},
            rrule=body.rrule,
            start_at=body.start_at,
            idempotency_key=str(envelope_id),
            created_by_user_id=claims.user_id,
        )
    except IdempotencyKeyConflictError as exc:
        raise RefillScheduleAlreadyExistsError() from exc
    except ValueError as exc:
        # Runner raises ValueError if the RRULE has no occurrence after
        # ``start_at`` (e.g. UNTIL already past).
        raise RefillScheduleInvalidRRuleError(reason=str(exc)) from exc

    log.info(
        "refill_schedule.created",
        envelope_id=str(envelope_id),
        scheduled_job_id=str(job_id),
    )

    # The runner committed in its own session; refresh ours and return.
    repo = ScheduledJobRepository(session, claims.household_id)
    job = repo.get(job_id)
    if job is None:
        # Shouldn't happen — runner just committed. Treat as 500.
        from tulip_api.errors import InternalServerError

        raise InternalServerError()
    return _to_refill_read(job)


@router.get(
    "/v1/envelopes/{envelope_id}/refill-schedule",
    response_model=RefillScheduleRead,
    responses={
        401: problem_response("auth.unauthorized"),
        404: problem_response("envelope.not_found", "refill_schedule.not_found"),
    },
)
def get_refill_schedule(
    envelope_id: UUID,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> RefillScheduleRead:
    """Fetch the active refill schedule for an envelope, if any."""
    found = EnvelopeRepository(session, claims.household_id).get(envelope_id)
    if found is None:
        raise EnvelopeNotFoundError()
    pool, _env = found
    if not filter_for_role(pool, claims):
        raise EnvelopeNotFoundError()

    repo = ScheduledJobRepository(session, claims.household_id)
    job = repo.get_by_idempotency_key(kind=REFILL_KIND, idempotency_key=str(envelope_id))
    if job is None or not job.is_active:
        raise RefillScheduleNotFoundError()
    return _to_refill_read(job)


@router.delete(
    "/v1/envelopes/{envelope_id}/refill-schedule",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("envelope.not_found", "refill_schedule.not_found"),
    },
)
def cancel_refill_schedule(
    envelope_id: UUID,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
    runner: Runner = Depends(get_runner),  # noqa: B008
) -> None:
    """Cancel a refill schedule (flip ``is_active=false``).

    The schedule's row stays in ``scheduled_jobs`` for audit; subsequent
    POSTs for the same envelope succeed because the partial unique index
    only covers ``WHERE idempotency_key IS NOT NULL`` — and we leave the
    cancelled row's key intact. Future P4.3 follow-up: optionally clear
    the idempotency_key on cancel so re-scheduling is trivial. v1 keeps
    the row stable for now.
    """
    found = EnvelopeRepository(session, claims.household_id).get(envelope_id)
    if found is None:
        raise EnvelopeNotFoundError()
    pool, _env = found
    if not filter_for_role(pool, claims):
        raise EnvelopeNotFoundError()

    repo = ScheduledJobRepository(session, claims.household_id)
    job = repo.get_by_idempotency_key(kind=REFILL_KIND, idempotency_key=str(envelope_id))
    if job is None or not job.is_active:
        raise RefillScheduleNotFoundError()

    runner.cancel(claims.household_id, job.id)
    log.info(
        "refill_schedule.cancelled",
        envelope_id=str(envelope_id),
        scheduled_job_id=str(job.id),
    )


# ---- /v1/scheduled-jobs --------------------------------------------


@router.get(
    "/v1/scheduled-jobs",
    response_model=list[ScheduledJobRead],
    responses={401: problem_response("auth.unauthorized")},
)
def list_scheduled_jobs(
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> list[ScheduledJobRead]:
    """List all active scheduled jobs in the caller's household.

    Cross-kind. Useful for ops + future Phase 5/6/7 consumers; the
    refill-specific shape lives at
    ``GET /v1/envelopes/{id}/refill-schedule``.
    """
    repo = ScheduledJobRepository(session, claims.household_id)
    return [_to_scheduled_job_read(j) for j in repo.list_active()]


@router.post(
    "/v1/scheduled-jobs/run-due",
    response_model=RunDueResponse,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
    },
)
async def run_due(
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    runner: Runner = Depends(get_runner),  # noqa: B008
) -> RunDueResponse:
    """Force a poll tick — run anything due now. Admin-only.

    Useful for testing schedules and for manual catch-up after a
    deployment. The runner polls automatically per ADR-0002 §1, so this
    endpoint isn't normally needed in production.
    """
    fired = await runner.run_once()
    log.info("scheduled_jobs.run_due", fired=fired, by_user_id=str(claims.user_id))
    return RunDueResponse(fired=fired)

"""CRUD + YAML import/export for per-household CSV profiles (P5.2.c).

Per ADR-0004 §Q8. The canonical store is the ``csv_profiles`` table
(P5.1); YAML is the export / import format. ``yaml.safe_load`` is
mandatory; ``yaml.load`` is banned by an architecture test in
``tulip-storage/tests/test_architecture_no_unsafe_yaml.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import structlog
import yaml
from fastapi import APIRouter, Body, Depends, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.deps import get_session
from tulip_api.errors import (
    CsvProfileDuplicateNameError,
    CsvProfileInvalidYamlError,
    CsvProfileNotFoundError,
    ValidationFailedError,
    problem_response,
)
from tulip_api.schemas.csv_profile import (
    CsvProfileCreate,
    CsvProfileRead,
    CsvProfileUpdate,
)
from tulip_importers.csv import CsvProfile
from tulip_storage.models import CsvProfile as CsvProfileRow
from tulip_storage.repositories import (
    AuditLogWriter,
    CsvProfileRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/imports/profiles", tags=["imports"])
log = structlog.get_logger("tulip_api.csv_profiles")


def _request_uuid(request: Request) -> UUID | None:
    rid = request.headers.get("x-request-id")
    if rid:
        try:
            return UUID(rid)
        except ValueError:
            return None
    return None


def _resolve(repo: CsvProfileRepository, id_or_name: str) -> CsvProfileRow:
    """Resolve a UUID-or-name string to a CsvProfile row.

    Mirrors the ``_resolve_account`` pattern from accounts.py: try UUID
    first, fall back to name lookup. Raises ``CsvProfileNotFoundError``
    on miss.
    """
    try:
        uuid_value = UUID(id_or_name)
    except ValueError:
        row = repo.get_by_name(id_or_name)
    else:
        row = repo.get(uuid_value)
    if row is None:
        raise CsvProfileNotFoundError()
    return row


def _to_read(row: CsvProfileRow) -> CsvProfileRead:
    """Materialize a CsvProfileRow + its YAML body into the read schema."""
    profile = CsvProfile.from_yaml(row.yaml_body)
    return CsvProfileRead(
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        **profile.model_dump(),
    )


@router.get(
    "",
    response_model=list[CsvProfileRead],
    responses={401: problem_response("auth.unauthorized")},
)
def list_profiles(
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> list[CsvProfileRead]:
    """List CSV profiles in this household, ordered by name."""
    repo = CsvProfileRepository(session, claims.household_id)
    return [_to_read(row) for row in repo.list_all()]


@router.post(
    "",
    response_model=CsvProfileRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: problem_response("auth.unauthorized"),
        409: problem_response("csv_profile.duplicate_name"),
        422: problem_response("validation.failed"),
    },
)
def create_profile(
    body: CsvProfileCreate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> CsvProfileRead:
    """Create a new CSV profile."""
    repo = CsvProfileRepository(session, claims.household_id)
    if repo.get_by_name(body.name) is not None:
        raise CsvProfileDuplicateNameError(name=body.name)
    yaml_body = body.to_yaml()
    try:
        row = repo.create(
            name=body.name,
            yaml_body=yaml_body,
            created_by_user_id=claims.user_id,
        )
    except IntegrityError as exc:
        # Defense in depth: the unique-name index also fires here.
        raise CsvProfileDuplicateNameError(name=body.name) from exc

    AuditLogWriter(session, claims.household_id).write(
        action="csv_profile_create",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="csv_profile",
        entity_id=row.id,
        after={"name": body.name},
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info("csv_profile.created", profile_id=str(row.id), name=body.name)
    return _to_read(row)


@router.get(
    "/{id_or_name}",
    response_model=CsvProfileRead,
    responses={
        401: problem_response("auth.unauthorized"),
        404: problem_response("csv_profile.not_found"),
    },
)
def get_profile(
    id_or_name: str,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> CsvProfileRead:
    """Fetch a CSV profile by UUID or name."""
    row = _resolve(CsvProfileRepository(session, claims.household_id), id_or_name)
    return _to_read(row)


@router.patch(
    "/{id_or_name}",
    response_model=CsvProfileRead,
    responses={
        401: problem_response("auth.unauthorized"),
        404: problem_response("csv_profile.not_found"),
        409: problem_response("csv_profile.duplicate_name"),
        422: problem_response("validation.failed"),
    },
)
def update_profile(
    id_or_name: str,
    body: CsvProfileUpdate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> CsvProfileRead:
    """Patch any subset of a CSV profile's fields."""
    repo = CsvProfileRepository(session, claims.household_id)
    row = _resolve(repo, id_or_name)
    current = CsvProfile.from_yaml(row.yaml_body)
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        # No-op patch — return current.
        return _to_read(row)

    if "name" in patch and patch["name"] != row.name:
        if repo.get_by_name(patch["name"]) is not None:
            raise CsvProfileDuplicateNameError(name=patch["name"])

    try:
        updated_profile = current.model_copy(update=patch)
        # Re-validate by round-tripping through the model.
        updated_profile = CsvProfile.model_validate(updated_profile.model_dump())
    except ValidationError as exc:
        raise ValidationFailedError(errors=[dict(e) for e in exc.errors()]) from exc

    new_yaml = updated_profile.to_yaml()
    if "name" in patch and patch["name"] != row.name:
        # Hard rename — update both name column + yaml_body.
        row.name = patch["name"]
    repo.update_yaml(row.id, new_yaml)

    AuditLogWriter(session, claims.household_id).write(
        action="csv_profile_update",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="csv_profile",
        entity_id=row.id,
        after=patch,
        request_id=_request_uuid(request),
    )
    session.commit()
    refreshed = repo.get(row.id)
    assert refreshed is not None  # noqa: S101 - just updated above
    return _to_read(refreshed)


@router.delete(
    "/{id_or_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: problem_response("auth.unauthorized"),
        404: problem_response("csv_profile.not_found"),
    },
)
def delete_profile(
    id_or_name: str,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> None:
    """Hard-delete a CSV profile. Profiles aren't FK-referenced from import_batches."""
    repo = CsvProfileRepository(session, claims.household_id)
    row = _resolve(repo, id_or_name)
    name = row.name
    profile_id = row.id
    repo.delete(profile_id)
    AuditLogWriter(session, claims.household_id).write(
        action="csv_profile_delete",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="csv_profile",
        entity_id=profile_id,
        before={"name": name},
        request_id=_request_uuid(request),
    )
    session.commit()


@router.get(
    "/{id_or_name}/export",
    responses={
        200: {
            "content": {"application/x-yaml": {}},
            "description": "YAML serialization of the profile.",
        },
        401: problem_response("auth.unauthorized"),
        404: problem_response("csv_profile.not_found"),
    },
)
def export_profile(
    id_or_name: str,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> Response:
    """Return the YAML body of a profile as ``application/x-yaml``."""
    row = _resolve(CsvProfileRepository(session, claims.household_id), id_or_name)
    return Response(content=row.yaml_body, media_type="application/x-yaml")


@router.post(
    "/import",
    response_model=CsvProfileRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: problem_response("csv_profile.invalid_yaml"),
        401: problem_response("auth.unauthorized"),
        409: problem_response("csv_profile.duplicate_name"),
        422: problem_response("validation.failed"),
    },
)
def import_profile(
    request: Request,
    body: bytes = Body(
        ...,
        media_type="application/x-yaml",
        description="YAML profile body, as emitted by GET /export.",
    ),
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> CsvProfileRead:
    """Import a CSV profile from a YAML body (round-trip with /export)."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CsvProfileInvalidYamlError(reason=f"YAML body is not valid UTF-8: {exc}") from exc
    try:
        profile = CsvProfile.from_yaml(text)
    except yaml.YAMLError as exc:
        raise CsvProfileInvalidYamlError(reason=f"yaml.safe_load: {exc}") from exc
    except ValueError as exc:
        raise CsvProfileInvalidYamlError(reason=str(exc)) from exc
    except ValidationError as exc:
        raise ValidationFailedError(errors=[dict(e) for e in exc.errors()]) from exc

    repo = CsvProfileRepository(session, claims.household_id)
    if repo.get_by_name(profile.name) is not None:
        raise CsvProfileDuplicateNameError(name=profile.name)
    row = repo.create(
        name=profile.name,
        yaml_body=profile.to_yaml(),
        created_by_user_id=claims.user_id,
    )
    AuditLogWriter(session, claims.household_id).write(
        action="csv_profile_import",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="csv_profile",
        entity_id=row.id,
        after={"name": profile.name},
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info("csv_profile.imported", profile_id=str(row.id), name=profile.name)
    return _to_read(row)

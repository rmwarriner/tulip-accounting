"""POST/GET /v1/imports — upload statement files + retrieve parsed batches.

Per ADR-0004 §"Statement-format normalization" / §Q6 / §Q9. P5.2.a ships
the OFX path; QIF and CSV land in P5.2.b/c. The handler:

1. Validates content_type + size cap (defense-in-depth before slurping).
2. Hashes the bytes; if the hash already exists for this household and
   account, returns ``import.duplicate_file`` (409). The ADR specifies a
   ``?force=true`` override (#114): the duplicate check is application-
   level, the underlying index is non-unique, and the audit log records
   the override.
3. Persists the encrypted bytes via ``AttachmentRepository``.
4. Creates the ``import_batches`` row.
5. Calls the format-specific parser (``tulip_importers.ofx.parse``) and
   bulk-inserts the resulting ``StatementLine`` rows.
6. Audit-logs ``import_create`` and commits.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from datetime import datetime
from typing import TYPE_CHECKING, Final
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.config import get_settings
from tulip_api.deps import get_session
from tulip_api.errors import (
    AccountUnknownError,
    CsvProfileNotFoundError,
    ForbiddenError,
    ImportAlreadyAppliedError,
    ImportBatchNotFoundError,
    ImportCategorizeUnknownAccountError,
    ImportCsvParseFailedError,
    ImportCsvProfileMissingError,
    ImportDuplicateFileError,
    ImportOfxParseFailedError,
    ImportQifParseFailedError,
    ImportUnsupportedFormatError,
    RequestPayloadTooLargeError,
    StatementLineAlreadyPromotedError,
    StatementLineExcludedError,
    StatementLineNotFoundError,
    UnsupportedMediaTypeError,
    problem_response,
)
from tulip_api.schemas.import_batch import (
    ImportBatchApplyResponse,
    ImportBatchListItem,
    ImportBatchListResponse,
    ImportBatchRead,
    ImportBatchSummary,
    StatementLinePromoteResponse,
    StatementLineRead,
)
from tulip_api.services.import_apply import (
    BatchAlreadyAppliedError,
    CategorizeUnknownAccountError,
    LineAlreadyPromotedError,
    LineExcludedError,
    apply_batch,
    promote_statement_line,
)
from tulip_core.reconciliation.categorizer import get_categorizer
from tulip_importers.csv import CsvParseError, CsvProfile
from tulip_importers.csv import parse as csv_parse
from tulip_importers.ofx import OfxParseError
from tulip_importers.ofx import parse as ofx_parse
from tulip_importers.qif import QifParseError
from tulip_importers.qif import parse as qif_parse
from tulip_storage.models import ImportBatchStatus, SourceFormat
from tulip_storage.repositories import (
    AccountRepository,
    AttachmentRepository,
    AuditLogWriter,
    CsvProfileRepository,
    ImportBatchRepository,
    StatementLineRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims


router = APIRouter(prefix="/v1/imports", tags=["imports"])
log = structlog.get_logger("tulip_api.imports")

#: Cap on accepted upload size. Real bank statements are well under 1 MB;
#: 25 MB is generous and still bounds the worst case. Follow-up: plumb
#: through ``Settings.max_upload_bytes`` for per-deployment overrides.
MAX_OFX_BYTES: Final[int] = 25 * 1024 * 1024

_OFX_CONTENT_TYPES: Final[tuple[str, ...]] = (
    "application/x-ofx",
    "application/octet-stream",
    "text/xml",
    "application/xml",
)

_QIF_CONTENT_TYPES: Final[tuple[str, ...]] = (
    "application/qif",
    "application/x-qif",
    "application/octet-stream",
    "text/plain",
)

_CSV_CONTENT_TYPES: Final[tuple[str, ...]] = (
    "text/csv",
    "application/csv",
    "application/octet-stream",
    "text/plain",
)

_SUPPORTED_FORMATS: Final[tuple[str, ...]] = ("ofx", "qif", "csv")


def _request_uuid(request: Request) -> UUID | None:
    rid = request.headers.get("x-request-id")
    if rid:
        try:
            return UUID(rid)
        except ValueError:
            return None
    return None


@router.post(
    "",
    response_model=ImportBatchSummary,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: problem_response(
            "account.unknown",
            "import.ofx_parse_failed",
            "import.qif_parse_failed",
            "import.csv_parse_failed",
            "import.csv_profile_missing",
            "import.unsupported_format",
            "request.body_invalid",
        ),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("csv_profile.not_found"),
        409: problem_response("import.duplicate_file"),
        413: problem_response("request.payload_too_large"),
        415: problem_response("request.unsupported_media_type"),
        422: problem_response("validation.failed"),
    },
)
async def upload_import(
    request: Request,
    file: UploadFile = File(..., description="OFX (XML/SGML), QIF, or CSV text."),  # noqa: B008
    account_id: UUID = Form(..., description="Account this statement belongs to."),  # noqa: B008
    source_format: str = Form(
        "ofx",
        description="Statement format ('ofx', 'qif', or 'csv').",
    ),
    profile_id: UUID | None = Form(  # noqa: B008
        None,
        description=(
            "CSV column-mapping profile (UUID). Required when "
            "source_format='csv'; ignored otherwise."
        ),
    ),
    force: bool = Query(
        default=False,
        description=(
            "When true, skip the same-file/same-account duplicate check and "
            "create a second import batch referencing the existing attachment. "
            "**Admin-only** per #230 (audit M-16) — members are rejected with "
            "403 `auth.forbidden`. The audit log records every force-override."
        ),
    ),
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> ImportBatchSummary:
    """Upload a statement file; parse it; persist as an ``import_batches`` row."""
    if force and claims.role != "admin":
        # The dedup override is a deliberate admin action — refuse for members
        # so the audit-row "force=true" stays correctly attributable.
        raise ForbiddenError(detail="force=true requires admin role.")

    if source_format not in _SUPPORTED_FORMATS:
        raise ImportUnsupportedFormatError(
            format_name=source_format,
            supported=_SUPPORTED_FORMATS,
        )

    received_ct = (file.content_type or "").lower()
    if source_format == "ofx":
        accepted_cts = _OFX_CONTENT_TYPES
    elif source_format == "qif":
        accepted_cts = _QIF_CONTENT_TYPES
    else:
        accepted_cts = _CSV_CONTENT_TYPES
    if received_ct not in accepted_cts:
        raise UnsupportedMediaTypeError(
            accepted=accepted_cts,
            received=received_ct or "<missing>",
        )

    raw_bytes = await file.read()
    if len(raw_bytes) > MAX_OFX_BYTES:
        raise RequestPayloadTooLargeError(max_bytes=MAX_OFX_BYTES)
    if not raw_bytes:
        if source_format == "ofx":
            raise ImportOfxParseFailedError(reason="uploaded file is empty")
        if source_format == "qif":
            raise ImportQifParseFailedError(reason="uploaded file is empty")
        raise ImportCsvParseFailedError(reason="uploaded file is empty")

    accounts_repo = AccountRepository(session, claims.household_id)
    account = accounts_repo.get(account_id)
    if account is None:
        raise AccountUnknownError(account_id=str(account_id))

    csv_profile: CsvProfile | None = None
    if source_format == "csv":
        if profile_id is None:
            raise ImportCsvProfileMissingError()
        profile_row = CsvProfileRepository(session, claims.household_id).get(profile_id)
        if profile_row is None:
            raise CsvProfileNotFoundError()
        csv_profile = CsvProfile.from_yaml(profile_row.yaml_body)

    settings = get_settings()
    attachment_repo = AttachmentRepository(
        session,
        claims.household_id,
        master_key=settings.master_key,
        attachment_root=settings.attachment_root,
    )

    # Idempotency: same hash + same account = same batch, unless ?force=true
    # explicitly opts out (ADR-0004 §Q6). The audit log records the override
    # so the admin trail is honest about the duplicate.
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    existing_attachment = attachment_repo.find_by_hash(content_hash)
    batch_repo = ImportBatchRepository(session, claims.household_id)
    if existing_attachment is not None and not force:
        existing_batch = batch_repo.find_for_attachment(
            account_id=account_id,
            attachment_id=existing_attachment.id,
        )
        if existing_batch is not None:
            raise ImportDuplicateFileError(
                content_hash=content_hash,
                existing_batch_id=str(existing_batch.id),
            )

    # Parse before any DB write so we can fail fast on garbage uploads.
    if source_format == "ofx":
        try:
            parsed_lines = ofx_parse(raw_bytes)
        except OfxParseError as exc:
            raise ImportOfxParseFailedError(reason=str(exc)) from exc
    elif source_format == "qif":
        try:
            parsed_lines = qif_parse(raw_bytes, currency=account.currency)
        except QifParseError as exc:
            raise ImportQifParseFailedError(reason=str(exc)) from exc
    else:  # csv
        # csv_profile guaranteed non-None by the source_format=='csv' branch above.
        assert csv_profile is not None  # noqa: S101 - existence verified above
        try:
            parsed_lines = csv_parse(
                raw_bytes,
                profile=csv_profile,
                currency=account.currency,
            )
        except CsvParseError as exc:
            raise ImportCsvParseFailedError(reason=str(exc)) from exc

    # Persist: attachment → batch → lines.
    if source_format == "ofx":
        default_filename, default_ct = "upload.ofx", "application/x-ofx"
    elif source_format == "qif":
        default_filename, default_ct = "upload.qif", "application/qif"
    else:
        default_filename, default_ct = "upload.csv", "text/csv"
    if existing_attachment is None:
        attachment = attachment_repo.create(
            filename=file.filename or default_filename,
            content_type=file.content_type or default_ct,
            raw_bytes=raw_bytes,
            uploaded_by_user_id=claims.user_id,
        )
    else:
        attachment = existing_attachment

    if source_format == "ofx":
        storage_format = SourceFormat.OFX
    elif source_format == "qif":
        storage_format = SourceFormat.QIF
    else:
        storage_format = SourceFormat.CSV
    batch = batch_repo.create(
        account_id=account_id,
        source_format=storage_format,
        source_filename=file.filename or default_filename,
        source_file_attachment_id=attachment.id,
        created_by_user_id=claims.user_id,
        summary_json={"line_count": len(parsed_lines)},
    )

    line_repo = StatementLineRepository(session, claims.household_id)
    line_repo.bulk_insert(
        batch.id,
        [
            {
                "line_number": parsed.line_number,
                "posted_date": parsed.posted_date,
                "amount": parsed.amount.amount,
                "currency": parsed.amount.currency,
                "description": parsed.description,
                "counterparty": parsed.counterparty,
                "reference": parsed.reference,
                "fitid": parsed.fitid,
                "raw_json": str(dict(parsed.raw)),
            }
            for parsed in parsed_lines
        ],
    )

    # Update counts on the batch row now that we know.
    batch.imported_count = len(parsed_lines)

    AuditLogWriter(session, claims.household_id).write(
        action="import_create",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="import_batch",
        entity_id=batch.id,
        after={
            "account_id": str(account_id),
            "source_format": source_format,
            "source_filename": file.filename or default_filename,
            "line_count": len(parsed_lines),
            "force": force,
        },
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info(
        "import.created",
        batch_id=str(batch.id),
        line_count=len(parsed_lines),
    )

    return ImportBatchSummary(
        id=batch.id,
        account_id=account_id,
        source_format=source_format,
        source_filename=batch.source_filename,
        status=batch.status.value,
        statement_line_count=len(parsed_lines),
        imported_count=batch.imported_count,
        skipped_count=batch.skipped_count,
        error_count=batch.error_count,
        created_at=batch.created_at,
    )


@router.post(
    "/{batch_id}/apply",
    response_model=ImportBatchApplyResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("import_batch.not_found"),
        409: problem_response(
            "import.already_applied",
            "import.categorize.unknown_account",
        ),
    },
)
async def apply_import(
    batch_id: UUID,
    request: Request,
    no_categorize: bool = Query(
        default=False,
        description=(
            "If true, skip the categorizer entirely. Each line lands "
            "balanced against the household's Imbalance:Unknown account "
            "(auto-created per currency on first use) so the user can "
            "review + assign categories manually. Useful for bulk "
            "migrations from another accounting tool."
        ),
    ),
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> ImportBatchApplyResponse:
    """Promote every non-excluded line in the batch to a PENDING ledger tx.

    Per ADR-0004 §Q4. Idempotent at the batch level: re-applying an
    already-applied batch returns ``import.already_applied`` (409). To
    promote a specific line individually, see
    ``POST /v1/imports/{batch_id}/lines/{line_id}/promote``.
    """
    batch_repo = ImportBatchRepository(session, claims.household_id)
    batch = batch_repo.get(batch_id)
    if batch is None:
        raise ImportBatchNotFoundError()

    try:
        result = await apply_batch(
            session=session,
            household_id=claims.household_id,
            batch=batch,
            categorizer=get_categorizer(),
            actor_user_id=claims.user_id,
            no_categorize=no_categorize,
        )
    except BatchAlreadyAppliedError as exc:
        raise ImportAlreadyAppliedError(batch_id=str(batch_id)) from exc
    except CategorizeUnknownAccountError as exc:
        raise ImportCategorizeUnknownAccountError(account_code=exc.account_code) from exc

    AuditLogWriter(session, claims.household_id).write(
        action="import_apply",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="import_batch",
        entity_id=batch.id,
        after={
            "created_count": result.created_count,
            "skipped_count": result.skipped_count,
            "transaction_ids": [str(t) for t in result.transaction_ids],
        },
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info(
        "import.applied",
        batch_id=str(batch.id),
        created=result.created_count,
        skipped=result.skipped_count,
    )
    return ImportBatchApplyResponse(
        batch_id=batch.id,
        status="applied",
        created_count=result.created_count,
        skipped_count=result.skipped_count,
        transaction_ids=list(result.transaction_ids),
    )


@router.post(
    "/{batch_id}/lines/{line_id}/promote",
    response_model=StatementLinePromoteResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("import_batch.not_found", "import.line.not_found"),
        409: problem_response(
            "import.line.already_promoted",
            "import.categorize.unknown_account",
        ),
        422: problem_response("import.line.excluded"),
    },
)
async def promote_line(
    batch_id: UUID,
    line_id: UUID,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> StatementLinePromoteResponse:
    """Promote one statement line to a PENDING ledger transaction.

    Per ADR-0004 §Q4. Useful for line-by-line review or for re-promoting
    a previously-excluded line after un-excluding it.
    """
    batch_repo = ImportBatchRepository(session, claims.household_id)
    batch = batch_repo.get(batch_id)
    if batch is None:
        raise ImportBatchNotFoundError()

    line_repo = StatementLineRepository(session, claims.household_id)
    line = line_repo.get(line_id)
    if line is None or line.import_batch_id != batch_id:
        raise StatementLineNotFoundError()

    try:
        tx = await promote_statement_line(
            session=session,
            household_id=claims.household_id,
            batch=batch,
            line=line,
            categorizer=get_categorizer(),
            actor_user_id=claims.user_id,
        )
    except LineAlreadyPromotedError as exc:
        # Re-fetch (line.promoted_transaction_id may have been set).
        line2 = line_repo.get(line_id)
        assert line2 is not None  # noqa: S101 - existence verified above
        raise StatementLineAlreadyPromotedError(
            line_id=str(line_id),
            transaction_id=str(line2.promoted_transaction_id),
        ) from exc
    except LineExcludedError as exc:
        raise StatementLineExcludedError(line_id=str(line_id)) from exc
    except CategorizeUnknownAccountError as exc:
        raise ImportCategorizeUnknownAccountError(account_code=exc.account_code) from exc

    AuditLogWriter(session, claims.household_id).write(
        action="statement_line_promote",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="statement_line",
        entity_id=line.id,
        after={"transaction_id": str(tx.id), "batch_id": str(batch.id)},
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info(
        "statement_line.promoted",
        line_id=str(line.id),
        transaction_id=str(tx.id),
    )
    return StatementLinePromoteResponse(
        statement_line_id=line.id,
        transaction_id=tx.id,
    )


_LIST_DEFAULT_LIMIT: Final[int] = 25
_LIST_MAX_LIMIT: Final[int] = 200


def _encode_cursor(created_at: datetime, batch_id: UUID) -> str:
    """Encode an (created_at, id) tuple as an opaque base64 cursor.

    The cursor pairs the timestamp with the id so paging is stable when
    multiple batches land in the same microsecond (a tiebreaker the SQL
    ORDER BY already uses). Base64 keeps the value URL-safe and signals
    "opaque" to callers — they should not try to construct one by hand.
    """
    payload = f"{created_at.isoformat()}|{batch_id}".encode()
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Inverse of :func:`_encode_cursor`. Raises ValueError on malformed input."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"invalid cursor: {exc}") from exc
    if "|" not in raw:
        raise ValueError("invalid cursor: missing separator")
    ts_str, _, id_str = raw.partition("|")
    return datetime.fromisoformat(ts_str), UUID(id_str)


@router.get(
    "",
    response_model=ImportBatchListResponse,
    responses={
        401: problem_response("auth.unauthorized"),
        422: problem_response("validation.failed"),
    },
)
def list_import_batches(
    status_: str | None = Query(
        default=None,
        alias="status",
        description=(
            "Filter to batches with this status. One of ``parsed``, ``applied``, ``reverted``."
        ),
        pattern="^(parsed|applied|reverted)$",
    ),
    account_id: UUID | None = Query(  # noqa: B008
        default=None,
        description="Filter to batches belonging to this account.",
    ),
    after: str | None = Query(
        default=None,
        description=(
            "Opaque cursor returned by a prior call as ``next_cursor``. Pass to "
            "fetch the next page; omit on the first request."
        ),
    ),
    limit: int = Query(
        default=_LIST_DEFAULT_LIMIT,
        ge=1,
        le=_LIST_MAX_LIMIT,
        description=(
            f"Cap on rows returned (1-{_LIST_MAX_LIMIT}). Defaults to {_LIST_DEFAULT_LIMIT}."
        ),
    ),
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> ImportBatchListResponse:
    """List recent import batches for the caller's household, newest first.

    Page size defaults to 25 (capped at 200). Pass ``next_cursor`` from a
    prior response back as ``?after=…`` to fetch the next page. Filters
    AND together: ``?status=parsed&account_id=…`` returns only parsed
    batches for that account.
    """
    storage_status: ImportBatchStatus | None = (
        ImportBatchStatus(status_) if status_ is not None else None
    )

    cursor: tuple[datetime, UUID] | None = None
    if after is not None:
        try:
            cursor = _decode_cursor(after)
        except ValueError as exc:
            # Surface as 422 so the schemathesis contract test recognises
            # the failure mode; mirror the body_invalid pattern.
            from tulip_api.errors import ValidationFailedError

            raise ValidationFailedError(
                errors=[{"loc": ["query", "after"], "msg": str(exc), "type": "value_error"}]
            ) from exc

    batches = ImportBatchRepository(session, claims.household_id).list_recent(
        status=storage_status,
        account_id=account_id,
        after=cursor,
        limit=limit,
    )

    next_cursor: str | None = None
    if len(batches) == limit:
        last = batches[-1]
        next_cursor = _encode_cursor(last.created_at, last.id)

    return ImportBatchListResponse(
        items=[
            ImportBatchListItem(
                id=batch.id,
                account_id=batch.account_id,
                source_format=batch.source_format.value,
                source_filename=batch.source_filename,
                status=batch.status.value,
                imported_count=batch.imported_count,
                skipped_count=batch.skipped_count,
                created_at=batch.created_at,
            )
            for batch in batches
        ],
        next_cursor=next_cursor,
    )


@router.get(
    "/{batch_id}",
    response_model=ImportBatchRead,
    responses={
        401: problem_response("auth.unauthorized"),
        404: problem_response("import_batch.not_found"),
    },
)
def get_import_batch(
    batch_id: UUID,
    claims: Claims = Depends(get_current_claims),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> ImportBatchRead:
    """Fetch an import batch + its parsed statement lines, in source-file order."""
    batch_repo = ImportBatchRepository(session, claims.household_id)
    batch = batch_repo.get(batch_id)
    if batch is None:
        raise ImportBatchNotFoundError()

    lines = StatementLineRepository(session, claims.household_id).list_for_batch(batch_id)
    return ImportBatchRead(
        id=batch.id,
        account_id=batch.account_id,
        source_format=batch.source_format.value,
        source_filename=batch.source_filename,
        status=batch.status.value,
        imported_count=batch.imported_count,
        skipped_count=batch.skipped_count,
        error_count=batch.error_count,
        created_at=batch.created_at,
        applied_at=batch.applied_at,
        reverted_at=batch.reverted_at,
        lines=[
            StatementLineRead(
                id=line.id,
                line_number=line.line_number,
                posted_date=line.posted_date,
                amount=line.amount,
                currency=line.currency,
                description=line.description,
                counterparty=line.counterparty,
                reference=line.reference,
                fitid=line.fitid,
                is_excluded=line.is_excluded,
                reconciliation_match_id=line.reconciliation_match_id,
            )
            for line in lines
        ],
    )

"""POST/GET /v1/imports — upload statement files + retrieve parsed batches.

Per ADR-0004 §"Statement-format normalization" / §Q6 / §Q9. P5.2.a ships
the OFX path; QIF and CSV land in P5.2.b/c. The handler:

1. Validates content_type + size cap (defense-in-depth before slurping).
2. Hashes the bytes; if the hash already exists for this household and
   account, returns ``import.duplicate_file`` (409). The ADR specifies a
   ``?force=true`` override, but the underlying unique index in P5.1
   forbids re-creating an ``import_batches`` row referencing the same
   attachment for the same account; force-mode is tracked as a follow-up.
3. Persists the encrypted bytes via ``AttachmentRepository``.
4. Creates the ``import_batches`` row.
5. Calls the format-specific parser (``tulip_importers.ofx.parse``) and
   bulk-inserts the resulting ``StatementLine`` rows.
6. Audit-logs ``import_create`` and commits.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.config import get_settings
from tulip_api.deps import get_session
from tulip_api.errors import (
    AccountUnknownError,
    ImportBatchNotFoundError,
    ImportDuplicateFileError,
    ImportOfxParseFailedError,
    ImportQifParseFailedError,
    ImportUnsupportedFormatError,
    RequestPayloadTooLargeError,
    UnsupportedMediaTypeError,
    problem_response,
)
from tulip_api.schemas.import_batch import (
    ImportBatchRead,
    ImportBatchSummary,
    StatementLineRead,
)
from tulip_importers.ofx import OfxParseError
from tulip_importers.ofx import parse as ofx_parse
from tulip_importers.qif import QifParseError
from tulip_importers.qif import parse as qif_parse
from tulip_storage.models import SourceFormat
from tulip_storage.repositories import (
    AccountRepository,
    AttachmentRepository,
    AuditLogWriter,
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

_SUPPORTED_FORMATS: Final[tuple[str, ...]] = ("ofx", "qif")


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
            "import.unsupported_format",
            "request.body_invalid",
        ),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        409: problem_response("import.duplicate_file"),
        413: problem_response("request.payload_too_large"),
        415: problem_response("request.unsupported_media_type"),
        422: problem_response("validation.failed"),
    },
)
async def upload_import(
    request: Request,
    file: UploadFile = File(..., description="OFX (XML/SGML) or QIF text."),  # noqa: B008
    account_id: UUID = Form(..., description="Account this statement belongs to."),  # noqa: B008
    source_format: str = Form(
        "ofx",
        description="Statement format ('ofx' or 'qif'; CSV lands in P5.2.c).",
    ),
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> ImportBatchSummary:
    """Upload a statement file; parse it; persist as an ``import_batches`` row."""
    if source_format not in _SUPPORTED_FORMATS:
        raise ImportUnsupportedFormatError(
            format_name=source_format,
            supported=_SUPPORTED_FORMATS,
        )

    received_ct = (file.content_type or "").lower()
    accepted_cts = _OFX_CONTENT_TYPES if source_format == "ofx" else _QIF_CONTENT_TYPES
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
        raise ImportQifParseFailedError(reason="uploaded file is empty")

    accounts_repo = AccountRepository(session, claims.household_id)
    account = accounts_repo.get(account_id)
    if account is None:
        raise AccountUnknownError(account_id=str(account_id))

    settings = get_settings()
    attachment_repo = AttachmentRepository(
        session,
        claims.household_id,
        master_key=settings.master_key,
        attachment_root=settings.attachment_root,
    )

    # Idempotency: same hash + same account = same batch.
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    existing_attachment = attachment_repo.find_by_hash(content_hash)
    batch_repo = ImportBatchRepository(session, claims.household_id)
    if existing_attachment is not None:
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
    else:  # qif
        try:
            parsed_lines = qif_parse(raw_bytes, currency=account.currency)
        except QifParseError as exc:
            raise ImportQifParseFailedError(reason=str(exc)) from exc

    # Persist: attachment → batch → lines.
    default_filename = "upload.ofx" if source_format == "ofx" else "upload.qif"
    default_ct = "application/x-ofx" if source_format == "ofx" else "application/qif"
    if existing_attachment is None:
        attachment = attachment_repo.create(
            filename=file.filename or default_filename,
            content_type=file.content_type or default_ct,
            raw_bytes=raw_bytes,
            uploaded_by_user_id=claims.user_id,
        )
    else:
        attachment = existing_attachment

    storage_format = SourceFormat.OFX if source_format == "ofx" else SourceFormat.QIF
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

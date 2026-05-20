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
import json
from datetime import datetime
from typing import TYPE_CHECKING, Final
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status

from tulip_api.auth.deps import get_current_claims, require_role
from tulip_api.config import get_settings
from tulip_api.deps import get_session
from tulip_api.errors import (
    AccountUnknownError,
    CsvProfileNotFoundError,
    ForbiddenError,
    ImportAccountMapInvalidError,
    ImportAlreadyAppliedError,
    ImportBatchHasPromotedLinesError,
    ImportBatchNotFoundError,
    ImportCategorizeUnknownAccountError,
    ImportCsvParseFailedError,
    ImportCsvProfileMissingError,
    ImportDuplicateFileError,
    ImportMultiAccountQifError,
    ImportOfxParseFailedError,
    ImportQifAccountNotFoundError,
    ImportQifAccountUnmappedError,
    ImportQifParseFailedError,
    ImportUnsupportedFormatError,
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
    MultiAccountImportSummary,
    StatementLinePromoteResponse,
    StatementLineRead,
    StatementLineUpdate,
)
from tulip_api.services.import_apply import (
    BatchAlreadyAppliedError,
    CategorizeUnknownAccountError,
    LineAlreadyPromotedError,
    LineExcludedError,
    apply_batch,
    promote_statement_line,
    serialize_parsed_line_raw_json,
)
from tulip_api.services.qif_multi_account import pair_transfers
from tulip_core.reconciliation.categorizer import get_categorizer
from tulip_core.transactions import Posting as DomainPosting
from tulip_core.transactions import Transaction as DomainTransaction
from tulip_core.transactions import TransactionStatus as DomainTxStatus
from tulip_importers.csv import CsvParseError, CsvProfile
from tulip_importers.csv import parse as csv_parse
from tulip_importers.ofx import OfxParseError
from tulip_importers.ofx import parse as ofx_parse
from tulip_importers.qif import QifParseError
from tulip_importers.qif import parse as qif_parse
from tulip_importers.qif import split_accounts as qif_split_accounts
from tulip_storage.models import ImportBatchStatus, SourceFormat
from tulip_storage.repositories import (
    AccountRepository,
    AttachmentRepository,
    AuditLogWriter,
    CsvProfileRepository,
    ImportBatchRepository,
    StatementLineRepository,
    TransactionRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tulip_api.auth.tokens import Claims
    from tulip_core.reconciliation import ParsedStatementLine
    from tulip_storage.models import Account, ImportBatch, StatementLine


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
            "import.multi_account_qif",
            "import.qif_account_not_found",
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
    qif_account: str | None = Form(
        None,
        description=(
            "Name of the !Account block to ingest from a multi-account QIF "
            "(#195). Only meaningful for source_format='qif'. When set, just "
            "that account's transactions are parsed and landed against "
            "account_id; the CLI sends one request per --account-map entry. "
            "When unset and the QIF declares 2+ accounts, the request is "
            "rejected with import.multi_account_qif rather than silently "
            "merging every account into one."
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

    # Security audit M-17 (#336): stream-and-bail rather than slurp.
    from tulip_api.upload_limits import read_upload_file_capped

    raw_bytes = await read_upload_file_capped(file, max_bytes=MAX_OFX_BYTES)
    if not raw_bytes:
        if source_format == "ofx":
            raise ImportOfxParseFailedError(reason="uploaded file is empty")
        if source_format == "qif":
            raise ImportQifParseFailedError(reason="uploaded file is empty")
        raise ImportCsvParseFailedError(reason="uploaded file is empty")

    # Multi-account QIF (#195). The bytes the QIF parser sees — ``qif_payload``
    # — is the whole file for a normal single-account import, or just one
    # !Account block's chunk when the caller selects ``qif_account``. The
    # attachment + dedup hash always cover the original ``raw_bytes`` so the
    # stored file is the real upload, and per-account dedup stays per the
    # (account_id, attachment) pair.
    qif_payload = raw_bytes
    if source_format == "qif":
        chunks = qif_split_accounts(raw_bytes)
        if qif_account is not None:
            chunk = next((c for c in chunks if c.account_name == qif_account), None)
            if chunk is None:
                raise ImportQifAccountNotFoundError(
                    qif_account=qif_account,
                    available=[c.account_name for c in chunks],
                )
            qif_payload = chunk.qif_text.encode("utf-8")
        else:
            account_names = sorted({c.account_name for c in chunks})
            if len(account_names) >= 2:
                raise ImportMultiAccountQifError(account_names=account_names)

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
    batch_repo = ImportBatchRepository(session, claims.household_id, master_key=settings.master_key)
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
            parsed_lines = qif_parse(qif_payload, currency=account.currency)
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
                "raw_json": serialize_parsed_line_raw_json(parsed),
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


def _parse_account_map(account_map: str) -> dict[str, UUID]:
    """Parse + validate the ``account_map`` form field (#195b).

    Expects a JSON object mapping each QIF !Account name to a tulip
    account UUID string — the CLI resolves codes/names to UUIDs before
    sending, so the server only has to validate the shape.
    """
    try:
        raw_map = json.loads(account_map)
    except json.JSONDecodeError as exc:
        raise ImportAccountMapInvalidError(reason=str(exc)) from exc
    if not isinstance(raw_map, dict) or not raw_map:
        raise ImportAccountMapInvalidError(reason="expected a non-empty JSON object")
    name_to_uuid: dict[str, UUID] = {}
    for qif_name, value in raw_map.items():
        try:
            name_to_uuid[str(qif_name)] = UUID(str(value))
        except ValueError as exc:
            raise ImportAccountMapInvalidError(
                reason=f"{qif_name!r} maps to {value!r}, which is not a UUID"
            ) from exc
    return name_to_uuid


@router.post(
    "/multi-account",
    response_model=MultiAccountImportSummary,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: problem_response(
            "account.unknown",
            "import.qif_parse_failed",
            "import.qif_account_unmapped",
            "import.account_map_invalid",
        ),
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        409: problem_response("import.duplicate_file"),
        413: problem_response("request.payload_too_large"),
    },
)
async def upload_multi_account_qif(
    request: Request,
    file: UploadFile = File(  # noqa: B008
        ..., description="A multi-account QIF file (2+ !Account blocks)."
    ),
    account_map: str = Form(
        ...,
        description=(
            "JSON object mapping each QIF !Account name to a tulip account "
            "UUID. The CLI resolves codes / names / paths to UUIDs before "
            "sending, so the server only validates the shape."
        ),
    ),
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> MultiAccountImportSummary:
    """Ingest a multi-account QIF — one batch per account, transfers paired (#195b).

    The file is split by !Account block; each account's transactions land
    in their own import batch. Cross-account transfers (reciprocal
    ``L[Account]`` legs) are landed directly as one balanced PENDING
    transaction, with both source statement lines marked promoted to it.
    Transfer legs that can't be paired fall back to ordinary one-sided
    statement lines and are reported in ``warnings``.
    """
    # Security audit M-17 (#336): stream-and-bail rather than slurp.
    from tulip_api.upload_limits import read_upload_file_capped

    raw_bytes = await read_upload_file_capped(file, max_bytes=MAX_OFX_BYTES)
    if not raw_bytes:
        raise ImportQifParseFailedError(reason="uploaded file is empty")

    name_to_uuid = _parse_account_map(account_map)

    chunks = qif_split_accounts(raw_bytes)
    if not chunks:
        raise ImportQifParseFailedError(
            reason=(
                "this QIF has no !Account blocks — use POST /v1/imports with "
                "account_id for a single-account file"
            )
        )

    # Every account the file declares must be covered by the map.
    unmapped = sorted({c.account_name for c in chunks if c.account_name not in name_to_uuid})
    if unmapped:
        raise ImportQifAccountUnmappedError(unmapped=unmapped)

    # Resolve + validate every mapped account up front.
    accounts_repo = AccountRepository(session, claims.household_id)
    account_by_name: dict[str, Account] = {}
    for chunk in chunks:
        acct = accounts_repo.get(name_to_uuid[chunk.account_name])
        if acct is None:
            raise AccountUnknownError(account_id=str(name_to_uuid[chunk.account_name]))
        account_by_name[chunk.account_name] = acct

    # Parse each chunk against its account's currency.
    parsed_by_account: dict[str, list[ParsedStatementLine]] = {}
    for chunk in chunks:
        try:
            parsed_by_account[chunk.account_name] = qif_parse(
                chunk.qif_text.encode("utf-8"),
                currency=account_by_name[chunk.account_name].currency,
            )
        except QifParseError as exc:
            raise ImportQifParseFailedError(
                reason=f"account {chunk.account_name!r}: {exc}"
            ) from exc

    # Match reciprocal cross-account transfer legs.
    pairs, warnings = pair_transfers(parsed_by_account, name_to_uuid)

    # Attachment + per-account dedup. The attachment covers the whole file;
    # re-importing it is a duplicate for every account it touches.
    settings = get_settings()
    attachment_repo = AttachmentRepository(
        session,
        claims.household_id,
        master_key=settings.master_key,
        attachment_root=settings.attachment_root,
    )
    batch_repo = ImportBatchRepository(session, claims.household_id, master_key=settings.master_key)
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    existing_attachment = attachment_repo.find_by_hash(content_hash)
    if existing_attachment is not None:
        for chunk in chunks:
            dup = batch_repo.find_for_attachment(
                account_id=name_to_uuid[chunk.account_name],
                attachment_id=existing_attachment.id,
            )
            if dup is not None:
                raise ImportDuplicateFileError(
                    content_hash=content_hash, existing_batch_id=str(dup.id)
                )
        attachment = existing_attachment
    else:
        attachment = attachment_repo.create(
            filename=file.filename or "upload.qif",
            content_type=file.content_type or "application/qif",
            raw_bytes=raw_bytes,
            uploaded_by_user_id=claims.user_id,
        )

    # One batch per account; bulk-insert every parsed line (transfer legs
    # included — they're marked promoted below, not omitted).
    line_repo = StatementLineRepository(session, claims.household_id)
    batches: dict[str, ImportBatch] = {}
    line_index: dict[tuple[str, int], StatementLine] = {}
    for chunk in chunks:
        parsed = parsed_by_account[chunk.account_name]
        batch = batch_repo.create(
            account_id=name_to_uuid[chunk.account_name],
            source_format=SourceFormat.QIF,
            source_filename=file.filename or "upload.qif",
            source_file_attachment_id=attachment.id,
            created_by_user_id=claims.user_id,
            summary_json={"line_count": len(parsed)},
        )
        batches[chunk.account_name] = batch
        inserted = line_repo.bulk_insert(
            batch.id,
            [
                {
                    "line_number": p.line_number,
                    "posted_date": p.posted_date,
                    "amount": p.amount.amount,
                    "currency": p.amount.currency,
                    "description": p.description,
                    "counterparty": p.counterparty,
                    "reference": p.reference,
                    "fitid": p.fitid,
                    "raw_json": serialize_parsed_line_raw_json(p),
                }
                for p in parsed
            ],
        )
        for sl in inserted:
            line_index[(chunk.account_name, sl.line_number)] = sl
        batch.imported_count = len(parsed)
        AuditLogWriter(session, claims.household_id).write(
            action="import_create",
            actor_kind="user",
            actor_user_id=claims.user_id,
            entity_type="import_batch",
            entity_id=batch.id,
            after={
                "account_id": str(name_to_uuid[chunk.account_name]),
                "source_format": "qif",
                "source_filename": file.filename or "upload.qif",
                "line_count": len(parsed),
                "qif_account": chunk.account_name,
            },
            request_id=_request_uuid(request),
        )

    # Land each matched transfer pair as one balanced PENDING transaction;
    # mark both source statement lines promoted to it so a later `apply`
    # skips them.
    tx_repo = TransactionRepository(session, claims.household_id)
    for pair in pairs:
        from_line = pair.from_line
        to_line = pair.to_line
        domain_tx = DomainTransaction(
            id=uuid4(),
            household_id=claims.household_id,
            date=from_line.posted_date,
            description=from_line.description,
            postings=(
                DomainPosting(
                    id=uuid4(),
                    account_id=name_to_uuid[pair.from_account],
                    amount=from_line.amount,
                ),
                DomainPosting(
                    id=uuid4(),
                    account_id=name_to_uuid[pair.to_account],
                    amount=to_line.amount,
                ),
            ),
            status=DomainTxStatus.PENDING,
            created_by_user_id=claims.user_id,
        )
        tx = tx_repo.save_balanced(domain_tx, imported_from_id=batches[pair.from_account].id)
        from_sl = line_index[(pair.from_account, from_line.line_number)]
        to_sl = line_index[(pair.to_account, to_line.line_number)]
        line_repo.mark_promoted(from_sl.id, tx.id)
        line_repo.mark_promoted(to_sl.id, tx.id)

    session.commit()
    log.info(
        "import.multi_account.created",
        batch_count=len(chunks),
        transfer_count=len(pairs),
        warning_count=len(warnings),
    )

    return MultiAccountImportSummary(
        batches=[
            ImportBatchSummary(
                id=batches[chunk.account_name].id,
                account_id=name_to_uuid[chunk.account_name],
                source_format="qif",
                source_filename=batches[chunk.account_name].source_filename,
                status=batches[chunk.account_name].status.value,
                statement_line_count=batches[chunk.account_name].imported_count,
                imported_count=batches[chunk.account_name].imported_count,
                skipped_count=batches[chunk.account_name].skipped_count,
                error_count=batches[chunk.account_name].error_count,
                created_at=batches[chunk.account_name].created_at,
            )
            for chunk in chunks
        ],
        transfer_count=len(pairs),
        warnings=warnings,
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
    as_posted: bool = Query(
        default=False,
        description=(
            "Issue #210: if true, each promoted line lands as POSTED "
            "(committed-but-unreconciled) instead of the default PENDING "
            "(review queue). Useful for migration workflows from other "
            "accounting tools where every imported line is already "
            "cleared by the bank. The double-entry balance invariant "
            "still holds — bank-side + categorizer-side postings sum "
            "to zero per currency."
        ),
    ),
    treat_cleared_as_pending: bool = Query(
        default=False,
        description=(
            "Issue #279: when true, force every line to PENDING even if "
            "the source format (e.g. QIF ``C`` field) marked it cleared "
            "or reconciled. Legacy 'everything pending' behaviour for "
            "users who want the manual review pass on imported lines."
        ),
    ),
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> ImportBatchApplyResponse:
    """Promote every non-excluded line in the batch to a ledger transaction.

    Per ADR-0004 §Q4. The new transactions are PENDING by default; pass
    ``?as_posted=true`` (issue #210) to land them as POSTED for direct
    migration workflows. Pass ``?treat_cleared_as_pending=true`` (#279)
    to ignore the QIF ``C`` field's cleared / reconciled hint.

    Idempotent at the batch level: re-applying an already-applied batch
    returns ``import.already_applied`` (409). To promote a specific line
    individually, see ``POST /v1/imports/{batch_id}/lines/{line_id}/promote``.
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
            as_posted=as_posted,
            treat_cleared_as_pending=treat_cleared_as_pending,
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
            "no_categorize": no_categorize,
            "as_posted": as_posted,
            "treat_cleared_as_pending": treat_cleared_as_pending,
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


@router.patch(
    "/{batch_id}/lines/{line_id}",
    response_model=StatementLineRead,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("import_batch.not_found", "import.line.not_found"),
        409: problem_response("import.line.already_promoted"),
    },
)
def update_statement_line(
    batch_id: UUID,
    line_id: UUID,
    body: StatementLineUpdate,
    request: Request,
    claims: Claims = Depends(require_role("admin", "member")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> StatementLineRead:
    """Toggle ``is_excluded`` on a single parsed statement line (P9.6.a).

    Idempotent — passing the line's current ``is_excluded`` is a no-op
    (no audit row, returns the line unchanged). Refuses to mutate an
    already-promoted line: that line has a ledger transaction and
    excluding it would orphan the back-reference; edit / void the
    transaction instead.
    """
    batch_repo = ImportBatchRepository(session, claims.household_id)
    batch = batch_repo.get(batch_id)
    if batch is None:
        raise ImportBatchNotFoundError()

    line_repo = StatementLineRepository(session, claims.household_id)
    line = line_repo.get(line_id)
    if line is None or line.import_batch_id != batch_id:
        raise StatementLineNotFoundError()

    if line.promoted_transaction_id is not None:
        raise StatementLineAlreadyPromotedError(
            line_id=str(line_id),
            transaction_id=str(line.promoted_transaction_id),
        )

    if bool(line.is_excluded) == body.is_excluded:
        # Idempotent no-op — return current state, no audit row.
        return StatementLineRead(
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
            promoted_transaction_id=line.promoted_transaction_id,
        )

    before = bool(line.is_excluded)
    if body.is_excluded:
        line = line_repo.exclude(line_id)
        action = "statement_line.excluded"
    else:
        line = line_repo.unexclude(line_id)
        action = "statement_line.unexcluded"

    AuditLogWriter(session, claims.household_id).write(
        action=action,
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="statement_line",
        entity_id=line.id,
        before={"is_excluded": before},
        after={"is_excluded": bool(line.is_excluded)},
        request_id=_request_uuid(request),
    )
    session.commit()
    log.info(
        "statement_line.is_excluded_updated",
        line_id=str(line_id),
        is_excluded=bool(line.is_excluded),
    )
    return StatementLineRead(
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
        promoted_transaction_id=line.promoted_transaction_id,
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
                promoted_transaction_id=line.promoted_transaction_id,
            )
            for line in lines
        ],
    )


@router.delete(
    "/{batch_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: problem_response("auth.unauthorized"),
        403: problem_response("auth.forbidden"),
        404: problem_response("import_batch.not_found"),
        409: problem_response("import.batch_has_promoted_lines"),
    },
)
def delete_import_batch(
    batch_id: UUID,
    request: Request,
    claims: Claims = Depends(require_role("admin")),  # noqa: B008
    session: Session = Depends(get_session),  # noqa: B008
) -> None:
    """Delete an import batch and its statement lines (#345).

    Refuses with 409 ``import.batch_has_promoted_lines`` if any of the
    batch's ``statement_lines`` have been promoted to ledger
    transactions (``promoted_transaction_id IS NOT NULL``) — the caller
    must void / delete those transactions first so the back-reference
    nulls itself out (see #301 for the back-reference plumbing).

    Otherwise cascades ``statement_lines`` via the existing FK
    ``ondelete="CASCADE"``. The ``attachments`` row stays — the
    attachment is content-addressed and may be referenced by other
    batches; the household-erasure path (#235) handles attachment
    cleanup on its broader unlink-orphans sweep.

    Admin-only per the issue (#345) — bulk delete is operator-grade.

    Audit row: ``import_batch.deleted`` with the batch's source format,
    line count, and promotion count snapshot. No PII from the lines
    themselves; just structural metadata.
    """
    batch_repo = ImportBatchRepository(session, claims.household_id)
    batch = batch_repo.get(batch_id)
    if batch is None:
        raise ImportBatchNotFoundError()

    lines_repo = StatementLineRepository(session, claims.household_id)
    lines = lines_repo.list_for_batch(batch_id)
    promoted = [line for line in lines if line.promoted_transaction_id is not None]
    if promoted:
        raise ImportBatchHasPromotedLinesError(batch_id=str(batch_id), promoted_count=len(promoted))

    AuditLogWriter(session, claims.household_id).write(
        action="import_batch.deleted",
        actor_kind="user",
        actor_user_id=claims.user_id,
        entity_type="import_batch",
        entity_id=batch.id,
        before={
            "source_format": batch.source_format.value,
            "source_filename": batch.source_filename,
            "line_count": len(lines),
            "promoted_count": 0,  # validated above
            "status": batch.status.value,
        },
        request_id=_request_uuid(request),
    )

    batch_repo.delete(batch_id)
    session.commit()
    log.info("import_batch.deleted", batch_id=str(batch_id), line_count=len(lines))

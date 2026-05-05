"""End-to-end test: OFX bytes → storage chokepoints → re-query (P5.2.a).

Proves that ``tulip_importers.ofx.parse`` composes with the P5.1
chokepoints (`AttachmentRepository`, `ImportBatchRepository`,
`StatementLineRepository`) to land an importable batch in the DB.
The test runs without the API or CLI layers — pure storage-level
integration.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from tulip_importers.ofx import parse
from tulip_storage.models import (
    Account,
    AccountType,
    Household,
    SourceFormat,
)
from tulip_storage.repositories import (
    AttachmentRepository,
    ImportBatchRepository,
    StatementLineRepository,
)

# Fixtures live with the importer; tests aren't part of the wheel so the
# cross-package read works.
_OFX_FIXTURES = (
    Path(__file__).resolve().parents[2] / "tulip-importers" / "tests" / "fixtures" / "ofx"
)


def _seed_household_and_account(session: Session) -> tuple[Household, Account]:
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    a = Account(
        household_id=h.id,
        id=uuid4(),
        code="1110",
        name="Checking",
        type=AccountType.ASSET,
        currency="USD",
        visibility="shared",
    )
    session.add(a)
    session.commit()
    return h, a


def _to_row(parsed) -> dict:
    return {
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


def test_ofx_bytes_roundtrip_through_storage_chokepoints(session: Session, tmp_path: Path):
    h, account = _seed_household_and_account(session)
    raw_bytes = (_OFX_FIXTURES / "minimal_ofx2.ofx").read_bytes()

    # Parse → ParsedStatementLine list (no persistence ids yet).
    parsed_lines = parse(raw_bytes)
    assert len(parsed_lines) == 2

    # Attachment: encrypted bytes on disk + metadata row.
    attachment_root = tmp_path / "attachments"
    att = AttachmentRepository(
        session,
        h.id,
        master_key=b"\x00" * 32,
        attachment_root=attachment_root,
    ).create(
        filename="may.ofx",
        content_type="application/x-ofx",
        raw_bytes=raw_bytes,
    )
    session.commit()

    # ImportBatch: links to attachment + account.
    batch = ImportBatchRepository(session, h.id).create(
        account_id=account.id,
        source_format=SourceFormat.OFX,
        source_filename="may.ofx",
        source_file_attachment_id=att.id,
    )
    session.commit()

    # StatementLines: bulk insert from the parsed lines.
    line_repo = StatementLineRepository(session, h.id)
    line_repo.bulk_insert(batch.id, [_to_row(p) for p in parsed_lines])
    session.commit()

    # Re-query and verify shape preserved.
    persisted = line_repo.list_for_batch(batch.id)
    assert len(persisted) == 2
    assert {row.line_number for row in persisted} == {1, 2}
    fitids = {row.fitid for row in persisted}
    assert fitids == {"FITID-AMAZON-001", "FITID-PAYCHECK-001"}

    # Attachment bytes round-trip through encrypt → fs → decrypt.
    repo = AttachmentRepository(
        session,
        h.id,
        master_key=b"\x00" * 32,
        attachment_root=attachment_root,
    )
    plaintext = repo.read_bytes(att.id)
    assert plaintext == raw_bytes

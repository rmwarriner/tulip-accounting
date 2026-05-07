"""Tests for the 7 P5.1 repositories."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tulip_storage.models import (
    Account,
    AccountType,
    Household,
    Posting,
    SourceFormat,
    Transaction,
    TransactionStatus,
)
from tulip_storage.repositories import (
    AccountRepository,
    AttachmentLinkRepository,
    AttachmentRepository,
    CsvProfileRepository,
    ImportBatchRepository,
    ReconciliationMatchRepository,
    ReconciliationRepository,
    StatementLineRepository,
    TransactionRepository,
)


@pytest.fixture
def household(session: Session) -> Household:
    h = Household(id=uuid4(), name="Smith", base_currency="USD")
    session.add(h)
    session.commit()
    return h


@pytest.fixture
def account(session: Session, household: Household) -> Account:
    a = AccountRepository(session, household.id).create(
        code="1110",
        name="Checking",
        type=AccountType.ASSET,
        currency="USD",
    )
    session.commit()
    return a


@pytest.fixture
def attachment_root(tmp_path: Path) -> Path:
    return tmp_path / "attachments"


@pytest.fixture
def master_key() -> bytes:
    return b"\x00" * 32  # deterministic test key


# ---- Attachment ----------------------------------------------------------


class TestAttachmentRepository:
    def test_create_writes_encrypted_blob_and_metadata_in_clear(
        self,
        session: Session,
        household: Household,
        attachment_root: Path,
        master_key: bytes,
    ):
        repo = AttachmentRepository(
            session,
            household.id,
            master_key=master_key,
            attachment_root=attachment_root,
        )
        att = repo.create(
            filename="may.ofx",
            content_type="application/x-ofx",
            raw_bytes=b"<OFX>...payload...</OFX>",
        )
        session.commit()

        # Metadata in clear.
        assert att.filename == "may.ofx"
        assert att.content_type == "application/x-ofx"
        assert att.size_bytes == len(b"<OFX>...payload...</OFX>")

        # Bytes on disk are encrypted (not the plaintext).
        on_disk = (attachment_root / att.content_hash).read_bytes()
        assert on_disk != b"<OFX>...payload...</OFX>"

        # Round-trip: read_bytes decrypts back to the original.
        plain = repo.read_bytes(att.id)
        assert plain == b"<OFX>...payload...</OFX>"

    def test_dedup_via_find_by_hash(
        self,
        session: Session,
        household: Household,
        attachment_root: Path,
        master_key: bytes,
    ):
        repo = AttachmentRepository(
            session,
            household.id,
            master_key=master_key,
            attachment_root=attachment_root,
        )
        repo.create(filename="a.ofx", content_type="x", raw_bytes=b"same")
        session.commit()
        found = repo.find_by_hash(__import__("hashlib").sha256(b"same").hexdigest())
        assert found is not None
        assert found.filename == "a.ofx"

    def test_unique_hash_constraint_rejects_duplicate_insert(
        self,
        session: Session,
        household: Household,
        attachment_root: Path,
        master_key: bytes,
    ):
        repo = AttachmentRepository(
            session,
            household.id,
            master_key=master_key,
            attachment_root=attachment_root,
        )
        repo.create(filename="a.ofx", content_type="x", raw_bytes=b"same")
        session.commit()
        # Insert again with same bytes → unique index rejects on flush.
        with pytest.raises(IntegrityError):
            repo.create(filename="b.ofx", content_type="x", raw_bytes=b"same")

    def test_get_returns_none_for_other_household(
        self,
        session: Session,
        household: Household,
        attachment_root: Path,
        master_key: bytes,
    ):
        repo = AttachmentRepository(
            session,
            household.id,
            master_key=master_key,
            attachment_root=attachment_root,
        )
        att = repo.create(filename="a", content_type="x", raw_bytes=b"x")
        session.commit()

        other = Household(id=uuid4(), name="Other", base_currency="USD")
        session.add(other)
        session.commit()
        other_repo = AttachmentRepository(
            session,
            other.id,
            master_key=master_key,
            attachment_root=attachment_root,
        )
        assert other_repo.get(att.id) is None


# ---- ImportBatch + StatementLine -----------------------------------------


class TestImportBatchAndStatementLine:
    @pytest.fixture
    def attachment(
        self,
        session: Session,
        household: Household,
        attachment_root: Path,
        master_key: bytes,
    ):
        att = AttachmentRepository(
            session,
            household.id,
            master_key=master_key,
            attachment_root=attachment_root,
        ).create(
            filename="may.ofx",
            content_type="application/x-ofx",
            raw_bytes=b"OFX",
        )
        session.commit()
        return att

    def test_create_import_batch(
        self,
        session: Session,
        household: Household,
        account: Account,
        attachment,
    ):
        repo = ImportBatchRepository(session, household.id)
        batch = repo.create(
            account_id=account.id,
            source_format=SourceFormat.OFX,
            source_filename="may.ofx",
            source_file_attachment_id=attachment.id,
        )
        session.commit()
        loaded = repo.get(batch.id)
        assert loaded is not None
        assert loaded.account_id == account.id
        assert loaded.source_format is SourceFormat.OFX
        assert loaded.status.value == "parsed"

    def test_idempotency_dup_attachment_per_account_rejected(
        self,
        session: Session,
        household: Household,
        account: Account,
        attachment,
    ):
        repo = ImportBatchRepository(session, household.id)
        repo.create(
            account_id=account.id,
            source_format=SourceFormat.OFX,
            source_filename="may.ofx",
            source_file_attachment_id=attachment.id,
        )
        session.commit()
        with pytest.raises(IntegrityError):
            repo.create(
                account_id=account.id,
                source_format=SourceFormat.OFX,
                source_filename="may.ofx",
                source_file_attachment_id=attachment.id,
            )

    def test_mark_applied_and_reverted(
        self,
        session: Session,
        household: Household,
        account: Account,
        attachment,
    ):
        repo = ImportBatchRepository(session, household.id)
        batch = repo.create(
            account_id=account.id,
            source_format=SourceFormat.OFX,
            source_filename="may.ofx",
            source_file_attachment_id=attachment.id,
        )
        session.commit()

        repo.mark_applied(batch.id)
        session.commit()
        assert repo.get(batch.id).status.value == "applied"
        assert repo.get(batch.id).applied_at is not None

        repo.mark_reverted(batch.id)
        session.commit()
        assert repo.get(batch.id).status.value == "reverted"
        assert repo.get(batch.id).reverted_at is not None

    def test_statement_line_bulk_insert_and_unmatched(
        self,
        session: Session,
        household: Household,
        account: Account,
        attachment,
    ):
        batch = ImportBatchRepository(session, household.id).create(
            account_id=account.id,
            source_format=SourceFormat.CSV,
            source_filename="may.csv",
            source_file_attachment_id=attachment.id,
        )
        session.commit()

        line_repo = StatementLineRepository(session, household.id)
        line_repo.bulk_insert(
            batch.id,
            [
                {
                    "line_number": 1,
                    "posted_date": date(2026, 5, 12),
                    "amount": Decimal("-42.17"),
                    "currency": "USD",
                    "description": "AMAZON",
                    "raw_json": "{}",
                },
                {
                    "line_number": 2,
                    "posted_date": date(2026, 5, 13),
                    "amount": Decimal("-12.50"),
                    "currency": "USD",
                    "description": "LUNCH",
                    "raw_json": "{}",
                },
            ],
        )
        session.commit()

        unmatched = line_repo.list_unmatched(batch.id)
        assert len(unmatched) == 2

        # Excluding one drops it from unmatched.
        line_repo.exclude(unmatched[0].id)
        session.commit()
        assert len(line_repo.list_unmatched(batch.id)) == 1


# ---- Reconciliation + Match ---------------------------------------------


def _seed_posted_tx(session: Session, household_id, account: Account) -> Transaction:
    food = AccountRepository(session, household_id).create(
        code="5100",
        name="Food",
        type=AccountType.EXPENSE,
        currency="USD",
    )
    session.commit()
    tx = Transaction(
        household_id=household_id,
        id=uuid4(),
        date=date(2026, 5, 12),
        description="Lunch",
        status=TransactionStatus.PENDING,
    )
    session.add(tx)
    session.flush()
    session.add_all(
        [
            Posting(
                id=uuid4(),
                household_id=household_id,
                transaction_id=tx.id,
                account_id=food.id,
                amount=Decimal("12.50"),
                currency="USD",
            ),
            Posting(
                id=uuid4(),
                household_id=household_id,
                transaction_id=tx.id,
                account_id=account.id,
                amount=Decimal("-12.50"),
                currency="USD",
            ),
        ]
    )
    session.flush()
    tx.status = TransactionStatus.POSTED
    session.commit()
    return tx


class TestReconciliationAndMatch:
    @pytest.fixture
    def attachment_for_recon(
        self,
        session: Session,
        household: Household,
        attachment_root: Path,
        master_key: bytes,
    ):
        att = AttachmentRepository(
            session,
            household.id,
            master_key=master_key,
            attachment_root=attachment_root,
        ).create(filename="may.ofx", content_type="x", raw_bytes=b"x")
        session.commit()
        return att

    def test_create_reconciliation(self, session: Session, household: Household, account: Account):
        repo = ReconciliationRepository(session, household.id)
        recon = repo.create(
            account_id=account.id,
            statement_period_start=date(2026, 5, 1),
            statement_period_end=date(2026, 5, 31),
            statement_starting_balance=Decimal("0"),
            statement_ending_balance=Decimal("-12.50"),
            currency="USD",
        )
        session.commit()
        loaded = repo.get(recon.id)
        assert loaded is not None
        assert loaded.status.value == "in_progress"

    def test_create_rejects_inverted_period(
        self, session: Session, household: Household, account: Account
    ):
        repo = ReconciliationRepository(session, household.id)
        with pytest.raises(ValueError, match="period_start"):
            repo.create(
                account_id=account.id,
                statement_period_start=date(2026, 5, 31),
                statement_period_end=date(2026, 5, 1),
                statement_starting_balance=Decimal("0"),
                statement_ending_balance=Decimal("0"),
                currency="USD",
            )

    def test_complete_denormalises_reconciled_at_onto_matched_tx(
        self,
        session: Session,
        household: Household,
        account: Account,
        attachment_for_recon,
    ):
        # Build the chain: attachment → batch → line → recon → match → tx.
        batch = ImportBatchRepository(session, household.id).create(
            account_id=account.id,
            source_format=SourceFormat.OFX,
            source_filename="may.ofx",
            source_file_attachment_id=attachment_for_recon.id,
        )
        session.commit()
        lines = StatementLineRepository(session, household.id).bulk_insert(
            batch.id,
            [
                {
                    "line_number": 1,
                    "posted_date": date(2026, 5, 12),
                    "amount": Decimal("-12.50"),
                    "currency": "USD",
                    "description": "LUNCH",
                    "raw_json": "{}",
                }
            ],
        )
        session.commit()
        tx = _seed_posted_tx(session, household.id, account)
        recon_repo = ReconciliationRepository(session, household.id)
        recon = recon_repo.create(
            account_id=account.id,
            statement_period_start=date(2026, 5, 1),
            statement_period_end=date(2026, 5, 31),
            statement_starting_balance=Decimal("0"),
            statement_ending_balance=Decimal("-12.50"),
            currency="USD",
        )
        session.commit()
        match_repo = ReconciliationMatchRepository(session, household.id)
        match_repo.create(
            reconciliation_id=recon.id,
            statement_line_id=lines[0].id,
            ledger_transaction_id=tx.id,
            match_amount=Decimal("12.50"),
            currency="USD",
        )
        session.commit()

        # Before complete: tx.reconciled_at is NULL.
        loaded = TransactionRepository(session, household.id).get(tx.id)
        assert loaded.reconciled_at is None
        assert loaded.reconciliation_id is None

        recon_repo.complete(recon.id)
        session.commit()

        # After complete: tx denormalised.
        loaded = TransactionRepository(session, household.id).get(tx.id)
        assert loaded.reconciled_at is not None
        assert loaded.reconciliation_id == recon.id
        # Statement line carries the match pointer.
        line = StatementLineRepository(session, household.id).get(lines[0].id)
        assert line.reconciliation_match_id is not None

    def test_match_rejection_clears_line_pointer(
        self,
        session: Session,
        household: Household,
        account: Account,
        attachment_for_recon,
    ):
        batch = ImportBatchRepository(session, household.id).create(
            account_id=account.id,
            source_format=SourceFormat.CSV,
            source_filename="may.csv",
            source_file_attachment_id=attachment_for_recon.id,
        )
        session.commit()
        lines = StatementLineRepository(session, household.id).bulk_insert(
            batch.id,
            [
                {
                    "line_number": 1,
                    "posted_date": date(2026, 5, 12),
                    "amount": Decimal("-12.50"),
                    "currency": "USD",
                    "description": "LUNCH",
                    "raw_json": "{}",
                }
            ],
        )
        session.commit()
        tx = _seed_posted_tx(session, household.id, account)
        recon = ReconciliationRepository(session, household.id).create(
            account_id=account.id,
            statement_period_start=date(2026, 5, 1),
            statement_period_end=date(2026, 5, 31),
            statement_starting_balance=Decimal("0"),
            statement_ending_balance=Decimal("-12.50"),
            currency="USD",
        )
        session.commit()
        match_repo = ReconciliationMatchRepository(session, household.id)
        match = match_repo.create(
            reconciliation_id=recon.id,
            statement_line_id=lines[0].id,
            ledger_transaction_id=tx.id,
            match_amount=Decimal("12.50"),
            currency="USD",
        )
        session.commit()

        match_repo.reject(match.id)
        session.commit()

        line = StatementLineRepository(session, household.id).get(lines[0].id)
        assert line.reconciliation_match_id is None

    def test_revert_nulls_tx_denorms_and_clears_line_pointers(
        self,
        session: Session,
        household: Household,
        account: Account,
        attachment_for_recon,
    ):
        """revert() un-reconciles: nulls tx denorms, clears line pointers, deletes recon."""
        batch = ImportBatchRepository(session, household.id).create(
            account_id=account.id,
            source_format=SourceFormat.OFX,
            source_filename="may.ofx",
            source_file_attachment_id=attachment_for_recon.id,
        )
        session.commit()
        lines = StatementLineRepository(session, household.id).bulk_insert(
            batch.id,
            [
                {
                    "line_number": 1,
                    "posted_date": date(2026, 5, 12),
                    "amount": Decimal("-12.50"),
                    "currency": "USD",
                    "description": "LUNCH",
                    "raw_json": "{}",
                }
            ],
        )
        session.commit()
        tx = _seed_posted_tx(session, household.id, account)
        recon_repo = ReconciliationRepository(session, household.id)
        recon = recon_repo.create(
            account_id=account.id,
            statement_period_start=date(2026, 5, 1),
            statement_period_end=date(2026, 5, 31),
            statement_starting_balance=Decimal("0"),
            statement_ending_balance=Decimal("-12.50"),
            currency="USD",
        )
        ReconciliationMatchRepository(session, household.id).create(
            reconciliation_id=recon.id,
            statement_line_id=lines[0].id,
            ledger_transaction_id=tx.id,
            match_amount=Decimal("12.50"),
            currency="USD",
        )
        recon_repo.complete(recon.id)
        session.commit()

        # Sanity: tx is reconciled.
        loaded = TransactionRepository(session, household.id).get(tx.id)
        assert loaded.reconciliation_id == recon.id
        assert loaded.reconciled_at is not None

        # Revert.
        recon_repo.revert(recon.id)
        session.commit()

        # Tx denorms cleared.
        loaded = TransactionRepository(session, household.id).get(tx.id)
        assert loaded.reconciliation_id is None
        assert loaded.reconciled_at is None

        # Line pointer cleared (cascade-deleted match left a dangling pointer
        # otherwise — revert nulls before delete).
        line = StatementLineRepository(session, household.id).get(lines[0].id)
        assert line.reconciliation_match_id is None

        # Reconciliation row gone.
        assert recon_repo.get(recon.id) is None

    def test_revert_missing_id_raises(self, session: Session, household: Household):
        from uuid import uuid4

        with pytest.raises(LookupError):
            ReconciliationRepository(session, household.id).revert(uuid4())

    def test_set_carry_forward_links_tx_to_recon(
        self,
        session: Session,
        household: Household,
        account: Account,
    ):
        """set_carry_forward(tx_id, recon_id) sets carried_forward_from_reconciliation_id."""
        tx = _seed_posted_tx(session, household.id, account)
        recon = ReconciliationRepository(session, household.id).create(
            account_id=account.id,
            statement_period_start=date(2026, 5, 1),
            statement_period_end=date(2026, 5, 31),
            statement_starting_balance=Decimal("0"),
            statement_ending_balance=Decimal("-12.50"),
            currency="USD",
        )
        session.commit()
        repo = ReconciliationRepository(session, household.id)
        repo.set_carry_forward(tx.id, recon.id)
        session.commit()
        loaded = TransactionRepository(session, household.id).get(tx.id)
        assert loaded.carried_forward_from_reconciliation_id == recon.id

    def test_clear_carry_forward_nulls_link(
        self,
        session: Session,
        household: Household,
        account: Account,
    ):
        tx = _seed_posted_tx(session, household.id, account)
        recon = ReconciliationRepository(session, household.id).create(
            account_id=account.id,
            statement_period_start=date(2026, 5, 1),
            statement_period_end=date(2026, 5, 31),
            statement_starting_balance=Decimal("0"),
            statement_ending_balance=Decimal("-12.50"),
            currency="USD",
        )
        repo = ReconciliationRepository(session, household.id)
        repo.set_carry_forward(tx.id, recon.id)
        session.commit()
        repo.clear_carry_forward(tx.id)
        session.commit()
        loaded = TransactionRepository(session, household.id).get(tx.id)
        assert loaded.carried_forward_from_reconciliation_id is None

    def test_set_carry_forward_missing_tx_raises(
        self, session: Session, household: Household, account: Account
    ):
        from uuid import uuid4

        recon = ReconciliationRepository(session, household.id).create(
            account_id=account.id,
            statement_period_start=date(2026, 5, 1),
            statement_period_end=date(2026, 5, 31),
            statement_starting_balance=Decimal("0"),
            statement_ending_balance=Decimal("0"),
            currency="USD",
        )
        with pytest.raises(LookupError):
            ReconciliationRepository(session, household.id).set_carry_forward(uuid4(), recon.id)

    def test_revert_nulls_carry_forward_links(
        self,
        session: Session,
        household: Household,
        account: Account,
    ):
        """revert() also nulls carried_forward_from_reconciliation_id pointers."""
        tx = _seed_posted_tx(session, household.id, account)
        repo = ReconciliationRepository(session, household.id)
        recon = repo.create(
            account_id=account.id,
            statement_period_start=date(2026, 5, 1),
            statement_period_end=date(2026, 5, 31),
            statement_starting_balance=Decimal("0"),
            statement_ending_balance=Decimal("-12.50"),
            currency="USD",
        )
        repo.set_carry_forward(tx.id, recon.id)
        session.commit()
        repo.revert(recon.id)
        session.commit()
        loaded = TransactionRepository(session, household.id).get(tx.id)
        assert loaded.carried_forward_from_reconciliation_id is None


# ---- CsvProfile ----------------------------------------------------------


class TestCsvProfileRepository:
    def test_create_get_list(self, session: Session, household: Household):
        repo = CsvProfileRepository(session, household.id)
        p = repo.create(name="chase-checking", yaml_body="date_column: Date\n")
        session.commit()
        assert repo.get(p.id) is not None
        assert repo.get_by_name("chase-checking") is not None
        assert len(repo.list_all()) == 1

    def test_unique_name_per_household(self, session: Session, household: Household):
        repo = CsvProfileRepository(session, household.id)
        repo.create(name="chase", yaml_body="x")
        session.commit()
        with pytest.raises(IntegrityError):
            repo.create(name="chase", yaml_body="y")

    def test_update_yaml(self, session: Session, household: Household):
        repo = CsvProfileRepository(session, household.id)
        p = repo.create(name="chase", yaml_body="old")
        session.commit()
        repo.update_yaml(p.id, "new")
        session.commit()
        assert repo.get(p.id).yaml_body == "new"


# ---- AttachmentLink ------------------------------------------------------


class TestAttachmentLinkRepository:
    def test_link_and_unlink(
        self,
        session: Session,
        household: Household,
        attachment_root: Path,
        master_key: bytes,
    ):
        att = AttachmentRepository(
            session,
            household.id,
            master_key=master_key,
            attachment_root=attachment_root,
        ).create(filename="a", content_type="x", raw_bytes=b"x")
        session.commit()

        link_repo = AttachmentLinkRepository(session, household.id)
        entity_id = uuid4()
        link_repo.link(attachment_id=att.id, entity_type="transaction", entity_id=entity_id)
        session.commit()
        assert len(link_repo.list_for_entity(entity_type="transaction", entity_id=entity_id)) == 1

        link_repo.unlink(attachment_id=att.id, entity_type="transaction", entity_id=entity_id)
        session.commit()
        assert link_repo.list_for_entity(entity_type="transaction", entity_id=entity_id) == []

    def test_link_idempotent(
        self,
        session: Session,
        household: Household,
        attachment_root: Path,
        master_key: bytes,
    ):
        att = AttachmentRepository(
            session,
            household.id,
            master_key=master_key,
            attachment_root=attachment_root,
        ).create(filename="a", content_type="x", raw_bytes=b"x")
        session.commit()

        link_repo = AttachmentLinkRepository(session, household.id)
        entity_id = uuid4()
        link_repo.link(attachment_id=att.id, entity_type="account", entity_id=entity_id)
        # Second call returns the same row, no IntegrityError.
        link_repo.link(attachment_id=att.id, entity_type="account", entity_id=entity_id)
        session.commit()
        assert len(link_repo.list_for_entity(entity_type="account", entity_id=entity_id)) == 1

    def test_check_constraint_rejects_unknown_entity_type(
        self,
        session: Session,
        household: Household,
        attachment_root: Path,
        master_key: bytes,
    ):
        att = AttachmentRepository(
            session,
            household.id,
            master_key=master_key,
            attachment_root=attachment_root,
        ).create(filename="a", content_type="x", raw_bytes=b"x")
        session.commit()

        link_repo = AttachmentLinkRepository(session, household.id)
        # 'something_random' is not in the CHECK constraint allowlist;
        # rejection fires on flush inside link().
        with pytest.raises(IntegrityError):
            link_repo.link(attachment_id=att.id, entity_type="something_random", entity_id=uuid4())

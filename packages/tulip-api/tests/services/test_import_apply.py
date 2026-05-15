"""Service-level tests for import_apply (P5.4.a).

The service module is the heart of the apply / promote flow:

- ``promote_statement_line`` turns one parsed line into a PENDING domain
  Transaction with two postings (bank-side + categorizer-side).
- ``apply_batch`` walks every non-excluded, non-already-promoted line in
  a batch and promotes it, then flips the batch to APPLIED.

Tests run at the service layer (no FastAPI) so the contract is testable
without HTTP mocking. Router-level tests in test_apply_endpoint.py /
test_promote_endpoint.py cover wiring.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from tulip_api.services.import_apply import (
    BatchAlreadyAppliedError,
    CategorizeUnknownAccountError,
    LineAlreadyPromotedError,
    LineExcludedError,
    apply_batch,
    promote_statement_line,
)
from tulip_core.reconciliation.categorizer import (
    CategorizationResult,
    NullCategorizer,
)
from tulip_storage.models import (
    AccountType,
    Household,
    ImportBatch,
    ImportBatchStatus,
    Posting,
    SourceFormat,
    StatementLine,
    TransactionStatus,
)
from tulip_storage.repositories import (
    AccountRepository,
    PeriodRepository,
)

# ---- fixtures -------------------------------------------------------------


@pytest.fixture
def setup(session_maker):
    """Seed a household + period + cash account + Imbalance:Unknown + import batch + 3 lines."""
    with session_maker() as s:
        h = Household(id=uuid4(), name="Smith", base_currency="USD")
        s.add(h)
        s.flush()

        PeriodRepository(s, h.id).create(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        cash = AccountRepository(s, h.id).create(
            code="1110",
            name="Checking",
            type=AccountType.ASSET,
            currency="USD",
        )
        imbalance = AccountRepository(s, h.id).create(
            code="Imbalance:Unknown",
            name="Imbalance: Unknown",
            type=AccountType.EQUITY,
            currency="USD",
        )

        # Attachment + import_batch (raw SQL: easier than wiring the full
        # attachment-encryption pipeline for a service test).
        from sqlalchemy import text

        att_id = uuid4()
        s.execute(
            text(
                "INSERT INTO attachments (household_id, id, filename, "
                "content_type, size_bytes, content_hash, storage_uri, "
                "uploaded_at) VALUES "
                "(:h, :i, 'x.ofx', 'application/x-ofx', 1, :hash, 's3://x', :now)"
            ),
            {
                "h": str(h.id),
                "i": str(att_id),
                "hash": "x" * 64,
                "now": datetime.now(UTC).isoformat(),
            },
        )

        batch = ImportBatch(
            household_id=h.id,
            id=uuid4(),
            account_id=cash.id,
            source_format=SourceFormat.OFX,
            source_filename="x.ofx",
            source_file_attachment_id=att_id,
            status=ImportBatchStatus.PARSED,
            imported_count=0,
            skipped_count=0,
            error_count=0,
            created_at=datetime.now(UTC),
        )
        s.add(batch)
        s.flush()

        lines: list[StatementLine] = []
        for i, (amt, desc) in enumerate(
            [
                (Decimal("-12.50"), "Coffee"),
                (Decimal("-100.00"), "Groceries"),
                (Decimal("2500.00"), "Paycheck"),
            ],
            start=1,
        ):
            line = StatementLine(
                household_id=h.id,
                id=uuid4(),
                import_batch_id=batch.id,
                line_number=i,
                posted_date=date(2026, 5, i),
                amount=amt,
                currency="USD",
                description=desc,
                raw_json="{}",
            )
            s.add(line)
            lines.append(line)
        s.commit()

        yield {
            "household_id": h.id,
            "cash_id": cash.id,
            "imbalance_id": imbalance.id,
            "batch_id": batch.id,
            "line_ids": [line.id for line in lines],
        }


def _reload(s: Session, model_cls, household_id: UUID, obj_id: UUID):
    """Re-fetch a (household_id, id) row from the session."""
    return s.get(model_cls, (household_id, obj_id))


# ---- promote_statement_line ----------------------------------------------


class TestPromoteStatementLine:
    @pytest.mark.asyncio
    async def test_creates_pending_tx_with_two_balanced_postings(self, session_maker, setup):
        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            tx = await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=NullCategorizer(),
                actor_user_id=uuid4(),
            )
            s.commit()

            assert tx.status == TransactionStatus.PENDING
            postings = (
                s.query(Posting).filter_by(transaction_id=tx.id).order_by(Posting.amount).all()
            )
            assert len(postings) == 2
            # Postings sum to zero.
            assert sum(p.amount for p in postings) == Decimal("0")
            # Bank-side posting is on the batch's account, signed as the line.
            bank = next(p for p in postings if p.account_id == setup["cash_id"])
            assert bank.amount == Decimal("-12.50")
            # Other-side posting is on Imbalance:Unknown, negated.
            other = next(p for p in postings if p.account_id == setup["imbalance_id"])
            assert other.amount == Decimal("12.50")
            assert other.currency == "USD"

    @pytest.mark.asyncio
    async def test_sets_imported_from_id_to_batch_id(self, session_maker, setup):
        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            tx = await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            assert tx.imported_from_id == setup["batch_id"]

    @pytest.mark.asyncio
    async def test_links_statement_line_promoted_transaction_id(self, session_maker, setup):
        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            tx = await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            reloaded = _reload(s, StatementLine, setup["household_id"], line.id)
            assert reloaded.promoted_transaction_id == tx.id

    @pytest.mark.asyncio
    async def test_already_promoted_raises(self, session_maker, setup):
        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            line2 = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            with pytest.raises(LineAlreadyPromotedError):
                await promote_statement_line(
                    session=s,
                    household_id=setup["household_id"],
                    batch=batch,
                    line=line2,
                    categorizer=NullCategorizer(),
                    actor_user_id=None,
                )

    @pytest.mark.asyncio
    async def test_excluded_line_raises(self, session_maker, setup):
        with session_maker() as s:
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            line.is_excluded = True
            s.commit()
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line2 = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            with pytest.raises(LineExcludedError):
                await promote_statement_line(
                    session=s,
                    household_id=setup["household_id"],
                    batch=batch,
                    line=line2,
                    categorizer=NullCategorizer(),
                    actor_user_id=None,
                )

    @pytest.mark.asyncio
    async def test_unknown_categorizer_account_raises(self, session_maker, setup):
        class _BadCategorizer:
            async def categorize(
                self, line, household_context, *, session=None
            ) -> CategorizationResult:
                return CategorizationResult(account_code="DoesNotExist", confidence=0.5)

        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            with pytest.raises(CategorizeUnknownAccountError):
                await promote_statement_line(
                    session=s,
                    household_id=setup["household_id"],
                    batch=batch,
                    line=line,
                    categorizer=_BadCategorizer(),
                    actor_user_id=None,
                )

    @pytest.mark.asyncio
    async def test_no_categorize_skips_categorizer_and_auto_creates_imbalance(
        self, session_maker, setup
    ):
        """Slice B: ``no_categorize=True`` bypasses the categorizer entirely
        and routes every line to an auto-created ``Imbalance:Unknown``
        account (code ``9999.<currency>``) for the bank account's currency.

        The categorizer used here would raise if called; the test asserts
        ``no_categorize=True`` short-circuits before invocation.
        """

        class _ExplodingCategorizer:
            async def categorize(
                self, line, household_context, *, session=None
            ) -> CategorizationResult:
                raise AssertionError("categorizer must not be invoked when no_categorize=True")

        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            tx = await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=_ExplodingCategorizer(),
                actor_user_id=None,
                no_categorize=True,
            )
            s.commit()

            # Auto-created Imbalance:Unknown for USD is the other-side account.
            other_account = AccountRepository(s, setup["household_id"]).get_by_code("9999.USD")
            assert other_account is not None
            assert other_account.name == "Imbalance:Unknown"
            assert other_account.type == AccountType.EQUITY
            assert other_account.currency == "USD"

            postings = s.query(Posting).filter_by(transaction_id=tx.id).all()
            other_ids = {p.account_id for p in postings} - {setup["cash_id"]}
            assert other_ids == {other_account.id}

    @pytest.mark.asyncio
    async def test_as_posted_creates_posted_transaction(self, session_maker, setup):
        """Issue #210: ``as_posted=True`` flips the new tx to POSTED instead of PENDING.

        The two postings (bank-side + categorizer/imbalance-side) already
        sum to zero per currency, so the POSTED balance invariant holds.
        """
        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            tx = await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=NullCategorizer(),
                actor_user_id=None,
                as_posted=True,
            )
            s.commit()
            assert tx.status == TransactionStatus.POSTED
            # Postings still balance.
            postings = s.query(Posting).filter_by(transaction_id=tx.id).all()
            assert sum(p.amount for p in postings) == Decimal("0")

    @pytest.mark.asyncio
    async def test_default_is_pending(self, session_maker, setup):
        """Default behavior (no ``as_posted``) preserves the existing PENDING contract."""
        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            tx = await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            assert tx.status == TransactionStatus.PENDING


# ---- apply_batch ----------------------------------------------------------


class TestApplyBatch:
    @pytest.mark.asyncio
    async def test_promotes_all_lines_and_marks_batch_applied(self, session_maker, setup):
        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            result = await apply_batch(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                categorizer=NullCategorizer(),
                actor_user_id=uuid4(),
            )
            s.commit()
            assert result.created_count == 3
            assert result.skipped_count == 0
            assert len(result.transaction_ids) == 3
            reloaded_batch = _reload(s, ImportBatch, setup["household_id"], batch.id)
            assert reloaded_batch.status == ImportBatchStatus.APPLIED
            assert reloaded_batch.applied_at is not None

    @pytest.mark.asyncio
    async def test_skips_excluded_lines(self, session_maker, setup):
        with session_maker() as s:
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][1])
            line.is_excluded = True
            s.commit()
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            result = await apply_batch(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            assert result.created_count == 2
            assert result.skipped_count == 1

    @pytest.mark.asyncio
    async def test_skips_already_promoted_lines(self, session_maker, setup):
        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            # Re-fetch (batch is unchanged, status PARSED)
            batch2 = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            result = await apply_batch(
                session=s,
                household_id=setup["household_id"],
                batch=batch2,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            assert result.created_count == 2
            assert result.skipped_count == 1

    @pytest.mark.asyncio
    async def test_already_applied_raises(self, session_maker, setup):
        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            await apply_batch(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            batch2 = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            with pytest.raises(BatchAlreadyAppliedError):
                await apply_batch(
                    session=s,
                    household_id=setup["household_id"],
                    batch=batch2,
                    categorizer=NullCategorizer(),
                    actor_user_id=None,
                )

    @pytest.mark.asyncio
    async def test_as_posted_creates_all_posted_transactions(self, session_maker, setup):
        """Issue #210: ``as_posted=True`` on apply_batch lands every line as POSTED."""
        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            result = await apply_batch(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                categorizer=NullCategorizer(),
                actor_user_id=None,
                as_posted=True,
            )
            s.commit()
            assert result.created_count == 3
            from tulip_storage.models import Transaction

            txs = s.query(Transaction).filter(Transaction.id.in_(result.transaction_ids)).all()
            assert len(txs) == 3
            assert {tx.status for tx in txs} == {TransactionStatus.POSTED}

    @pytest.mark.asyncio
    async def test_default_apply_batch_creates_pending(self, session_maker, setup):
        """Default ``apply_batch`` (no flag) still produces PENDING transactions."""
        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            result = await apply_batch(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            from tulip_storage.models import Transaction

            txs = s.query(Transaction).filter(Transaction.id.in_(result.transaction_ids)).all()
            assert {tx.status for tx in txs} == {TransactionStatus.PENDING}

    @pytest.mark.asyncio
    async def test_all_lines_excluded_is_no_op_but_flips_status(self, session_maker, setup):
        with session_maker() as s:
            for line_id in setup["line_ids"]:
                line = _reload(s, StatementLine, setup["household_id"], line_id)
                line.is_excluded = True
            s.commit()
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            result = await apply_batch(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            assert result.created_count == 0
            assert result.skipped_count == 3
            reloaded_batch = _reload(s, ImportBatch, setup["household_id"], batch.id)
            assert reloaded_batch.status == ImportBatchStatus.APPLIED

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


# ---- promote_statement_line: split-bearing lines (#297) ----------------


class TestPromoteSplitLine:
    """Per #297: a split-bearing statement line promotes to ONE transaction
    with ``1 + len(splits)`` postings (one bank-side + one per split).

    The split detail lives in ``statement_lines.raw_json`` under a
    reserved ``__splits__`` key (encoded by
    ``serialize_parsed_line_raw_json`` on import). The apply path
    reads it back, resolves each split's category via
    ``AccountRepository.get_by_code``, and falls back to
    ``Imbalance:Unknown`` for unknown categories. ``no_categorize``
    is moot for splits — the source format already categorized them.
    """

    def _seed_split_line(
        self,
        session_maker,
        setup,
        *,
        total: str,
        splits: list[dict],
    ) -> UUID:
        """Insert one new split-bearing statement line; return its id."""
        line_id = uuid4()
        with session_maker() as s:
            line = StatementLine(
                household_id=setup["household_id"],
                id=line_id,
                import_batch_id=setup["batch_id"],
                line_number=99,  # fresh number, no collision with seeded 1/2/3.
                posted_date=date(2026, 1, 2),
                amount=Decimal(total),
                currency="USD",
                description="CenterPoint Energy",
                raw_json=(
                    '{"raw": {"P": "CenterPoint Energy", "T": "' + total + '"}, '
                    '"__splits__": ' + str(splits).replace("'", '"').replace("None", "null") + "}"
                ),
            )
            s.add(line)
            s.commit()
        return line_id

    @pytest.mark.asyncio
    async def test_split_gas_bill_promotes_one_tx_with_three_postings(self, session_maker, setup):
        """Banktivity-style 2-split gas bill: -58.99 total = -45.27 + -13.72."""
        gas_code = "Needs:Utilities:Natural Gas/TulipDrive"
        warranty_code = "Needs:Insurance:Home Warranty/TulipDrive"
        # Seed accounts so the split categories resolve.
        with session_maker() as s:
            AccountRepository(s, setup["household_id"]).create(
                code=gas_code, name="Natural Gas", type=AccountType.EXPENSE, currency="USD"
            )
            AccountRepository(s, setup["household_id"]).create(
                code=warranty_code,
                name="Home Warranty",
                type=AccountType.EXPENSE,
                currency="USD",
            )
            s.commit()

        line_id = self._seed_split_line(
            session_maker,
            setup,
            total="-58.99",
            splits=[
                {
                    "amount": "-45.27",
                    "currency": "USD",
                    "category": gas_code,
                    "memo": "Current gas charges",
                },
                {
                    "amount": "-13.72",
                    "currency": "USD",
                    "category": warranty_code,
                    "memo": "Current home service charges",
                },
            ],
        )

        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line = _reload(s, StatementLine, setup["household_id"], line_id)
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
            postings = list(s.query(Posting).filter_by(transaction_id=tx.id).all())
            # One bank-side + two splits = three postings.
            assert len(postings) == 3
            # Postings sum to zero per currency.
            assert sum(p.amount for p in postings) == Decimal("0")
            # Bank-side carries the parent total.
            bank = next(p for p in postings if p.account_id == setup["cash_id"])
            assert bank.amount == Decimal("-58.99")
            # Two split-side postings, negated.
            split_amounts = sorted(p.amount for p in postings if p.account_id != setup["cash_id"])
            assert split_amounts == [Decimal("13.72"), Decimal("45.27")]
            # Per-split memo round-trips into Posting.memo.
            split_memos = {p.memo for p in postings if p.memo is not None}
            assert split_memos == {"Current gas charges", "Current home service charges"}

    @pytest.mark.asyncio
    async def test_split_paycheck_promotes_one_tx_with_five_postings(self, session_maker, setup):
        """4-split paycheck: +2814.50 = +3500.00 - 420 - 150 - 115.50."""
        wages = "Income:Wages/BNSF"
        fed = "Expenses:Taxes:Federal/BNSF"
        state = "Expenses:Taxes:State/BNSF"
        fica = "Expenses:Taxes:FICA/BNSF"
        with session_maker() as s:
            for code, account_type in [
                (wages, AccountType.INCOME),
                (fed, AccountType.EXPENSE),
                (state, AccountType.EXPENSE),
                (fica, AccountType.EXPENSE),
            ]:
                AccountRepository(s, setup["household_id"]).create(
                    code=code, name=code, type=account_type, currency="USD"
                )
            s.commit()

        line_id = self._seed_split_line(
            session_maker,
            setup,
            total="2814.50",
            splits=[
                {"amount": "3500.00", "currency": "USD", "category": wages, "memo": "Gross"},
                {"amount": "-420.00", "currency": "USD", "category": fed, "memo": "Federal"},
                {"amount": "-150.00", "currency": "USD", "category": state, "memo": "State"},
                {"amount": "-115.50", "currency": "USD", "category": fica, "memo": "FICA"},
            ],
        )

        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line = _reload(s, StatementLine, setup["household_id"], line_id)
            tx = await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()

            postings = list(s.query(Posting).filter_by(transaction_id=tx.id).all())
            assert len(postings) == 5
            assert sum(p.amount for p in postings) == Decimal("0")
            # Bank-side carries the net deposited amount.
            bank = next(p for p in postings if p.account_id == setup["cash_id"])
            assert bank.amount == Decimal("2814.50")

    @pytest.mark.asyncio
    async def test_split_resolves_by_name_path_when_code_misses(self, session_maker, setup):
        """#450: GnuCash-rooted chart accepts Banktivity-style colon-paths.

        The chart has ``Expenses:Wants:Personal:Gifts`` (no ``code``
        populated for any node). QIF emits ``Wants:Personal:Gifts``.
        ``get_by_code`` misses → ``find_by_name_path`` resolves the
        suffix → posting lands on the gift account, not
        Imbalance:Unknown.
        """
        # Build the chart hierarchy without populating ``code``.
        with session_maker() as s:
            repo = AccountRepository(s, setup["household_id"])
            expenses = repo.create(name="Expenses", type=AccountType.EXPENSE, currency="USD")
            wants = repo.create(
                name="Wants",
                type=AccountType.EXPENSE,
                currency="USD",
                parent_account_id=expenses.id,
            )
            personal = repo.create(
                name="Personal",
                type=AccountType.EXPENSE,
                currency="USD",
                parent_account_id=wants.id,
            )
            gifts = repo.create(
                name="Gifts",
                type=AccountType.EXPENSE,
                currency="USD",
                parent_account_id=personal.id,
            )
            gifts_id = gifts.id
            s.commit()

        line_id = self._seed_split_line(
            session_maker,
            setup,
            total="-50.00",
            splits=[
                {
                    "amount": "-50.00",
                    "currency": "USD",
                    "category": "Wants:Personal:Gifts",
                    "memo": None,
                },
            ],
        )

        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line = _reload(s, StatementLine, setup["household_id"], line_id)
            tx = await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            postings = list(s.query(Posting).filter_by(transaction_id=tx.id).all())
            # 2 postings: bank-side + the split (resolved to Gifts).
            assert len(postings) == 2
            other = next(p for p in postings if p.account_id != setup["cash_id"])
            assert other.account_id == gifts_id

    @pytest.mark.asyncio
    async def test_unknown_split_category_falls_back_to_imbalance(self, session_maker, setup):
        """A split whose category doesn't exist routes to Imbalance:Unknown."""
        line_id = self._seed_split_line(
            session_maker,
            setup,
            total="-58.99",
            splits=[
                {
                    "amount": "-45.27",
                    "currency": "USD",
                    "category": "Account:Does:Not:Exist",
                    "memo": None,
                },
                {
                    "amount": "-13.72",
                    "currency": "USD",
                    "category": "Another:Missing:Code",
                    "memo": None,
                },
            ],
        )

        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line = _reload(s, StatementLine, setup["household_id"], line_id)
            tx = await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            postings = list(s.query(Posting).filter_by(transaction_id=tx.id).all())
            # 3 postings: bank + 2 splits → both unknown splits route to
            # the auto-created Imbalance:Unknown (code 9999.USD).
            assert len(postings) == 3
            imbalance = AccountRepository(s, setup["household_id"]).get_by_code("9999.USD")
            assert imbalance is not None
            # Both non-bank postings land on the auto-created Imbalance account.
            other = [p for p in postings if p.account_id != setup["cash_id"]]
            assert len(other) == 2
            assert all(p.account_id == imbalance.id for p in other)
            assert sum(p.amount for p in postings) == Decimal("0")

    @pytest.mark.asyncio
    async def test_links_promoted_transaction_id_on_split_line(self, session_maker, setup):
        """Split lines link to their promoted tx the same way non-split lines do."""
        gas_code = "Needs:Utilities:Natural Gas/TulipDrive"
        with session_maker() as s:
            AccountRepository(s, setup["household_id"]).create(
                code=gas_code, name="Gas", type=AccountType.EXPENSE, currency="USD"
            )
            s.commit()

        line_id = self._seed_split_line(
            session_maker,
            setup,
            total="-58.99",
            splits=[
                {
                    "amount": "-58.99",
                    "currency": "USD",
                    "category": gas_code,
                    "memo": None,
                },
            ],
        )

        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line = _reload(s, StatementLine, setup["household_id"], line_id)
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


# ---- promote_statement_line: QIF C cleared-status field (#279) ----------


class TestPromoteQifClearedStatus:
    """#279: QIF C field in raw_json drives the promoted transaction's status.

    Status priority (per ``promote_statement_line`` docstring):
    1. ``as_posted=True`` → POSTED (highest precedence).
    2. ``treat_cleared_as_pending=True`` → PENDING.
    3. raw_json ``C``: ``c``/``*`` → POSTED; ``R`` → RECONCILED; empty → PENDING.
    4. Otherwise → PENDING.
    """

    @pytest.mark.asyncio
    async def test_c_equals_c_lands_as_posted(self, session_maker, setup):
        """QIF C=c (Banktivity "cleared in register") → POSTED transaction."""
        with session_maker() as s:
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            line.raw_json = '{"raw": {"C": "c"}}'
            s.commit()
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            tx = await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            assert tx.status == TransactionStatus.POSTED

    @pytest.mark.asyncio
    async def test_c_equals_R_lands_as_reconciled(self, session_maker, setup):
        """QIF C=R (matched during reconciliation) → RECONCILED transaction."""
        with session_maker() as s:
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            line.raw_json = '{"raw": {"C": "R"}}'
            s.commit()
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            tx = await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            assert tx.status == TransactionStatus.RECONCILED

    @pytest.mark.asyncio
    async def test_c_star_lands_as_posted(self, session_maker, setup):
        """QIF C=* (legacy "cleared" marker) → POSTED transaction."""
        with session_maker() as s:
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            line.raw_json = '{"raw": {"C": "*"}}'
            s.commit()
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            tx = await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            assert tx.status == TransactionStatus.POSTED

    @pytest.mark.asyncio
    async def test_empty_c_or_missing_lands_as_pending(self, session_maker, setup):
        """No C field → default PENDING (the existing contract)."""
        with session_maker() as s:
            # Test fixture lines have raw_json="{}" — no C field.
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
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

    @pytest.mark.asyncio
    async def test_treat_cleared_as_pending_overrides_C_R(self, session_maker, setup):
        """--treat-cleared-as-pending forces PENDING even when C=R."""
        with session_maker() as s:
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            line.raw_json = '{"raw": {"C": "R"}}'
            s.commit()
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            tx = await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=NullCategorizer(),
                actor_user_id=None,
                treat_cleared_as_pending=True,
            )
            s.commit()
            assert tx.status == TransactionStatus.PENDING

    @pytest.mark.asyncio
    async def test_as_posted_wins_over_C_R(self, session_maker, setup):
        """as_posted=True overrides everything — even C=R → POSTED, not RECONCILED."""
        with session_maker() as s:
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            line.raw_json = '{"raw": {"C": "R"}}'
            s.commit()
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
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


# ---- #447: QIF tags from L / S lines -------------------------------------


class TestPromoteWithQifTags:
    """The line-level tag tuple (union of L-line + per-split tags) lands
    on the transaction via ``transaction_tags``."""

    @pytest.mark.asyncio
    async def test_l_line_tags_land_on_transaction(self, session_maker, setup):
        from tulip_api.services.import_apply import (
            serialize_parsed_line_raw_json,
        )
        from tulip_core.money import Money
        from tulip_core.reconciliation.statement_line import ParsedStatementLine
        from tulip_storage.repositories import TransactionTagRepository

        line_id = uuid4()
        parsed = ParsedStatementLine(
            line_number=42,
            posted_date=date(2026, 5, 1),
            amount=Money(Decimal("-12.50"), "USD"),
            description="Coffee Shop",
            raw={"L": "Expenses:Coffee"},
            tags=("wants", "robert"),
        )
        raw_json = serialize_parsed_line_raw_json(parsed)
        with session_maker() as s:
            line = StatementLine(
                household_id=setup["household_id"],
                id=line_id,
                import_batch_id=setup["batch_id"],
                line_number=42,
                posted_date=date(2026, 5, 1),
                amount=Decimal("-12.50"),
                currency="USD",
                description="Coffee Shop",
                raw_json=raw_json,
            )
            s.add(line)
            s.commit()
        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line = _reload(s, StatementLine, setup["household_id"], line_id)
            tx = await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            tags = TransactionTagRepository(s, setup["household_id"]).list_tags(tx.id)
        assert sorted(tags) == ["robert", "wants"]

    @pytest.mark.asyncio
    async def test_split_tags_union_lands_on_transaction(self, session_maker, setup):
        line_id = self._seed_split_line(
            session_maker,
            setup,
            total="-58.99",
            splits=[
                {
                    "amount": "-45.27",
                    "currency": "USD",
                    "category": "Needs:Utilities",
                    "memo": None,
                    "tags": ["tulipdrive"],
                },
                {
                    "amount": "-13.72",
                    "currency": "USD",
                    "category": "Needs:Insurance",
                    "memo": None,
                    "tags": ["tulipdrive", "warranty"],
                },
            ],
        )
        with session_maker() as s:
            batch = _reload(s, ImportBatch, setup["household_id"], setup["batch_id"])
            line = _reload(s, StatementLine, setup["household_id"], line_id)
            tx = await promote_statement_line(
                session=s,
                household_id=setup["household_id"],
                batch=batch,
                line=line,
                categorizer=NullCategorizer(),
                actor_user_id=None,
            )
            s.commit()
            from tulip_storage.repositories import TransactionTagRepository

            tags = TransactionTagRepository(s, setup["household_id"]).list_tags(tx.id)
        assert sorted(tags) == ["tulipdrive", "warranty"]

    def _seed_split_line(
        self,
        session_maker,
        setup,
        *,
        total: str,
        splits: list[dict],
    ) -> UUID:
        """Mirror of TestPromoteSplitLine._seed_split_line — class-local copy."""
        import json as _json

        line_id = uuid4()
        with session_maker() as s:
            line = StatementLine(
                household_id=setup["household_id"],
                id=line_id,
                import_batch_id=setup["batch_id"],
                line_number=99,
                posted_date=date(2026, 1, 2),
                amount=Decimal(total),
                currency="USD",
                description="Tagged splits",
                raw_json=_json.dumps(
                    {"raw": {"P": "CenterPoint", "T": total}, "__splits__": splits}
                ),
            )
            s.add(line)
            s.commit()
        return line_id

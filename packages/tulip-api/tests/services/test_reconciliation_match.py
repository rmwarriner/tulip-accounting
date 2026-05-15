"""Service-level tests for the reconciliation auto-match + complete flow (P5.4.b)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tulip_api.services.reconciliation_match import (
    AutoMatchAlreadyRunError,
    AutoMatchInvalidStateError,
    CompleteInvalidStateError,
    CompleteUnbalancedError,
    auto_match,
    complete,
)
from tulip_core.money import Money
from tulip_core.transactions import (
    Posting as DomainPosting,
)
from tulip_core.transactions import (
    Transaction as DomainTransaction,
)
from tulip_core.transactions import (
    TransactionStatus as DomainTxStatus,
)
from tulip_storage.models import (
    AccountType,
    Household,
    ImportBatch,
    ImportBatchStatus,
    Reconciliation,
    ReconciliationStatus,
    SourceFormat,
    StatementLine,
)
from tulip_storage.repositories import (
    AccountRepository,
    PeriodRepository,
    ReconciliationMatchRepository,
    ReconciliationRepository,
    TransactionRepository,
)

# ---- fixtures -------------------------------------------------------------


@pytest.fixture
def setup(session_maker):
    """Seed: household, period, checking account, import batch, 2 lines, 2 ledger txs.

    Lines:
      L1: -12.50 USD on 2026-05-12, "Coffee"
      L2: -100.00 USD on 2026-05-15, "Groceries"
    Ledger txs (POSTED, account = checking):
      TX1: -12.50 USD on 2026-05-12, "Coffee Shop"
      TX2: -100.00 USD on 2026-05-15, "Whole Foods"
    Reconciliation envelope:
      Period 2026-05-01..2026-05-31, ending balance -112.50
      source_import_batch_id set
    """
    with session_maker() as s:
        h = Household(id=uuid4(), name="Smith", base_currency="USD")
        s.add(h)
        s.flush()
        PeriodRepository(s, h.id).create(start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        accounts = AccountRepository(s, h.id)
        cash = accounts.create(code="1110", name="Checking", type=AccountType.ASSET, currency="USD")
        food = accounts.create(code="5100", name="Food", type=AccountType.EXPENSE, currency="USD")

        # Attachment + import batch (raw SQL — service test scope).
        att_id = uuid4()
        s.execute(
            text(
                "INSERT INTO attachments (household_id, id, filename, "
                "content_type, size_bytes, content_hash, storage_uri, "
                "uploaded_at) VALUES (:h, :i, 'x.ofx', 'application/x-ofx', "
                "1, :hash, 's3://x', :now)"
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
            imported_count=2,
            skipped_count=0,
            error_count=0,
            created_at=datetime.now(UTC),
        )
        s.add(batch)
        s.flush()

        line_ids: list[UUID] = []
        for i, (amt, desc, day) in enumerate(
            [
                (Decimal("-12.50"), "Coffee", 12),
                (Decimal("-100.00"), "Groceries", 15),
            ],
            start=1,
        ):
            line = StatementLine(
                household_id=h.id,
                id=uuid4(),
                import_batch_id=batch.id,
                line_number=i,
                posted_date=date(2026, 5, day),
                amount=amt,
                currency="USD",
                description=desc,
                raw_json="{}",
            )
            s.add(line)
            line_ids.append(line.id)

        # Ledger transactions: matching descriptions tilted slightly so
        # fuzzy_score lands in MEDIUM bucket (same date + lower fuzzy =>
        # MEDIUM per ADR §Q2).
        tx_repo = TransactionRepository(s, h.id)
        tx_ids: list[UUID] = []
        for amt, desc, day in [
            (Decimal("-12.50"), "Coffee Shop", 12),
            (Decimal("-100.00"), "Whole Foods", 15),
        ]:
            tx = tx_repo.save_balanced(
                DomainTransaction(
                    id=uuid4(),
                    household_id=h.id,
                    date=date(2026, 5, day),
                    description=desc,
                    postings=(
                        DomainPosting(id=uuid4(), account_id=cash.id, amount=Money(amt, "USD")),
                        DomainPosting(
                            id=uuid4(),
                            account_id=food.id,
                            amount=Money(-amt, "USD"),
                        ),
                    ),
                    status=DomainTxStatus.POSTED,
                )
            )
            tx_ids.append(tx.id)

        recon = ReconciliationRepository(s, h.id).create(
            account_id=cash.id,
            statement_period_start=date(2026, 5, 1),
            statement_period_end=date(2026, 5, 31),
            statement_starting_balance=Decimal("0"),
            statement_ending_balance=Decimal("-112.50"),
            currency="USD",
            source_import_batch_id=batch.id,
        )
        s.commit()

        yield {
            "household_id": h.id,
            "cash_id": cash.id,
            "batch_id": batch.id,
            "line_ids": line_ids,
            "tx_ids": tx_ids,
            "recon_id": recon.id,
        }


def _reload(s: Session, model_cls, household_id: UUID, obj_id: UUID):
    return s.get(model_cls, (household_id, obj_id))


# ---- auto_match -----------------------------------------------------------


class TestAutoMatch:
    @pytest.mark.asyncio
    async def test_creates_match_per_candidate_with_matcher_version(self, session_maker, setup):
        with session_maker() as s:
            recon = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            result = await auto_match(
                session=s,
                household_id=setup["household_id"],
                reconciliation=recon,
                actor_user_id=uuid4(),
            )
            s.commit()
            assert result.matches_created == 2
            # Both same-date with non-empty fuzzy >= 0.6 — MEDIUM bucket
            # (token_set_ratio "Coffee" vs "Coffee Shop" ≈ 1.0 -> HIGH;
            # "Groceries" vs "Whole Foods" ≈ low -> MEDIUM under same-date).
            # Either way, total counts == 2.
            assert result.high_count + result.medium_count + result.low_count == 2

            matches = ReconciliationMatchRepository(
                s, setup["household_id"]
            ).list_for_reconciliation(recon.id)
            assert len(matches) == 2
            assert all(m.matcher_version == "v1" for m in matches)
            assert all(m.created_by_user_id is None for m in matches)
            assert all(m.confidence is not None for m in matches)

    @pytest.mark.asyncio
    async def test_already_run_raises(self, session_maker, setup):
        with session_maker() as s:
            recon = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            await auto_match(
                session=s,
                household_id=setup["household_id"],
                reconciliation=recon,
                actor_user_id=None,
            )
            s.commit()
            recon2 = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            with pytest.raises(AutoMatchAlreadyRunError) as exc:
                await auto_match(
                    session=s,
                    household_id=setup["household_id"],
                    reconciliation=recon2,
                    actor_user_id=None,
                )
            assert exc.value.existing_match_count == 2

    @pytest.mark.asyncio
    async def test_completed_state_raises(self, session_maker, setup):
        with session_maker() as s:
            recon = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            recon.status = ReconciliationStatus.COMPLETE
            s.commit()
            recon2 = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            with pytest.raises(AutoMatchInvalidStateError):
                await auto_match(
                    session=s,
                    household_id=setup["household_id"],
                    reconciliation=recon2,
                    actor_user_id=None,
                )

    @pytest.mark.asyncio
    async def test_excluded_lines_skipped(self, session_maker, setup):
        with session_maker() as s:
            line = _reload(s, StatementLine, setup["household_id"], setup["line_ids"][0])
            line.is_excluded = True
            s.commit()
            recon = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            result = await auto_match(
                session=s,
                household_id=setup["household_id"],
                reconciliation=recon,
                actor_user_id=None,
            )
            s.commit()
            # Only 1 line eligible -> at most 1 match.
            assert result.matches_created == 1


# ---- complete -------------------------------------------------------------


class TestComplete:
    @pytest.mark.asyncio
    async def test_balanced_completes(self, session_maker, setup):
        with session_maker() as s:
            recon = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            await auto_match(
                session=s,
                household_id=setup["household_id"],
                reconciliation=recon,
                actor_user_id=None,
            )
            s.commit()
            recon2 = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            result = complete(
                session=s,
                household_id=setup["household_id"],
                reconciliation=recon2,
            )
            s.commit()
            assert result.affected_transaction_count == 2
            reloaded = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            assert reloaded.status is ReconciliationStatus.COMPLETE
            assert reloaded.completed_at is not None

    def test_unbalanced_raises_with_residual(self, session_maker, setup):
        with session_maker() as s:
            recon = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            # No matches yet — matched_net == 0; expected_net = -112.50.
            with pytest.raises(CompleteUnbalancedError) as exc:
                complete(
                    session=s,
                    household_id=setup["household_id"],
                    reconciliation=recon,
                )
            assert exc.value.expected_net == Decimal("-112.50")
            assert exc.value.matched_net == Decimal("0")
            assert exc.value.residual == Decimal("-112.50")

    def test_completed_state_raises(self, session_maker, setup):
        with session_maker() as s:
            recon = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            recon.status = ReconciliationStatus.COMPLETE
            s.commit()
            recon2 = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            with pytest.raises(CompleteInvalidStateError):
                complete(
                    session=s,
                    household_id=setup["household_id"],
                    reconciliation=recon2,
                )


# ---- manual_match ---------------------------------------------------------


class TestManualMatch:
    @pytest.mark.asyncio
    async def test_creates_match_with_user_id_no_matcher_version(self, session_maker, setup):
        from tulip_api.services.reconciliation_match import manual_match

        with session_maker() as s:
            recon = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            user_id = uuid4()
            match = await manual_match(
                session=s,
                household_id=setup["household_id"],
                reconciliation=recon,
                statement_line_id=setup["line_ids"][0],
                ledger_transaction_id=setup["tx_ids"][0],
                match_amount=Decimal("-12.50"),
                currency="USD",
                actor_user_id=user_id,
            )
            s.commit()
            assert match.created_by_user_id == user_id
            assert match.matcher_version is None
            assert match.confidence is None

    @pytest.mark.asyncio
    async def test_amount_mismatch_raises(self, session_maker, setup):
        from tulip_api.services.reconciliation_match import (
            ManualMatchAmountMismatchError,
            manual_match,
        )

        with session_maker() as s:
            recon = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            with pytest.raises(ManualMatchAmountMismatchError):
                await manual_match(
                    session=s,
                    household_id=setup["household_id"],
                    reconciliation=recon,
                    statement_line_id=setup["line_ids"][0],
                    ledger_transaction_id=setup["tx_ids"][0],
                    match_amount=Decimal("-10.00"),
                    currency="USD",
                    actor_user_id=uuid4(),
                )

    @pytest.mark.asyncio
    async def test_already_matched_raises(self, session_maker, setup):
        from tulip_api.services.reconciliation_match import (
            ManualMatchLineAlreadyMatchedError,
            manual_match,
        )

        with session_maker() as s:
            recon = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            await manual_match(
                session=s,
                household_id=setup["household_id"],
                reconciliation=recon,
                statement_line_id=setup["line_ids"][0],
                ledger_transaction_id=setup["tx_ids"][0],
                match_amount=Decimal("-12.50"),
                currency="USD",
                actor_user_id=uuid4(),
            )
            s.commit()
            recon2 = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            with pytest.raises(ManualMatchLineAlreadyMatchedError):
                await manual_match(
                    session=s,
                    household_id=setup["household_id"],
                    reconciliation=recon2,
                    statement_line_id=setup["line_ids"][0],
                    ledger_transaction_id=setup["tx_ids"][1],
                    match_amount=Decimal("-12.50"),
                    currency="USD",
                    actor_user_id=uuid4(),
                )

    @pytest.mark.asyncio
    async def test_tx_account_mismatch_raises(self, session_maker, setup):
        from tulip_api.services.reconciliation_match import (
            ManualMatchTxAccountMismatchError,
            manual_match,
        )
        from tulip_storage.models import AccountType
        from tulip_storage.repositories import (
            AccountRepository,
            TransactionRepository,
        )

        with session_maker() as s:
            other = AccountRepository(s, setup["household_id"]).create(
                code="2000",
                name="Other",
                type=AccountType.LIABILITY,
                currency="USD",
            )
            food = AccountRepository(s, setup["household_id"]).get_by_code("5100")
            assert food is not None
            tx = TransactionRepository(s, setup["household_id"]).save_balanced(
                DomainTransaction(
                    id=uuid4(),
                    household_id=setup["household_id"],
                    date=date(2026, 5, 15),
                    description="Unrelated",
                    postings=(
                        DomainPosting(
                            id=uuid4(), account_id=other.id, amount=Money(Decimal("-5"), "USD")
                        ),
                        DomainPosting(
                            id=uuid4(), account_id=food.id, amount=Money(Decimal("5"), "USD")
                        ),
                    ),
                    status=DomainTxStatus.POSTED,
                )
            )
            s.commit()
            recon = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            with pytest.raises(ManualMatchTxAccountMismatchError):
                await manual_match(
                    session=s,
                    household_id=setup["household_id"],
                    reconciliation=recon,
                    statement_line_id=setup["line_ids"][0],
                    ledger_transaction_id=tx.id,
                    match_amount=Decimal("-12.50"),
                    currency="USD",
                    actor_user_id=uuid4(),
                )


# ---- carry-forward --------------------------------------------------------


class TestCarryForward:
    def test_add_marks_in_period_tx(self, session_maker, setup):
        from tulip_api.services.reconciliation_match import add_carry_forward
        from tulip_storage.repositories import TransactionRepository

        with session_maker() as s:
            recon = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            add_carry_forward(
                session=s,
                household_id=setup["household_id"],
                reconciliation=recon,
                transaction_ids=[setup["tx_ids"][0]],
            )
            s.commit()
            tx = TransactionRepository(s, setup["household_id"]).get(setup["tx_ids"][0])
            assert tx.carried_forward_from_reconciliation_id == recon.id

    def test_add_out_of_period_raises(self, session_maker, setup):
        from tulip_api.services.reconciliation_match import (
            CarryForwardTxNotInPeriodError,
            add_carry_forward,
        )
        from tulip_storage.repositories import (
            AccountRepository,
            TransactionRepository,
        )

        with session_maker() as s:
            cash = AccountRepository(s, setup["household_id"]).get_by_code("1110")
            food = AccountRepository(s, setup["household_id"]).get_by_code("5100")
            assert cash is not None and food is not None
            out_tx = TransactionRepository(s, setup["household_id"]).save_balanced(
                DomainTransaction(
                    id=uuid4(),
                    household_id=setup["household_id"],
                    date=date(2026, 6, 5),
                    description="Out of period",
                    postings=(
                        DomainPosting(
                            id=uuid4(), account_id=cash.id, amount=Money(Decimal("-7"), "USD")
                        ),
                        DomainPosting(
                            id=uuid4(), account_id=food.id, amount=Money(Decimal("7"), "USD")
                        ),
                    ),
                    status=DomainTxStatus.POSTED,
                )
            )
            s.commit()
            recon = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            with pytest.raises(CarryForwardTxNotInPeriodError):
                add_carry_forward(
                    session=s,
                    household_id=setup["household_id"],
                    reconciliation=recon,
                    transaction_ids=[out_tx.id],
                )

    def test_remove_clears_link(self, session_maker, setup):
        from tulip_api.services.reconciliation_match import (
            add_carry_forward,
            remove_carry_forward,
        )
        from tulip_storage.repositories import TransactionRepository

        with session_maker() as s:
            recon = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            add_carry_forward(
                session=s,
                household_id=setup["household_id"],
                reconciliation=recon,
                transaction_ids=[setup["tx_ids"][0]],
            )
            s.commit()
            remove_carry_forward(
                session=s,
                household_id=setup["household_id"],
                reconciliation=recon,
                transaction_id=setup["tx_ids"][0],
            )
            s.commit()
            tx = TransactionRepository(s, setup["household_id"]).get(setup["tx_ids"][0])
            assert tx.carried_forward_from_reconciliation_id is None

    def test_complete_with_carry_forward_balances(self, session_maker, setup):
        """Carry-forward deducts from expected_net so /complete balances."""
        from tulip_api.services.reconciliation_match import (
            add_carry_forward,
            complete,
        )

        with session_maker() as s:
            recon = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            add_carry_forward(
                session=s,
                household_id=setup["household_id"],
                reconciliation=recon,
                transaction_ids=setup["tx_ids"],
            )
            s.commit()
            recon2 = _reload(s, Reconciliation, setup["household_id"], setup["recon_id"])
            result = complete(
                session=s,
                household_id=setup["household_id"],
                reconciliation=recon2,
            )
            s.commit()
            assert result.affected_transaction_count == 0

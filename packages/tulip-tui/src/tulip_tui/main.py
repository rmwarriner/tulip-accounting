"""Entry point for the ``tulip-tui`` console script.

Resolves the CLI's stored ``api_url`` + on-disk token store, builds
loaders for every screen, and hands them to ``TulipTuiApp``. Tests
bypass this path entirely by constructing ``TulipTuiApp`` directly
with their own loaders.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from tulip_cli.auth.tokens import default_token_store

if TYPE_CHECKING:
    from decimal import Decimal

    from tulip_tui.data.ai_categorize import AIProposalCandidate
from tulip_cli.config import load_config
from tulip_cli.http import TulipClient
from tulip_tui.app import TulipTuiApp
from tulip_tui.data.account_write import (
    AccountDraft,
    create_account,
    update_account,
)
from tulip_tui.data.accounts import AccountsData, load_accounts
from tulip_tui.data.envelopes import EnvelopesData, load_envelopes
from tulip_tui.data.import_batch_detail import (
    ImportBatchDetail,
    load_import_batch_detail,
    patch_line_excluded,
    promote_line,
)
from tulip_tui.data.import_batch_detail import apply_batch as _apply_batch_call
from tulip_tui.data.imports import ImportsData, load_import_batches
from tulip_tui.data.pending import PendingData, load_pending
from tulip_tui.data.reconciliation_detail import (
    ReconciliationDetail,
    auto_match,
    carry_forward,
    complete,
    load_reconciliation_detail,
    manual_match,
    paper_match,
    reject_match,
)
from tulip_tui.data.reconciliations import ReconciliationsData, load_reconciliations
from tulip_tui.data.reports import ReportPayload, ReportSpec, load_report
from tulip_tui.data.sinking_funds import SinkingFundsData, load_sinking_funds
from tulip_tui.data.transaction_write import (
    TransactionDraft,
    create_transaction,
    update_transaction,
    void_transaction,
)
from tulip_tui.data.transactions import TransactionsData, load_transactions
from tulip_tui.screens.transactions import TransactionsLoader


def _accounts_loader() -> AccountsData:
    """Open a fresh ``TulipClient`` per load and round-trip ``load_accounts``."""
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return load_accounts(client)


def _transactions_loader_factory(account_id: str | None) -> TransactionsLoader:
    """Build a loader that pulls transactions filtered by ``account_id``."""

    def _load() -> TransactionsData:
        config = load_config()
        with TulipClient(config, token_store=default_token_store()) as client:
            return load_transactions(client, account_id=account_id)

    return _load


def _reports_loader(spec: ReportSpec) -> ReportPayload:
    """Open a fresh ``TulipClient`` per fetch and round-trip ``load_report``."""
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return load_report(client, spec)


def _reconciliations_loader() -> ReconciliationsData:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return load_reconciliations(client)


def _imports_loader() -> ImportsData:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return load_import_batches(client)


def _import_batch_detail_factory(
    batch_id: str,
) -> Callable[[], ImportBatchDetail]:
    """Build a loader that pulls one import batch's parsed lines."""

    def _load() -> ImportBatchDetail:
        config = load_config()
        with TulipClient(config, token_store=default_token_store()) as client:
            return load_import_batch_detail(client, batch_id)

    return _load


def _line_exclude_action(batch_id: str, line_id: str, is_excluded: bool) -> None:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        patch_line_excluded(client, batch_id, line_id, is_excluded=is_excluded)


def _line_promote_action(batch_id: str, line_id: str) -> None:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        promote_line(client, batch_id, line_id)


def _batch_apply_action(
    batch_id: str,
    as_posted: bool,
    no_categorize: bool,
    treat_cleared_as_pending: bool,
) -> object:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return _apply_batch_call(
            client,
            batch_id,
            as_posted=as_posted,
            no_categorize=no_categorize,
            treat_cleared_as_pending=treat_cleared_as_pending,
        )


def _envelopes_loader() -> EnvelopesData:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return load_envelopes(client)


def _sinking_funds_loader() -> SinkingFundsData:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return load_sinking_funds(client)


def _pending_loader() -> PendingData:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return load_pending(client)


def _reconciliation_detail_factory(
    reconciliation_id: str,
) -> Callable[[], ReconciliationDetail]:
    def _load() -> ReconciliationDetail:
        config = load_config()
        with TulipClient(config, token_store=default_token_store()) as client:
            return load_reconciliation_detail(client, reconciliation_id)

    return _load


def _reconciliation_auto_match(reconciliation_id: str) -> object:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return auto_match(client, reconciliation_id)


def _reconciliation_reject(reconciliation_id: str, match_id: str) -> None:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        reject_match(client, reconciliation_id, match_id)


def _reconciliation_manual_match(
    reconciliation_id: str,
    statement_line_id: str,
    ledger_transaction_id: str,
    match_amount: str,
    currency: str,
) -> object:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return manual_match(
            client,
            reconciliation_id,
            statement_line_id=statement_line_id,
            ledger_transaction_id=ledger_transaction_id,
            match_amount=match_amount,
            currency=currency,
        )


def _reconciliation_paper_match(reconciliation_id: str, ledger_transaction_id: str) -> object:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return paper_match(client, reconciliation_id, ledger_transaction_id=ledger_transaction_id)


def _reconciliation_carry_forward(reconciliation_id: str, transaction_id: str) -> object:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return carry_forward(client, reconciliation_id, transaction_ids=[transaction_id])


def _reconciliation_complete(reconciliation_id: str) -> object:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return complete(client, reconciliation_id)


def _tx_create_action(draft: TransactionDraft) -> object:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return create_transaction(client, draft)


def _tx_edit_action(tx_id: str, draft: TransactionDraft) -> object:
    """Best-effort PATCH for PENDING transactions.

    The TUI screen routes only PENDING transactions to ``e``; the API
    will 409 if the user managed to slip a POSTED one through.
    """
    config = load_config()
    patch: dict[str, object] = {
        "date": draft.date,
        "description": draft.description,
        "tags": list(draft.tags),
    }
    if draft.reference is not None:
        patch["reference"] = draft.reference
    with TulipClient(config, token_store=default_token_store()) as client:
        return update_transaction(client, tx_id, patch)


def _tx_void_action(tx_id: str, reason: str) -> object:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return void_transaction(client, tx_id, reason=reason)


def _account_create_action(draft: AccountDraft) -> object:
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return create_account(client, draft)


def _tx_fetch_proposals_action(
    description: str, amount: Decimal, currency: str, posted_date: str
) -> tuple[AIProposalCandidate, ...]:
    """Call ``POST /v1/ai/categorize-proposals`` (#425)."""
    from tulip_tui.data.ai_categorize import fetch_proposals

    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        return fetch_proposals(
            client,
            description=description,
            amount=amount,
            currency=currency,
            posted_date=posted_date,
        )


def _tx_apply_category_action(tx_id: str, account_code: str) -> object:
    """Re-categorize a PENDING transaction (#425).

    Resolves ``account_code`` to an account id, fetches the current
    transaction, finds the non-bank posting (the one whose account is
    income/expense, currently Imbalance:Unknown), and PATCHes the
    transaction with the new ``account_id`` on that posting. Errors
    propagate to the screen's notice line.
    """
    config = load_config()
    with TulipClient(config, token_store=default_token_store()) as client:
        accounts = client.get("/v1/accounts", authenticated=True).json()
        target = next((a for a in accounts if a.get("code") == account_code), None)
        if target is None:
            raise RuntimeError(
                f"AI proposed code {account_code!r} but no account with that "
                f"code is visible — refresh the chart and try again"
            )
        tx = client.get(f"/v1/transactions/{tx_id}", authenticated=True).json()
        # Find the non-bank-side posting. Heuristic: largest |amount| is the
        # bank side; the rest are the categorizable legs. For a 2-posting
        # transaction (the common case), that's everything that ISN'T the
        # bank side.
        postings = tx.get("postings") or []
        if len(postings) < 2:
            raise RuntimeError("transaction has < 2 postings — can't recategorize")
        from decimal import Decimal as _D

        bank_idx = max(
            range(len(postings)),
            key=lambda i: abs(_D(str(postings[i].get("amount", "0")))),
        )
        new_postings = []
        for i, p in enumerate(postings):
            entry = {
                "account_id": p["account_id"] if i == bank_idx else target["id"],
                "amount": str(p["amount"]),
                "currency": p["currency"],
            }
            if p.get("memo"):
                entry["memo"] = p["memo"]
            if p.get("pool_id"):
                entry["pool_id"] = p["pool_id"]
            new_postings.append(entry)
        return client.patch(
            f"/v1/transactions/{tx_id}",
            authenticated=True,
            json={"postings": new_postings},
        ).json()


def _account_edit_action(account_id: str, draft: AccountDraft) -> object:
    """PATCH a subset of the editable fields."""
    config = load_config()
    patch: dict[str, object] = {
        "name": draft.name,
        "visibility": draft.visibility,
        "is_placeholder": draft.is_placeholder,
        "tags": list(draft.tags),
    }
    if draft.code is not None:
        patch["code"] = draft.code
    if draft.subtype is not None:
        patch["subtype"] = draft.subtype
    if draft.parent_account_id is not None:
        patch["parent_account_id"] = draft.parent_account_id
    if draft.notes is not None:
        patch["notes"] = draft.notes
    with TulipClient(config, token_store=default_token_store()) as client:
        return update_account(client, account_id, patch)


def run() -> None:
    """Launch the Tulip TUI against the configured API."""
    TulipTuiApp(
        loader=_accounts_loader,
        transactions_loader_factory=_transactions_loader_factory,
        reports_loader=_reports_loader,
        reconciliations_loader=_reconciliations_loader,
        imports_loader=_imports_loader,
        import_batch_detail_factory=_import_batch_detail_factory,
        line_exclude_action=_line_exclude_action,
        line_promote_action=_line_promote_action,
        batch_apply_action=_batch_apply_action,
        envelopes_loader=_envelopes_loader,
        sinking_funds_loader=_sinking_funds_loader,
        pending_loader=_pending_loader,
        reconciliation_detail_factory=_reconciliation_detail_factory,
        reconciliation_auto_match=_reconciliation_auto_match,
        reconciliation_reject=_reconciliation_reject,
        reconciliation_manual_match=_reconciliation_manual_match,
        reconciliation_paper_match=_reconciliation_paper_match,
        reconciliation_carry_forward=_reconciliation_carry_forward,
        reconciliation_complete=_reconciliation_complete,
        tx_create_action=_tx_create_action,
        tx_edit_action=_tx_edit_action,
        tx_void_action=_tx_void_action,
        tx_fetch_proposals_action=_tx_fetch_proposals_action,
        tx_apply_category_action=_tx_apply_category_action,
        account_create_action=_account_create_action,
        account_edit_action=_account_edit_action,
    ).run()

"""Transaction register — P9.2 of [#309](https://github.com/rmwarriner/tulip-accounting/issues/309).

Scrollable list of transactions with a posting-detail pane that
follows the cursor (mirrors `docs/TUI_WIREFRAMES.md § Transactions
list`). Filters (status / date / account) flow through the loader at
construction time; surfacing filter widgets in the UI is a separate
slice.

Reachable via ``Enter`` from the accounts screen (drills into that
account's transactions) and via the back-pop binding ``escape``.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from tulip_tui.data.ai_categorize import AIProposalCandidate
from tulip_tui.data.transaction_write import TransactionDraft
from tulip_tui.data.transactions import (
    PostingSummary,
    TransactionsData,
    TransactionSummary,
)
from tulip_tui.screens.ai_categorize_modal import AICategorizeProposalModal
from tulip_tui.screens.transaction_modal import (
    TransactionEditModal,
    VoidConfirmModal,
)

TransactionsLoader = Callable[[], TransactionsData]
CreateTransactionAction = Callable[[TransactionDraft], object]
EditTransactionAction = Callable[[str, TransactionDraft], object]
VoidTransactionAction = Callable[[str, str], object]
FetchProposalsAction = Callable[[str, "Decimal", str, str], tuple[AIProposalCandidate, ...]]
ApplyCategoryAction = Callable[[str, str], object]


def _noop_create(_draft: TransactionDraft) -> object:
    raise RuntimeError("create action not configured")


def _noop_edit(_tx_id: str, _draft: TransactionDraft) -> object:
    raise RuntimeError("edit action not configured")


def _noop_void(_tx_id: str, _reason: str) -> object:
    raise RuntimeError("void action not configured")


def _noop_fetch_proposals(
    _description: str, _amount: Decimal, _currency: str, _posted_date: str
) -> tuple[AIProposalCandidate, ...]:
    raise RuntimeError("AI categorize proposals action not configured")


def _noop_apply_category(_tx_id: str, _account_code: str) -> object:
    raise RuntimeError("apply category action not configured")


def _row_text(tx: TransactionSummary) -> tuple[str, str, str, str, str]:
    leg_summary = " · ".join(f"{p.account_label} {_signed(p)}" for p in tx.postings[:2])
    if len(tx.postings) > 2:
        leg_summary += f" (+{len(tx.postings) - 2})"
    return (tx.date, tx.description, tx.status, leg_summary, tx.amount_display)


def _signed(posting: PostingSummary) -> str:
    quantised = posting.amount.quantize(Decimal("0.01"))
    return f"{quantised:,.2f}"


def _detail_text(tx: TransactionSummary) -> str:
    lines = [f"[b]{tx.description}[/b]    {tx.date}    [{tx.status}]"]
    if tx.reference:
        lines.append(f"reference: {tx.reference}")
    if tx.notes:
        lines.append(f"notes: {tx.notes}")
    if tx.tags:
        lines.append(f"tags: {' · '.join(tx.tags)}")
    lines.append("")
    for posting in tx.postings:
        memo = f"  ({posting.memo})" if posting.memo else ""
        lines.append(f"  {posting.account_label:<24} {_signed(posting)} {posting.currency}{memo}")
    return "\n".join(lines)


class TransactionsScreen(Screen[None]):
    """Scrollable transaction list with a posting-detail pane."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "back", show=True),
        Binding("r", "refresh", "refresh", show=True),
        Binding("n", "new_tx", "new tx", show=True),
        Binding("e", "edit_tx", "edit tx", show=True),
        Binding("x", "void_tx", "void tx", show=True),
        Binding("c", "categorize", "AI categorize", show=True),
    ]

    DEFAULT_CSS = """
    TransactionsScreen {
        layout: vertical;
    }

    TransactionsScreen #tx-status {
        height: auto;
        padding: 0 1;
    }

    TransactionsScreen #tx-table {
        height: 2fr;
    }

    TransactionsScreen #tx-detail {
        height: 1fr;
        padding: 1 2;
        border-top: solid $accent;
    }
    """

    def __init__(
        self,
        loader: TransactionsLoader,
        *,
        on_create: CreateTransactionAction = _noop_create,
        on_edit: EditTransactionAction = _noop_edit,
        on_void: VoidTransactionAction = _noop_void,
        on_fetch_proposals: FetchProposalsAction = _noop_fetch_proposals,
        on_apply_category: ApplyCategoryAction = _noop_apply_category,
    ) -> None:
        """Store the loader and the per-action callbacks (P9.6.c, #425)."""
        super().__init__()
        self._loader = loader
        self._on_create = on_create
        self._on_edit = on_edit
        self._on_void = on_void
        self._on_fetch_proposals = on_fetch_proposals
        self._on_apply_category = on_apply_category
        self._data: TransactionsData = TransactionsData(transactions=())
        self._rendered_rows: list[str] = []
        self._empty = False
        self._error: str | None = None
        self._row_index_to_tx: list[TransactionSummary] = []
        self._detail_text: str = ""
        self._notice: str = ""

    def compose(self) -> ComposeResult:
        """Lay out the status strip, table, and posting-detail pane."""
        yield Header()
        with Vertical():
            yield Static("loading transactions…", id="tx-status")
            yield DataTable(id="tx-table", zebra_stripes=True, cursor_type="row")
            yield Static("", id="tx-detail")
        yield Footer()

    def on_mount(self) -> None:
        """Install column headers and run the initial load."""
        table = self.query_one("#tx-table", DataTable)
        table.add_columns("Date", "Description", "Status", "Postings", "Amount")
        self._load()

    def action_refresh(self) -> None:
        """Re-run the loader and repopulate the table in place."""
        self._load()

    def action_new_tx(self) -> None:
        """``n`` — push the add-transaction modal."""
        modal = TransactionEditModal(title="New transaction")
        self.app.push_screen(modal, self._on_new_tx_modal_done)

    def action_edit_tx(self) -> None:
        """``e`` — push the edit modal pre-filled from the cursor row."""
        tx = self._cursor_tx()
        if tx is None:
            self._set_notice("no transaction focused")
            return
        if tx.status != "pending":
            self._set_notice(
                f"only PENDING transactions can be edited in-place (this one is {tx.status})"
            )
            return
        # Render the current postings as `account=amount` lines so
        # the user can tweak in place.
        postings_text = "\n".join(
            f"{p.account_label}={p.amount.normalize()}@{p.currency}" for p in tx.postings
        )
        modal = TransactionEditModal(
            title=f"Edit {tx.id[:8]}",
            initial_date=tx.date,
            initial_description=tx.description,
            initial_reference=tx.reference or "",
            initial_postings=postings_text,
            initial_tags=tx.tags,
        )
        tx_id = tx.id
        self.app.push_screen(
            modal,
            lambda result: self._on_edit_tx_modal_done(tx_id, result),
        )

    def action_void_tx(self) -> None:
        """``x`` — push the void-confirm modal for the cursor row."""
        tx = self._cursor_tx()
        if tx is None:
            self._set_notice("no transaction focused")
            return
        if tx.status == "pending":
            # PENDING can be hard-deleted; the void endpoint is for POSTED.
            # Surface the option as void-with-empty-reason → hard delete?
            # For simplicity, route PENDING through the same modal but
            # call delete on submit.
            modal = VoidConfirmModal(tx_id=tx.id, description=tx.description)
            tx_id = tx.id
            self.app.push_screen(
                modal,
                lambda result: self._on_void_modal_done(tx_id, result),
            )
            return
        modal = VoidConfirmModal(tx_id=tx.id, description=tx.description)
        tx_id = tx.id
        self.app.push_screen(modal, lambda result: self._on_void_modal_done(tx_id, result))

    def action_categorize(self) -> None:
        """``c`` — fetch AI proposals + push the picker modal (#425).

        Only meaningful on PENDING transactions. Any failure surfaces
        as an inline notice rather than crashing the screen.
        """
        tx = self._cursor_tx()
        if tx is None:
            self._set_notice("no transaction focused")
            return
        if tx.status != "pending":
            self._set_notice(
                f"AI categorize only works on PENDING transactions (this one is {tx.status})"
            )
            return
        # Use the transaction's largest non-bank-side posting amount + currency
        # as the synthetic line for the propose call. v1 sends ``description``
        # straight through; the API normalises further.
        if not tx.postings:
            self._set_notice("transaction has no postings to categorize")
            return
        # Pick the posting whose absolute amount is largest — typically the
        # bank-side leg. We send its currency + the transaction amount.
        largest = max(tx.postings, key=lambda p: abs(p.amount))
        try:
            candidates = self._on_fetch_proposals(
                tx.description, largest.amount, largest.currency, tx.date
            )
        except Exception as exc:
            self._set_notice(f"[red]proposals failed:[/red] {exc}")
            return
        modal = AICategorizeProposalModal(description=tx.description, candidates=candidates)
        tx_id = tx.id
        self.app.push_screen(modal, lambda result: self._on_categorize_modal_done(tx_id, result))

    def _on_categorize_modal_done(self, tx_id: str, result: object) -> None:
        if result is None:
            self._set_notice("categorize cancelled")
            return
        if not isinstance(result, str):
            return
        try:
            self._on_apply_category(tx_id, result)
        except Exception as exc:
            self._set_notice(f"[red]apply failed:[/red] {exc}")
            return
        self._set_notice(f"categorized as {result}")
        self._load()

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        """Re-render the detail pane to match the newly-highlighted row."""
        self._refresh_detail()

    # -- modal-done handlers -----------------------------------------------

    def _on_new_tx_modal_done(self, result: object) -> None:
        if result is None:
            self._set_notice("add cancelled")
            return
        from tulip_tui.data.transaction_write import TransactionDraft as _Draft

        assert isinstance(result, _Draft)  # noqa: S101
        try:
            response = self._on_create(result)
        except Exception as exc:
            self._set_notice(f"[red]create failed:[/red] {exc}")
            return
        new_id = response.get("id") if isinstance(response, dict) else None
        self._set_notice(f"created {str(new_id)[:8] if new_id else 'transaction'}")
        self._load()

    def _on_edit_tx_modal_done(self, tx_id: str, result: object) -> None:
        if result is None:
            self._set_notice("edit cancelled")
            return
        from tulip_tui.data.transaction_write import TransactionDraft as _Draft

        assert isinstance(result, _Draft)  # noqa: S101
        try:
            self._on_edit(tx_id, result)
        except Exception as exc:
            self._set_notice(f"[red]edit failed:[/red] {exc}")
            return
        self._set_notice(f"updated {tx_id[:8]}")
        self._load()

    def _on_void_modal_done(self, tx_id: str, result: object) -> None:
        if result is None:
            self._set_notice("void cancelled")
            return
        reason = str(result)
        try:
            self._on_void(tx_id, reason)
        except Exception as exc:
            self._set_notice(f"[red]void failed:[/red] {exc}")
            return
        self._set_notice(f"voided {tx_id[:8]}")
        self._load()

    # -- internals -----------------------------------------------------

    def _load(self) -> None:
        self._error = None
        try:
            self._data = self._loader()
        except Exception as exc:
            self._error = str(exc)
            self._render_error(exc)
            return
        self._populate(self._data)

    def _populate(self, data: TransactionsData) -> None:
        table = self.query_one("#tx-table", DataTable)
        table.clear()
        self._rendered_rows = []
        self._row_index_to_tx = []

        status = self.query_one("#tx-status", Static)
        status.update(f"{len(data.transactions)} transactions")

        if not data.transactions:
            self._empty = True
            self._set_detail("No transactions match.")
            return

        self._empty = False
        for tx in data.transactions:
            cells = _row_text(tx)
            table.add_row(*cells)
            self._rendered_rows.append(" ".join(cells))
            self._row_index_to_tx.append(tx)
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        if not self._row_index_to_tx:
            return
        table = self.query_one("#tx-table", DataTable)
        cursor = table.cursor_row
        # DataTable.cursor_row is -1 before any row exists; clamp to 0.
        index = max(0, cursor)
        if index >= len(self._row_index_to_tx):
            return
        tx = self._row_index_to_tx[index]
        self._set_detail(_detail_text(tx))

    def _render_error(self, exc: BaseException) -> None:
        table = self.query_one("#tx-table", DataTable)
        table.clear()
        self._rendered_rows = []
        self._row_index_to_tx = []
        self._empty = False
        status = self.query_one("#tx-status", Static)
        status.update(f"[red]error:[/red] {exc}")
        self._set_detail(f"error: {exc}")

    def _set_detail(self, text: str) -> None:
        self._detail_text = text
        detail = self.query_one("#tx-detail", Static)
        detail.update(text)

    def _set_notice(self, text: str) -> None:
        self._notice = text
        status = self.query_one("#tx-status", Static)
        status.update(
            f"{len(self._row_index_to_tx)} transactions"
            + (f"    [dim]{text}[/dim]" if text else "")
        )

    def _cursor_tx(self) -> TransactionSummary | None:
        if not self._row_index_to_tx:
            return None
        table = self.query_one("#tx-table", DataTable)
        cursor = max(0, table.cursor_row)
        if cursor >= len(self._row_index_to_tx):
            return None
        return self._row_index_to_tx[cursor]

    # -- introspection used by tests ----------------------------------

    def rendered_rows(self) -> list[str]:
        """Return a string-only mirror of the table's rows for assertions."""
        return list(self._rendered_rows)

    def has_no_transactions(self) -> bool:
        """True when the most-recent load returned zero rows."""
        return self._empty

    def detail_text(self) -> str:
        """Return the current detail-pane text as a plain string."""
        return self._detail_text

    def notice(self) -> str:
        """Return the last action notice (set by add/edit/void)."""
        return self._notice

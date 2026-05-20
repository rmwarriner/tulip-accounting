"""Reconciliation detail / actioning screen — P9.6.b of [#414](https://github.com/rmwarriner/2014).

Reached with ``enter`` on a row in the reconciliations browser.
Renders three stacked tables — Matches / Unmatched Lines / Unmatched
Transactions — and exposes the daily-driver actions inline:

- ``a`` run auto-match (when matches list is empty)
- ``x`` reject the highlighted match (matches table only)
- ``m`` manual match: picker modal lists unmatched txs; user picks
  one to pair with the highlighted unmatched line. Amount + currency
  default to the line's.
- ``k`` mark-cleared: paper-match the highlighted unmatched tx
  (paper-statement reconciliations only — the API enforces this).
- ``f`` carry-forward the highlighted unmatched tx.
- ``c`` complete the reconciliation (errors surface inline if the
  envelope isn't balanced).

Already-complete reconciliations show actions in a "view-only"
state — refresh / escape still work; mutation keys produce a
notice rather than calling the API.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from tulip_tui.data.reconciliation_detail import (
    MatchSummary,
    ReconciliationDetail,
    UnmatchedLine,
    UnmatchedTransaction,
)

ReconciliationDetailLoader = Callable[[], ReconciliationDetail]
AutoMatchAction = Callable[[], object]
RejectAction = Callable[[str], None]
ManualMatchAction = Callable[[str, str, str, str], object]
PaperMatchAction = Callable[[str], object]
CarryForwardAction = Callable[[str], object]
CompleteAction = Callable[[], object]


def _match_row(m: MatchSummary) -> tuple[str, str, str, str, str]:
    return (
        m.confidence or ("manual" if m.is_manual else "—"),
        m.statement_line_id[:8] if m.statement_line_id else "—",
        m.ledger_transaction_id[:8],
        f"{m.match_amount} {m.currency}",
        m.id[:8],
    )


def _line_row(line: UnmatchedLine) -> tuple[str, str, str, str, str]:
    return (
        str(line.line_number),
        line.posted_date,
        line.description,
        f"{line.amount_display} {line.currency}",
        line.id[:8],
    )


def _tx_row(tx: UnmatchedTransaction) -> tuple[str, str, str, str, str]:
    return (
        tx.date,
        tx.description,
        tx.reference or "—",
        tx.status,
        tx.id[:8],
    )


class ManualMatchPickerModal(ModalScreen[str | None]):
    """Picker modal — choose a ledger tx to pair with a given line (P9.6.b)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "cancel", show=True),
    ]

    DEFAULT_CSS = """
    ManualMatchPickerModal {
        align: center middle;
    }
    ManualMatchPickerModal #picker-panel {
        width: 90%;
        height: 80%;
        background: $panel;
        border: thick $primary;
        padding: 1 2;
    }
    ManualMatchPickerModal #picker-table {
        height: 1fr;
    }
    ManualMatchPickerModal #picker-buttons {
        align-horizontal: right;
        height: auto;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        *,
        line: UnmatchedLine,
        candidates: tuple[UnmatchedTransaction, ...],
    ) -> None:
        """Store the line + candidate list for rendering."""
        super().__init__()
        self._line = line
        self._candidates = candidates

    def compose(self) -> ComposeResult:
        """Lay out title, candidate picker, and the confirm/cancel buttons."""
        with Vertical(id="picker-panel"):
            yield Static(
                f"[b]Pair line {self._line.line_number}[/b]  "
                f"{self._line.posted_date}  "
                f"{self._line.description}  "
                f"({self._line.amount_display} {self._line.currency})\n"
                f"Pick the ledger transaction it matches:",
                id="picker-title",
            )
            yield DataTable(id="picker-table", zebra_stripes=True, cursor_type="row")
            with Horizontal(id="picker-buttons"):
                yield Button("Cancel", id="picker-cancel")
                yield Button("Confirm", variant="primary", id="picker-confirm")

    def on_mount(self) -> None:
        """Install columns and populate the picker on mount."""
        table = self.query_one("#picker-table", DataTable)
        table.add_columns("Date", "Description", "Ref", "Status", "Id")
        for tx in self._candidates:
            table.add_row(*_tx_row(tx))
        if self._candidates:
            table.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Confirm with the chosen tx id, or cancel."""
        if event.button.id == "picker-confirm":
            self.dismiss(self._chosen_id())
        elif event.button.id == "picker-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        """``escape`` cancels."""
        self.dismiss(None)

    def _chosen_id(self) -> str | None:
        if not self._candidates:
            return None
        table = self.query_one("#picker-table", DataTable)
        cursor = max(0, table.cursor_row)
        if cursor >= len(self._candidates):
            return None
        return self._candidates[cursor].id


class ReconciliationDetailScreen(Screen[None]):
    """Per-reconciliation action surface (P9.6.b)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "back", show=True),
        Binding("r", "refresh", "refresh", show=True),
        Binding("a", "auto_match", "auto-match", show=True),
        Binding("x", "reject", "reject match", show=True),
        Binding("m", "manual_match", "manual match", show=True),
        Binding("k", "mark_cleared", "mark cleared", show=True),
        Binding("f", "carry_forward", "carry-forward", show=True),
        Binding("c", "complete", "complete", show=True),
    ]

    DEFAULT_CSS = """
    ReconciliationDetailScreen { layout: vertical; }
    ReconciliationDetailScreen #rcd-header { height: auto; padding: 0 1; }
    ReconciliationDetailScreen #rcd-status { height: auto; padding: 0 1; color: $accent; }
    ReconciliationDetailScreen .group-header { height: auto; padding: 0 1; color: $accent; }
    ReconciliationDetailScreen #rcd-matches,
    ReconciliationDetailScreen #rcd-lines,
    ReconciliationDetailScreen #rcd-txs { height: 1fr; }
    ReconciliationDetailScreen #rcd-detail {
        height: auto;
        min-height: 4;
        padding: 1 2;
        border-top: solid $accent;
    }
    """

    def __init__(
        self,
        *,
        loader: ReconciliationDetailLoader,
        on_auto_match: AutoMatchAction,
        on_reject: RejectAction,
        on_manual_match: ManualMatchAction,
        on_paper_match: PaperMatchAction,
        on_carry_forward: CarryForwardAction,
        on_complete: CompleteAction,
    ) -> None:
        """Wire the per-action callbacks; the screen makes no HTTP calls itself."""
        super().__init__()
        self._loader = loader
        self._on_auto_match = on_auto_match
        self._on_reject = on_reject
        self._on_manual_match = on_manual_match
        self._on_paper_match = on_paper_match
        self._on_carry_forward = on_carry_forward
        self._on_complete = on_complete
        self._data: ReconciliationDetail | None = None
        self._matches_index: list[MatchSummary] = []
        self._lines_index: list[UnmatchedLine] = []
        self._txs_index: list[UnmatchedTransaction] = []
        self._notice: str = ""
        self._header: str = ""
        self._status: str = ""
        self._detail: str = ""

    def compose(self) -> ComposeResult:
        """Lay out header + three stacked tables + detail pane."""
        yield Header()
        with Vertical():
            yield Static("loading reconciliation…", id="rcd-header")
            yield Static("", id="rcd-status")
            yield Static("[b]Matches[/b]", classes="group-header", id="rcd-matches-header")
            yield DataTable(id="rcd-matches", zebra_stripes=True, cursor_type="row")
            yield Static("[b]Unmatched lines[/b]", classes="group-header", id="rcd-lines-header")
            yield DataTable(id="rcd-lines", zebra_stripes=True, cursor_type="row")
            yield Static(
                "[b]Unmatched transactions[/b]", classes="group-header", id="rcd-txs-header"
            )
            yield DataTable(id="rcd-txs", zebra_stripes=True, cursor_type="row")
            yield Static("", id="rcd-detail")
        yield Footer()

    def on_mount(self) -> None:
        """Install columns and trigger the initial load."""
        self.query_one("#rcd-matches", DataTable).add_columns(
            "Conf.", "Line", "Tx", "Amount", "Match"
        )
        self.query_one("#rcd-lines", DataTable).add_columns(
            "#", "Date", "Description", "Amount", "Id"
        )
        self.query_one("#rcd-txs", DataTable).add_columns(
            "Date", "Description", "Ref", "Status", "Id"
        )
        self._load()

    def action_refresh(self) -> None:
        """Re-run the loader and rebuild every table in place."""
        self._load()

    def action_auto_match(self) -> None:
        """``a`` — run the matcher (when no matches exist yet)."""
        if self._data is None:
            return
        if self._data.matches:
            self._set_notice("matches already exist — reject one with `x` to re-run")
            return
        try:
            result = self._on_auto_match()
        except Exception as exc:
            self._set_notice(f"[red]auto-match failed:[/red] {exc}")
            return
        created = result.get("matches_created") if isinstance(result, dict) else None
        if isinstance(created, int):
            self._set_notice(f"auto-matched — {created} new match(es)")
        else:
            self._set_notice("auto-matched")
        self._load()

    def action_reject(self) -> None:
        """``x`` — reject the highlighted match (matches table only)."""
        match = self._cursor_match()
        if match is None:
            self._set_notice("focus a match row (top table) before pressing `x`")
            return
        try:
            self._on_reject(match.id)
        except Exception as exc:
            self._set_notice(f"[red]reject failed:[/red] {exc}")
            return
        self._set_notice(f"rejected match {match.id[:8]}")
        self._load()

    def action_manual_match(self) -> None:
        """``m`` — open the picker modal to pair the focused line with a tx."""
        line = self._cursor_line()
        if line is None:
            self._set_notice("focus an unmatched line before pressing `m`")
            return
        if self._data is None or not self._data.unmatched_transactions:
            self._set_notice("no unmatched ledger transactions to pair")
            return

        modal = ManualMatchPickerModal(line=line, candidates=self._data.unmatched_transactions)
        self.app.push_screen(
            modal,
            lambda result: self._on_manual_match_chosen(line, result),
        )

    def action_mark_cleared(self) -> None:
        """``k`` — paper-match the highlighted unmatched tx (paper recons)."""
        tx = self._cursor_tx()
        if tx is None:
            self._set_notice("focus an unmatched transaction before pressing `k`")
            return
        if self._data is None or not self._data.is_paper:
            self._set_notice("mark-cleared is only valid on paper reconciliations")
            return
        try:
            self._on_paper_match(tx.id)
        except Exception as exc:
            self._set_notice(f"[red]mark-cleared failed:[/red] {exc}")
            return
        self._set_notice(f"marked cleared: {tx.description}")
        self._load()

    def action_carry_forward(self) -> None:
        """``f`` — carry the highlighted unmatched tx to the next reconciliation."""
        tx = self._cursor_tx()
        if tx is None:
            self._set_notice("focus an unmatched transaction before pressing `f`")
            return
        try:
            self._on_carry_forward(tx.id)
        except Exception as exc:
            self._set_notice(f"[red]carry-forward failed:[/red] {exc}")
            return
        self._set_notice(f"carried forward: {tx.description}")
        self._load()

    def action_complete(self) -> None:
        """``c`` — finalise the reconciliation (server checks balance)."""
        if self._data is None:
            return
        if self._data.envelope.status == "complete":
            self._set_notice("already complete")
            return
        try:
            self._on_complete()
        except Exception as exc:
            self._set_notice(f"[red]complete failed:[/red] {exc}")
            return
        self._set_notice("reconciliation complete")
        self._load()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Re-render the detail pane for the focused table's cursor."""
        widget = event.data_table
        widget_id = widget.id
        cursor = max(0, event.cursor_row)
        if widget_id == "rcd-matches" and cursor < len(self._matches_index):
            self._set_detail(_detail_for_match(self._matches_index[cursor]))
        elif widget_id == "rcd-lines" and cursor < len(self._lines_index):
            self._set_detail(_detail_for_line(self._lines_index[cursor]))
        elif widget_id == "rcd-txs" and cursor < len(self._txs_index):
            self._set_detail(_detail_for_tx(self._txs_index[cursor]))

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """When focus shifts between tables, re-render detail from that cursor."""
        widget_id = getattr(event.widget, "id", None)
        cursor = max(0, getattr(event.widget, "cursor_row", 0))
        if widget_id == "rcd-matches" and self._matches_index:
            cursor = min(cursor, len(self._matches_index) - 1)
            self._set_detail(_detail_for_match(self._matches_index[cursor]))
        elif widget_id == "rcd-lines" and self._lines_index:
            cursor = min(cursor, len(self._lines_index) - 1)
            self._set_detail(_detail_for_line(self._lines_index[cursor]))
        elif widget_id == "rcd-txs" and self._txs_index:
            cursor = min(cursor, len(self._txs_index) - 1)
            self._set_detail(_detail_for_tx(self._txs_index[cursor]))

    # -- internals -------------------------------------------------------

    def _load(self) -> None:
        try:
            data = self._loader()
        except Exception as exc:
            self._render_error(exc)
            return
        self._populate(data)

    def _populate(self, data: ReconciliationDetail) -> None:
        self._data = data
        m_table = self.query_one("#rcd-matches", DataTable)
        l_table = self.query_one("#rcd-lines", DataTable)
        t_table = self.query_one("#rcd-txs", DataTable)
        m_table.clear()
        l_table.clear()
        t_table.clear()
        self._matches_index = []
        self._lines_index = []
        self._txs_index = []

        env = data.envelope
        recon_kind = "paper" if data.is_paper else "imported"
        self._set_header(
            f"[b]{env.statement_period_start} → {env.statement_period_end}[/b]    "
            f"{env.statement_starting_balance} → {env.statement_ending_balance} "
            f"{env.currency}    status={env.status}    [{recon_kind}]"
        )
        self._set_status(
            f"{len(data.matches)} matches · "
            f"{len(data.unmatched_lines)} unmatched lines · "
            f"{len(data.unmatched_transactions)} unmatched txs"
            + (f"    [dim]{self._notice}[/dim]" if self._notice else "")
        )

        for m in data.matches:
            m_table.add_row(*_match_row(m))
            self._matches_index.append(m)
        for line in data.unmatched_lines:
            l_table.add_row(*_line_row(line))
            self._lines_index.append(line)
        for tx in data.unmatched_transactions:
            t_table.add_row(*_tx_row(tx))
            self._txs_index.append(tx)

        # Focus the first non-empty table so the cursor binds somewhere
        # sensible. Most useful default is the unmatched-lines table —
        # that's where most user actions originate.
        if data.unmatched_lines:
            l_table.focus()
        elif data.matches:
            m_table.focus()
        elif data.unmatched_transactions:
            t_table.focus()

    def _cursor_match(self) -> MatchSummary | None:
        if self.focused is None or self.focused.id != "rcd-matches":
            return None
        cursor = max(0, getattr(self.focused, "cursor_row", 0))
        if cursor >= len(self._matches_index):
            return None
        return self._matches_index[cursor]

    def _cursor_line(self) -> UnmatchedLine | None:
        if self.focused is None or self.focused.id != "rcd-lines":
            return None
        cursor = max(0, getattr(self.focused, "cursor_row", 0))
        if cursor >= len(self._lines_index):
            return None
        return self._lines_index[cursor]

    def _cursor_tx(self) -> UnmatchedTransaction | None:
        if self.focused is None or self.focused.id != "rcd-txs":
            return None
        cursor = max(0, getattr(self.focused, "cursor_row", 0))
        if cursor >= len(self._txs_index):
            return None
        return self._txs_index[cursor]

    def _on_manual_match_chosen(self, line: UnmatchedLine, tx_id: str | None) -> None:
        if tx_id is None:
            self._set_notice("manual match cancelled")
            return
        try:
            self._on_manual_match(
                line.id,
                tx_id,
                line.amount_display.replace(",", ""),
                line.currency,
            )
        except Exception as exc:
            self._set_notice(f"[red]manual match failed:[/red] {exc}")
            return
        self._set_notice(f"matched line {line.line_number} ↔ tx {tx_id[:8]}")
        self._load()

    def _render_error(self, exc: BaseException) -> None:
        for tid in ("#rcd-matches", "#rcd-lines", "#rcd-txs"):
            self.query_one(tid, DataTable).clear()
        self._matches_index = []
        self._lines_index = []
        self._txs_index = []
        self._data = None
        self._set_header("[red]error loading reconciliation[/red]")
        self._set_status(f"[red]error:[/red] {exc}")
        self._set_detail(f"error: {exc}")

    def _set_header(self, text: str) -> None:
        self._header = text
        self.query_one("#rcd-header", Static).update(text)

    def _set_status(self, text: str) -> None:
        self._status = text
        self.query_one("#rcd-status", Static).update(text)

    def _set_detail(self, text: str) -> None:
        self._detail = text
        self.query_one("#rcd-detail", Static).update(text)

    def _set_notice(self, text: str) -> None:
        self._notice = text
        # Re-render the status strip so the notice surfaces immediately.
        if self._data is not None:
            data = self._data
            self.query_one("#rcd-status", Static).update(
                f"{len(data.matches)} matches · "
                f"{len(data.unmatched_lines)} unmatched lines · "
                f"{len(data.unmatched_transactions)} unmatched txs"
                + (f"    [dim]{self._notice}[/dim]" if self._notice else "")
            )

    # -- introspection used by tests ------------------------------------

    def header_text(self) -> str:
        """Return the current header text."""
        return self._header

    def status_text(self) -> str:
        """Return the current status-strip text."""
        return self._status

    def detail_text(self) -> str:
        """Return the current detail-pane text."""
        return self._detail

    def notice(self) -> str:
        """Return the last action notice."""
        return self._notice


def _detail_for_match(m: MatchSummary) -> str:
    lines = [
        f"[b]match {m.id}[/b]",
        f"line:        {m.statement_line_id or '—'}",
        f"tx:          {m.ledger_transaction_id}",
        f"amount:      {m.match_amount} {m.currency}",
        f"confidence:  {m.confidence or ('manual' if m.is_manual else '—')}",
    ]
    return "\n".join(lines)


def _detail_for_line(line: UnmatchedLine) -> str:
    out = [
        f"[b]line {line.line_number}[/b]    {line.posted_date}",
        f"description: {line.description}",
        f"amount:      {line.amount_display} {line.currency}",
    ]
    if line.reference:
        out.append(f"reference:   {line.reference}")
    out.append(f"id:          {line.id}")
    return "\n".join(out)


def _detail_for_tx(tx: UnmatchedTransaction) -> str:
    out = [
        f"[b]{tx.date}[/b]    {tx.description}",
        f"status:      {tx.status}",
    ]
    if tx.reference:
        out.append(f"reference:   {tx.reference}")
    out.append(f"id:          {tx.id}")
    return "\n".join(out)

"""Import-batch detail + apply screen — P9.6.a of [#414](https://github.com/rmwarriner/tulip-accounting/issues/414).

Reachable with ``enter`` on a row in the imports browser. Renders the
batch header plus a per-line table with status markers
(promoted / excluded / pending) and offers three line-level actions:

- ``x`` toggles exclude / un-exclude (``PATCH /v1/imports/.../lines/{id}``)
- ``p`` promotes a single pending line
  (``POST /v1/imports/.../lines/{id}/promote``)
- ``a`` opens the apply confirm modal with three flag toggles
  (``--as-posted``, ``--no-categorize``, ``--treat-cleared-as-pending``).
  Confirm fires ``POST /v1/imports/{batch_id}/apply`` with the toggles
  as query params, then pops both screens back to the imports list.

Already-promoted lines refuse `x` and `p` (the API enforces this; the
screen rendering surfaces the "promoted" marker so the user knows).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Static, Switch

from tulip_tui.data.import_batch_detail import (
    ImportBatchDetail,
    StatementLineSummary,
)

ImportBatchDetailLoader = Callable[[], ImportBatchDetail]
LineExcludeToggle = Callable[[str, bool], None]
LinePromote = Callable[[str], None]
BatchApply = Callable[[bool, bool, bool], object]


def _status_marker(status: str) -> str:
    """Map status enum → display marker.  Kept here so tests can pin display."""
    return {"promoted": "✓", "excluded": "✗", "pending": "•"}.get(status, "?")


def _row_for(line: StatementLineSummary) -> tuple[str, str, str, str, str, str]:
    amount = f"{line.amount_display} {line.currency}".strip()
    return (
        str(line.line_number),
        line.date,
        line.description,
        amount,
        line.status,
        _status_marker(line.status),
    )


def _detail_for(line: StatementLineSummary) -> str:
    out = [
        f"[b]line {line.line_number}[/b]    {line.date}",
        f"description:  {line.description}",
        f"amount:       {line.amount_display} {line.currency}",
        f"status:       {line.status}",
    ]
    if line.promoted_transaction_id:
        out.append(f"transaction:  {line.promoted_transaction_id}")
    if line.reconciliation_match_id:
        out.append(f"matched:      {line.reconciliation_match_id}")
    out.append(f"id:           {line.id}")
    return "\n".join(out)


class ApplyConfirmModal(ModalScreen[tuple[bool, bool, bool] | None]):
    """Confirmation modal for ``POST /v1/imports/{id}/apply`` (P9.6.a).

    Renders the three apply-flag toggles. Returns the chosen flags on
    ``confirm``, or ``None`` if the user cancels. The caller fires the
    API call after the modal dismisses; keeping the network call out of
    the modal keeps it pilot-mode testable without a live API.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "cancel", show=True),
    ]

    DEFAULT_CSS = """
    ApplyConfirmModal {
        align: center middle;
    }

    ApplyConfirmModal #apply-modal {
        width: 70;
        max-width: 90%;
        height: auto;
        background: $panel;
        border: thick $primary;
        padding: 1 2;
    }

    ApplyConfirmModal .row {
        height: auto;
        margin: 1 0 0 0;
    }

    ApplyConfirmModal .toggle-label {
        width: 1fr;
        padding: 1 0 0 0;
    }

    ApplyConfirmModal #apply-buttons {
        align-horizontal: right;
        height: auto;
        margin-top: 1;
    }

    ApplyConfirmModal Button {
        margin-left: 2;
    }
    """

    def __init__(self, *, pending_count: int) -> None:
        """Track the count so the header is concrete (``Apply 12 lines?``)."""
        super().__init__()
        self._pending_count = pending_count

    def compose(self) -> ComposeResult:
        """Lay out title, three toggles, and the confirm/cancel buttons."""
        with Vertical(id="apply-modal"):
            yield Static(
                f"[b]Apply {self._pending_count} line(s) to the ledger?[/b]\n"
                "Excluded and already-promoted lines are skipped.",
                id="apply-title",
            )
            with Horizontal(classes="row"):
                yield Static("Land as POSTED (skip PENDING review)", classes="toggle-label")
                yield Switch(value=False, id="apply-as-posted")
            with Horizontal(classes="row"):
                yield Static("Skip AI categorizer (Imbalance:Unknown)", classes="toggle-label")
                yield Switch(value=False, id="apply-no-categorize")
            with Horizontal(classes="row"):
                yield Static("Force everything to PENDING", classes="toggle-label")
                yield Switch(value=False, id="apply-treat-cleared-as-pending")
            with Horizontal(id="apply-buttons"):
                yield Button("Cancel", id="apply-cancel")
                yield Button("Apply", variant="primary", id="apply-confirm")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dismiss with the toggle tuple on confirm, or ``None`` on cancel."""
        if event.button.id == "apply-confirm":
            self.dismiss(self._snapshot())
        elif event.button.id == "apply-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        """``escape`` key alias for the cancel button."""
        self.dismiss(None)

    def _snapshot(self) -> tuple[bool, bool, bool]:
        return (
            self.query_one("#apply-as-posted", Switch).value,
            self.query_one("#apply-no-categorize", Switch).value,
            self.query_one("#apply-treat-cleared-as-pending", Switch).value,
        )

    # -- test introspection ------------------------------------------------

    def snapshot_flags(self) -> tuple[bool, bool, bool]:
        """Pilot-mode helper — read the three switches without dismissing."""
        return self._snapshot()


class ImportBatchDetailScreen(Screen[None]):
    """Per-line review and apply for a parsed import batch (P9.6.a)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "back", show=True),
        Binding("r", "refresh", "refresh", show=True),
        Binding("x", "toggle_exclude", "exclude/un-exclude", show=True),
        Binding("p", "promote", "promote line", show=True),
        Binding("a", "open_apply", "apply…", show=True),
    ]

    DEFAULT_CSS = """
    ImportBatchDetailScreen {
        layout: vertical;
    }

    ImportBatchDetailScreen #ibd-header {
        height: auto;
        padding: 0 1;
    }

    ImportBatchDetailScreen #ibd-status {
        height: auto;
        padding: 0 1;
        color: $accent;
    }

    ImportBatchDetailScreen #ibd-table {
        height: 2fr;
    }

    ImportBatchDetailScreen #ibd-detail {
        height: 1fr;
        padding: 1 2;
        border-top: solid $accent;
    }
    """

    def __init__(
        self,
        *,
        loader: ImportBatchDetailLoader,
        on_toggle_exclude: LineExcludeToggle,
        on_promote: LinePromote,
        on_apply: BatchApply,
    ) -> None:
        """Wire the per-action callbacks; the screen does no network itself."""
        super().__init__()
        self._loader = loader
        self._on_toggle_exclude = on_toggle_exclude
        self._on_promote = on_promote
        self._on_apply = on_apply
        self._index: list[StatementLineSummary] = []
        self._rendered_rows: list[str] = []
        self._header: str = ""
        self._status: str = ""
        self._detail: str = ""
        self._batch: ImportBatchDetail | None = None
        self._notice: str = ""

    def compose(self) -> ComposeResult:
        """Lay out header / status strip / line table / detail pane."""
        yield Header()
        with Vertical():
            yield Static("loading batch…", id="ibd-header")
            yield Static("", id="ibd-status")
            yield DataTable(id="ibd-table", zebra_stripes=True, cursor_type="row")
            yield Static("", id="ibd-detail")
        yield Footer()

    def on_mount(self) -> None:
        """Install column headers and trigger the initial load."""
        table = self.query_one("#ibd-table", DataTable)
        table.add_columns("#", "Date", "Description", "Amount", "Status", "M")
        self._load()

    def action_refresh(self) -> None:
        """Re-run the loader and rebuild the table in place."""
        self._load()

    def action_toggle_exclude(self) -> None:
        """``x`` — flip ``is_excluded`` on the cursor line, then reload."""
        line = self._cursor_line()
        if line is None:
            return
        if line.status == "promoted":
            self._set_notice("cannot exclude a promoted line — edit/void the tx instead")
            return
        new_state = not line.is_excluded
        try:
            self._on_toggle_exclude(line.id, new_state)
        except Exception as exc:
            self._set_notice(f"[red]exclude failed:[/red] {exc}")
            return
        self._set_notice("excluded" if new_state else "un-excluded")
        self._load()

    def action_promote(self) -> None:
        """``p`` — promote the cursor line to a ledger transaction, then reload."""
        line = self._cursor_line()
        if line is None:
            return
        if line.status != "pending":
            self._set_notice(f"line is {line.status}; only pending lines can promote")
            return
        try:
            self._on_promote(line.id)
        except Exception as exc:
            self._set_notice(f"[red]promote failed:[/red] {exc}")
            return
        self._set_notice("promoted")
        self._load()

    def action_open_apply(self) -> None:
        """``a`` — push the apply confirm modal."""
        if self._batch is None:
            return
        pending = self._batch.pending_count
        if pending == 0:
            self._set_notice("nothing to apply — all lines are excluded or already promoted")
            return

        modal = ApplyConfirmModal(pending_count=pending)
        self.app.push_screen(modal, self._on_apply_modal_done)

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        """Re-render the detail pane to match the newly-highlighted row."""
        self._refresh_detail()

    # -- internals -----------------------------------------------------

    def _load(self) -> None:
        try:
            data = self._loader()
        except Exception as exc:
            self._render_error(exc)
            return
        self._populate(data)

    def _populate(self, data: ImportBatchDetail) -> None:
        self._batch = data
        table = self.query_one("#ibd-table", DataTable)
        table.clear()
        self._rendered_rows = []
        self._index = []
        header_text = (
            f"[b]{data.source_format.upper()}[/b] {data.source_filename}    "
            f"id={data.id[:8] if data.id else '—'}    status={data.status}"
        )
        self._set_header(header_text)
        self._set_status(
            f"{data.pending_count} pending · "
            f"{data.excluded_count} excluded · "
            f"{data.promoted_count} promoted"
            + (f"    [dim]{self._notice}[/dim]" if self._notice else "")
        )
        if not data.lines:
            self._set_detail("No statement lines.")
            return
        for line in data.lines:
            cells = _row_for(line)
            table.add_row(*cells)
            self._rendered_rows.append(" ".join(cells))
            self._index.append(line)
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        if not self._index:
            return
        table = self.query_one("#ibd-table", DataTable)
        cursor = max(0, table.cursor_row)
        if cursor >= len(self._index):
            return
        self._set_detail(_detail_for(self._index[cursor]))

    def _cursor_line(self) -> StatementLineSummary | None:
        if not self._index:
            return None
        table = self.query_one("#ibd-table", DataTable)
        cursor = max(0, table.cursor_row)
        if cursor >= len(self._index):
            return None
        return self._index[cursor]

    def _on_apply_modal_done(self, result: tuple[bool, bool, bool] | None) -> None:
        if result is None:
            self._set_notice("apply cancelled")
            self._refresh_status_with_notice()
            return
        as_posted, no_categorize, treat_cleared = result
        try:
            response = self._on_apply(as_posted, no_categorize, treat_cleared)
        except Exception as exc:
            self._set_notice(f"[red]apply failed:[/red] {exc}")
            self._refresh_status_with_notice()
            return
        # Show the created count when the API returned the standard body.
        created = response.get("created_count") if isinstance(response, dict) else None
        if isinstance(created, int):
            self._set_notice(f"applied — {created} transaction(s) created")
        else:
            self._set_notice("applied")
        self.app.pop_screen()

    def _render_error(self, exc: BaseException) -> None:
        table = self.query_one("#ibd-table", DataTable)
        table.clear()
        self._rendered_rows = []
        self._index = []
        self._batch = None
        self._set_header("[red]error loading batch[/red]")
        self._set_status(f"[red]error:[/red] {exc}")
        self._set_detail(f"error: {exc}")

    def _set_header(self, text: str) -> None:
        self._header = text
        self.query_one("#ibd-header", Static).update(text)

    def _set_status(self, text: str) -> None:
        self._status = text
        self.query_one("#ibd-status", Static).update(text)

    def _set_detail(self, text: str) -> None:
        self._detail = text
        self.query_one("#ibd-detail", Static).update(text)

    def _set_notice(self, text: str) -> None:
        self._notice = text

    def _refresh_status_with_notice(self) -> None:
        if self._batch is None:
            return
        data = self._batch
        self._set_status(
            f"{data.pending_count} pending · "
            f"{data.excluded_count} excluded · "
            f"{data.promoted_count} promoted"
            + (f"    [dim]{self._notice}[/dim]" if self._notice else "")
        )

    # -- introspection used by tests ----------------------------------

    def rendered_rows(self) -> list[str]:
        """Return a string-only mirror of the table's rows for assertions."""
        return list(self._rendered_rows)

    def header_text(self) -> str:
        """Return the current header text as plain string."""
        return self._header

    def status_text(self) -> str:
        """Return the current status-strip text as a plain string."""
        return self._status

    def detail_text(self) -> str:
        """Return the current detail-pane text as a plain string."""
        return self._detail

    def notice(self) -> str:
        """Return the last action notice (set by exclude/promote/apply)."""
        return self._notice

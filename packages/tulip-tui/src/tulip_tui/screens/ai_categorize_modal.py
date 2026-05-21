"""AI categorize proposal modal — #425 (P9.6.d).

Reached from the transactions register: ``c`` on a PENDING transaction
fetches up to N proposals from the categorize endpoint and renders
the top-1 prominent with alternates listed below. Confirmed pick
fires a PATCH against the transaction to re-target the non-bank
posting's ``account_id``.

Mirrors the P9.6.c modal pattern: HTTP stays out of the modal, the
caller fires the action after the modal dismisses with a chosen
``account_code``.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from tulip_tui.data.ai_categorize import AIProposalCandidate


class AICategorizeProposalModal(ModalScreen[str | None]):
    """Pick a category from the AI's ranked proposals (#425).

    Dismisses with the selected ``account_code`` on confirm, ``None``
    on cancel. ``a`` / ``enter`` confirms the row under the cursor;
    ``escape`` cancels.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "cancel", show=True),
        Binding("a", "accept", "accept", show=True),
        Binding("enter", "accept", "accept", show=False),
    ]

    DEFAULT_CSS = """
    AICategorizeProposalModal {
        align: center middle;
    }
    AICategorizeProposalModal #ai-cat-form {
        width: 100;
        max-width: 95%;
        height: auto;
        background: $panel;
        border: thick $primary;
        padding: 1 2;
    }
    AICategorizeProposalModal #ai-cat-top {
        height: auto;
        padding: 0 1 1 1;
    }
    AICategorizeProposalModal #ai-cat-table {
        height: 12;
    }
    AICategorizeProposalModal #ai-cat-hint {
        height: auto;
        color: $text-muted;
        padding: 1 1 0 1;
    }
    """

    def __init__(
        self,
        *,
        description: str,
        candidates: tuple[AIProposalCandidate, ...],
    ) -> None:
        """Store the candidates; modal renders on mount."""
        super().__init__()
        self._description = description
        self._candidates = candidates
        self._selected_code: str | None = None

    def compose(self) -> ComposeResult:
        """Render top-1 prominently + a table of alternates."""
        with Vertical(id="ai-cat-form"):
            yield Static(
                f"[b]AI proposal for[/b] {self._description}",
                id="ai-cat-title",
            )
            if not self._candidates:
                yield Static(
                    "[yellow]No proposals returned — check AI policy / "
                    "provider key, or use the manual picker.[/yellow]",
                    id="ai-cat-top",
                )
            else:
                top = self._candidates[0]
                pct = int(top.confidence * 100)
                reason = f" — {top.reasoning}" if top.reasoning else ""
                yield Static(
                    f"[b green]{top.account_code}[/b green] ([dim]{pct}% confident[/dim]){reason}",
                    id="ai-cat-top",
                )
            yield DataTable(id="ai-cat-table", zebra_stripes=True, cursor_type="row")
            yield Static(
                "[dim]a / enter[/dim] accept · [dim]escape[/dim] cancel",
                id="ai-cat-hint",
            )

    def on_mount(self) -> None:
        """Populate the alternates table with every candidate."""
        table = self.query_one("#ai-cat-table", DataTable)
        table.add_columns("Code", "Confidence", "Reasoning")
        for c in self._candidates:
            pct = f"{int(c.confidence * 100)}%"
            reasoning = c.reasoning or "—"
            table.add_row(c.account_code, pct, reasoning)
        if self._candidates:
            table.cursor_coordinate = Coordinate(0, 0)
            table.focus()

    def action_cancel(self) -> None:
        """``escape`` cancels."""
        self.dismiss(None)

    def action_accept(self) -> None:
        """``a`` / ``enter`` accepts the row under the cursor."""
        if not self._candidates:
            self.dismiss(None)
            return
        table = self.query_one("#ai-cat-table", DataTable)
        idx = max(0, table.cursor_row)
        if idx >= len(self._candidates):
            idx = 0
        self.dismiss(self._candidates[idx].account_code)

    # -- pilot-mode introspection -----------------------------------------

    def snapshot_candidates(self) -> tuple[AIProposalCandidate, ...]:
        """Return the candidates the modal is rendering."""
        return self._candidates

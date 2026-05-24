"""Transaction add/edit/void modals — P9.6.c of [#414](https://github.com/rmwarriner/tulip-accounting/issues/414).

Reached from the transactions register:

- ``n`` opens :class:`TransactionEditModal` with empty defaults
  (today's date as the date prefill, blank everything else).
- ``e`` opens the same modal pre-filled from the focused tx — only
  PENDING transactions are editable in-place; POSTED/RECONCILED
  fall back to "void the source and add a new one" on the CLI.
- ``x`` opens :class:`VoidConfirmModal`. The user supplies a
  reason; the modal returns it for the caller to POST.

The modal returns a draft on confirm or ``None`` on cancel; the
caller fires the API call after dismissal. Keeping HTTP out of the
modal keeps it pilot-mode testable without a live API.

Posting input uses the same ``account=amount[@CUR]`` syntax as
``tulip add --post``: users have one mental model for both
surfaces.
"""

from __future__ import annotations

from datetime import UTC, datetime
from datetime import date as date_type
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, TextArea

from tulip_tui.data.transaction_write import (
    TransactionDraft,
    parse_postings_block,
)


def _parse_tags(raw: str) -> tuple[str, ...]:
    """Parse a comma-separated tag string into a normalised tuple.

    Each element is stripped and lowercased; blanks and duplicates are
    dropped. Order is preserved (the API will alpha-sort on write).
    """
    seen: list[str] = []
    for part in raw.split(","):
        t = part.strip().lower()
        if t and t not in seen:
            seen.append(t)
    return tuple(seen)


def _today() -> str:
    """Return today's date as ISO-8601 (UTC), per the QUICKSTART convention."""
    return datetime.now(UTC).date().isoformat()


def _validate_iso_date(value: str) -> str | None:
    """Return an error message if ``value`` isn't ``YYYY-MM-DD``, else ``None``."""
    try:
        date_type.fromisoformat(value)
    except ValueError:
        return f"date must be YYYY-MM-DD (got {value!r})"
    return None


class TransactionEditModal(ModalScreen[TransactionDraft | None]):
    """Modal form for ``tulip add`` / PENDING-tx edit (P9.6.c).

    Returns a parsed :class:`TransactionDraft` on confirm or ``None``
    on cancel. The form validates locally (date shape, posting
    syntax) before dismissing; surface inline errors instead of
    closing on bad input.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "cancel", show=True),
    ]

    DEFAULT_CSS = """
    TransactionEditModal {
        align: center middle;
    }
    TransactionEditModal #tx-form {
        width: 90;
        max-width: 95%;
        height: auto;
        background: $panel;
        border: thick $primary;
        padding: 1 2;
    }
    TransactionEditModal .label {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    TransactionEditModal Input {
        margin: 0 0 1 0;
    }
    TransactionEditModal TextArea#tx-postings {
        height: 8;
        margin: 0 0 1 0;
    }
    TransactionEditModal #tx-error {
        height: auto;
        color: $error;
        padding: 0 1;
    }
    TransactionEditModal #tx-buttons {
        align-horizontal: right;
        height: auto;
    }
    TransactionEditModal Button {
        margin-left: 2;
    }
    """

    def __init__(
        self,
        *,
        title: str = "Add transaction",
        initial_date: str | None = None,
        initial_description: str = "",
        initial_reference: str = "",
        initial_postings: str = "",
        initial_tags: tuple[str, ...] = (),
    ) -> None:
        """Store the prefill values; ``initial_date`` defaults to today (UTC)."""
        super().__init__()
        self._title = title
        self._initial_date = initial_date or _today()
        self._initial_description = initial_description
        self._initial_reference = initial_reference
        self._initial_postings = initial_postings or (
            "# one posting per line — e.g.\n# 1110=-12.50\n# 5100=12.50\n"
        )
        self._initial_tags = ", ".join(initial_tags)
        self._error: str = ""

    def compose(self) -> ComposeResult:
        """Lay out the form fields, the inline error pane, and confirm/cancel."""
        with Vertical(id="tx-form"):
            yield Static(f"[b]{self._title}[/b]", id="tx-title")
            yield Static("date (YYYY-MM-DD)", classes="label")
            yield Input(value=self._initial_date, id="tx-date")
            yield Static("description", classes="label")
            yield Input(value=self._initial_description, id="tx-description")
            yield Static("reference (optional)", classes="label")
            yield Input(value=self._initial_reference, id="tx-reference")
            yield Static("postings (one per line: account=amount[@CUR])", classes="label")
            yield TextArea(text=self._initial_postings, id="tx-postings")
            yield Static("tags (optional, comma-separated)", classes="label")
            yield Input(value=self._initial_tags, id="tx-tags")
            yield Static("", id="tx-error")
            with Horizontal(id="tx-buttons"):
                yield Button("Cancel", id="tx-cancel")
                yield Button("Save", variant="primary", id="tx-save")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Validate on save; dismiss with ``None`` on cancel."""
        if event.button.id == "tx-cancel":
            self.dismiss(None)
        elif event.button.id == "tx-save":
            draft = self._build_draft()
            if draft is not None:
                self.dismiss(draft)

    def action_cancel(self) -> None:
        """``escape`` cancels the modal."""
        self.dismiss(None)

    def _build_draft(self) -> TransactionDraft | None:
        date_val = self.query_one("#tx-date", Input).value.strip()
        description = self.query_one("#tx-description", Input).value.strip()
        reference = self.query_one("#tx-reference", Input).value.strip()
        postings_text = self.query_one("#tx-postings", TextArea).text

        if not date_val:
            self._set_error("date is required")
            return None
        err = _validate_iso_date(date_val)
        if err:
            self._set_error(err)
            return None
        if not description:
            self._set_error("description is required")
            return None

        try:
            postings = parse_postings_block(postings_text)
        except ValueError as exc:
            self._set_error(str(exc))
            return None

        tags = _parse_tags(self.query_one("#tx-tags", Input).value)
        return TransactionDraft(
            date=date_val,
            description=description,
            reference=reference or None,
            postings=postings,
            tags=tags,
        )

    def _set_error(self, msg: str) -> None:
        self._error = msg
        self.query_one("#tx-error", Static).update(f"[red]{msg}[/red]")

    # -- test introspection ------------------------------------------------

    def error_text(self) -> str:
        """Return the last error message rendered in the inline pane."""
        return self._error

    def snapshot(self) -> TransactionDraft | None:
        """Pilot-mode helper — read the current form state without dismissing."""
        return self._build_draft()


class VoidConfirmModal(ModalScreen[str | None]):
    """Confirmation modal for ``POST /v1/transactions/{id}/void``.

    Returns the user-supplied reason on confirm or ``None`` on cancel.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "cancel", show=True),
    ]

    DEFAULT_CSS = """
    VoidConfirmModal {
        align: center middle;
    }
    VoidConfirmModal #void-modal {
        width: 70;
        max-width: 90%;
        height: auto;
        background: $panel;
        border: thick $primary;
        padding: 1 2;
    }
    VoidConfirmModal .label {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    VoidConfirmModal Input {
        margin: 0 0 1 0;
    }
    VoidConfirmModal #void-error {
        height: auto;
        color: $error;
        padding: 0 1;
    }
    VoidConfirmModal #void-buttons {
        align-horizontal: right;
        height: auto;
    }
    VoidConfirmModal Button {
        margin-left: 2;
    }
    """

    def __init__(self, *, tx_id: str, description: str) -> None:
        """Store the tx ref so the prompt is concrete (``Void <desc> (<id>)?``)."""
        super().__init__()
        self._tx_id = tx_id
        self._description = description
        self._error: str = ""

    def compose(self) -> ComposeResult:
        """Lay out the prompt, reason input, error pane, and confirm/cancel."""
        with Vertical(id="void-modal"):
            yield Static(
                f"[b]Void transaction?[/b]\n{self._description}    [{self._tx_id[:8]}]",
                id="void-title",
            )
            yield Static("reason (required)", classes="label")
            yield Input(value="", id="void-reason")
            yield Static("", id="void-error")
            with Horizontal(id="void-buttons"):
                yield Button("Cancel", id="void-cancel")
                yield Button("Void", variant="error", id="void-confirm")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Validate on confirm; dismiss with ``None`` on cancel."""
        if event.button.id == "void-cancel":
            self.dismiss(None)
        elif event.button.id == "void-confirm":
            reason = self.query_one("#void-reason", Input).value.strip()
            if not reason:
                self._error = "reason is required"
                self.query_one("#void-error", Static).update(f"[red]{self._error}[/red]")
                return
            self.dismiss(reason)

    def action_cancel(self) -> None:
        """``escape`` cancels."""
        self.dismiss(None)

    # -- test introspection ------------------------------------------------

    def error_text(self) -> str:
        """Return the last error message rendered."""
        return self._error

"""Account add / edit modal — #431 (post-P9.6 polish).

Reached from the accounts browser:

- ``n`` opens :class:`AccountEditModal` with empty defaults
  (currency prefilled from the user's first existing account so
  multi-currency households don't need to retype it).
- ``e`` opens the same modal pre-filled from the focused account.

Mirror of the P9.6.c pattern: the modal validates locally and
surfaces errors inline; the caller fires the API call after the
modal dismisses with a draft. Keeping HTTP out of the modal keeps
it pilot-mode testable without a live API.
"""

from __future__ import annotations

import re
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, Switch

from tulip_tui.data.account_write import AccountDraft

_TYPE_RE = re.compile(r"^(asset|liability|equity|income|expense)$")
_CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")


def _parse_tags(raw: str) -> tuple[str, ...]:
    """Parse a comma-separated tag string into a normalised tuple."""
    seen: list[str] = []
    for part in raw.split(","):
        t = part.strip().lower()
        if t and t not in seen:
            seen.append(t)
    return tuple(seen)


class AccountEditModal(ModalScreen[AccountDraft | None]):
    """Modal form for ``tulip accounts add`` / ``edit`` (#431)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "cancel", show=True),
    ]

    DEFAULT_CSS = """
    AccountEditModal {
        align: center middle;
    }
    AccountEditModal #acct-form {
        width: 80;
        max-width: 95%;
        height: auto;
        background: $panel;
        border: thick $primary;
        padding: 1 2;
    }
    AccountEditModal .label {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    AccountEditModal Input {
        margin: 0 0 1 0;
    }
    AccountEditModal #acct-visibility-row {
        height: auto;
        margin: 0 0 1 0;
    }
    AccountEditModal #acct-visibility-row Static {
        width: 1fr;
        padding: 1 0 0 0;
    }
    AccountEditModal #acct-error {
        height: auto;
        color: $error;
        padding: 0 1;
    }
    AccountEditModal #acct-buttons {
        align-horizontal: right;
        height: auto;
    }
    AccountEditModal Button {
        margin-left: 2;
    }
    """

    def __init__(
        self,
        *,
        title: str = "Add account",
        initial_name: str = "",
        initial_type: str = "asset",
        initial_currency: str = "USD",
        initial_code: str = "",
        initial_subtype: str = "",
        initial_visibility: str = "shared",
        initial_parent_id: str | None = None,
        initial_notes: str = "",
        initial_placeholder: bool = False,
        initial_tags: tuple[str, ...] = (),
    ) -> None:
        """Store prefill values; modal renders on mount."""
        super().__init__()
        self._title = title
        self._initial_name = initial_name
        self._initial_type = initial_type
        self._initial_currency = initial_currency
        self._initial_code = initial_code
        self._initial_subtype = initial_subtype
        self._initial_visibility = initial_visibility
        self._initial_parent_id = initial_parent_id
        self._initial_notes = initial_notes
        self._initial_placeholder = initial_placeholder
        self._initial_tags = ", ".join(initial_tags)
        self._error: str = ""

    def compose(self) -> ComposeResult:
        """Lay out form fields, inline error pane, and confirm/cancel."""
        with Vertical(id="acct-form"):
            yield Static(f"[b]{self._title}[/b]", id="acct-title")
            yield Static("name (required)", classes="label")
            yield Input(value=self._initial_name, id="acct-name")
            yield Static(
                "type (asset / liability / equity / income / expense)",
                classes="label",
            )
            yield Input(value=self._initial_type, id="acct-type")
            yield Static("currency (3-char ISO; e.g. USD)", classes="label")
            yield Input(value=self._initial_currency, id="acct-currency")
            yield Static("code (optional, e.g. 1110)", classes="label")
            yield Input(value=self._initial_code, id="acct-code")
            yield Static("subtype (optional, e.g. bank / credit_card)", classes="label")
            yield Input(value=self._initial_subtype, id="acct-subtype")
            with Horizontal(id="acct-visibility-row"):
                yield Static("private (admins only)?", classes="label")
                yield Switch(
                    value=self._initial_visibility == "private",
                    id="acct-private",
                )
            with Horizontal(id="acct-placeholder-row"):
                yield Static(
                    "placeholder (organisational; rejects postings)?",
                    classes="label",
                )
                yield Switch(
                    value=self._initial_placeholder,
                    id="acct-placeholder",
                )
            yield Static("parent account id (optional)", classes="label")
            yield Input(
                value=self._initial_parent_id or "",
                id="acct-parent-id",
            )
            yield Static("notes (optional, encrypted at rest)", classes="label")
            yield Input(value=self._initial_notes, id="acct-notes")
            yield Static("tags (optional, comma-separated)", classes="label")
            yield Input(value=self._initial_tags, id="acct-tags")
            yield Static("", id="acct-error")
            with Horizontal(id="acct-buttons"):
                yield Button("Cancel", id="acct-cancel")
                yield Button("Save", variant="primary", id="acct-save")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Validate on save; dismiss with ``None`` on cancel."""
        if event.button.id == "acct-cancel":
            self.dismiss(None)
        elif event.button.id == "acct-save":
            draft = self._build_draft()
            if draft is not None:
                self.dismiss(draft)

    def action_cancel(self) -> None:
        """``escape`` cancels the modal."""
        self.dismiss(None)

    def _build_draft(self) -> AccountDraft | None:
        name = self.query_one("#acct-name", Input).value.strip()
        type_ = self.query_one("#acct-type", Input).value.strip().lower()
        currency = self.query_one("#acct-currency", Input).value.strip().upper()
        code = self.query_one("#acct-code", Input).value.strip() or None
        subtype = self.query_one("#acct-subtype", Input).value.strip() or None
        visibility = "private" if self.query_one("#acct-private", Switch).value else "shared"
        is_placeholder = self.query_one("#acct-placeholder", Switch).value
        parent_id_raw = self.query_one("#acct-parent-id", Input).value.strip()
        parent_id = parent_id_raw or None
        notes = self.query_one("#acct-notes", Input).value.strip() or None
        tags = _parse_tags(self.query_one("#acct-tags", Input).value)

        if not name:
            self._set_error("name is required")
            return None
        if not _TYPE_RE.match(type_):
            self._set_error("type must be one of asset / liability / equity / income / expense")
            return None
        if not _CURRENCY_RE.match(currency):
            self._set_error("currency must be a 3-letter ISO code (e.g. USD)")
            return None

        return AccountDraft(
            name=name,
            type=type_,
            currency=currency,
            code=code,
            subtype=subtype,
            visibility=visibility,
            parent_account_id=parent_id,
            notes=notes,
            is_placeholder=is_placeholder,
            tags=tags,
        )

    def _set_error(self, msg: str) -> None:
        self._error = msg
        self.query_one("#acct-error", Static).update(f"[red]{msg}[/red]")

    # -- test introspection -----------------------------------------------

    def error_text(self) -> str:
        """Return the last error message rendered."""
        return self._error

    def snapshot(self) -> AccountDraft | None:
        """Pilot-mode helper — read the form state without dismissing."""
        return self._build_draft()

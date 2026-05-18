"""Pure decision logic for ``tulip transactions edit`` (#209b).

The CLI's ``edit_transaction`` command branches on:

* the transaction's current status (PENDING / POSTED / RECONCILED),
* the CLI's ``--json`` mode (machine-readable; cannot prompt),
* whether the caller passed ``--force-reconciled-edit`` (explicit
  automation opt-in),
* an in-process session flag (``[S]`` answer earlier this session),
* a persisted preference (``[A]`` answer in a past session →
  ``reconciled_edit_confirm == "never_ask"``).

Factoring the matrix out of the command body keeps the wiring
testable without an editor, an httpx client, or terminal stubs.
"""

from __future__ import annotations

from enum import Enum
from typing import Final, Literal


class EditAction(Enum):
    """What ``tulip transactions edit`` should do once the user saves the editor."""

    #: PENDING transactions: PATCH /v1/transactions/{id} (existing path).
    PATCH = "patch"

    #: POSTED transactions: POST /v1/transactions/{id}/replace, no prompt.
    REPLACE_SILENT = "replace_silent"

    #: RECONCILED transactions: caller has already opted in (session flag,
    #: persisted ``never_ask``, or ``--force-reconciled-edit``). Go to
    #: /replace without prompting.
    REPLACE_AFTER_PROMPT = "replace_after_prompt"

    #: RECONCILED transactions at an interactive TTY without prior
    #: opt-in: caller must prompt the user (Y/N/S/A) and call back with
    #: the answer threaded through ``session_confirmed`` /
    #: ``persisted_pref``.
    PROMPT_REQUIRED = "prompt_required"

    #: RECONCILED transactions in ``--json`` mode without
    #: ``--force-reconciled-edit``: fail with a Problem Detail telling
    #: the caller to pass the flag (or set ``never_ask`` once at a TTY).
    REJECT_JSON_MODE = "reject_json_mode"


_VALID_STATUSES: Final[frozenset[str]] = frozenset({"pending", "posted", "reconciled"})


def decide_edit_action(
    *,
    status: str,
    json_mode: bool,
    force: bool,
    session_confirmed: bool,
    persisted_pref: Literal["ask", "never_ask"],
) -> EditAction:
    """Resolve the edit-flow outcome for the given inputs.

    Raises ``ValueError`` on an unexpected status — the caller should
    have validated against the API's known set before calling.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(
            f"unexpected transaction status {status!r}; expected one of {sorted(_VALID_STATUSES)}"
        )

    if status == "pending":
        return EditAction.PATCH
    if status == "posted":
        return EditAction.REPLACE_SILENT

    # status == "reconciled"
    opted_in = force or session_confirmed or persisted_pref == "never_ask"
    if opted_in:
        return EditAction.REPLACE_AFTER_PROMPT
    if json_mode:
        return EditAction.REJECT_JSON_MODE
    return EditAction.PROMPT_REQUIRED


__all__: list[str] = ["EditAction", "decide_edit_action"]

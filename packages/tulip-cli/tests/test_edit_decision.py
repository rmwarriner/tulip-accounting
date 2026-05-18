"""Unit tests for the ``tulip transactions edit`` decision matrix (#209b).

The decision function maps ``(status, json_mode, force, session_flag,
persisted_pref)`` to one of:

* ``"patch"`` — PENDING edits stay on the existing PATCH path.
* ``"replace_silent"`` — POSTED edits go to /replace without a prompt.
* ``"replace_after_prompt"`` — RECONCILED edits go to /replace iff the
  user (or a previous session) opted in; otherwise the caller prompts.
* ``"prompt_required"`` — RECONCILED edit at an interactive TTY: ask
  Y/N/S/A; the caller handles the prompt then re-decides.
* ``"reject_json_mode"`` — RECONCILED edit in ``--json`` mode without
  ``--force-reconciled-edit``: surface a Problem Detail telling the
  caller to opt in explicitly.

The function is pure and dependency-free; the command-level test
exercises the full edit → /replace round-trip separately.
"""

from __future__ import annotations

import pytest

from tulip_cli.commands._edit_decision import EditAction, decide_edit_action


def test_pending_always_goes_to_patch() -> None:
    """PENDING transactions take the existing in-place PATCH path."""
    action = decide_edit_action(
        status="pending",
        json_mode=False,
        force=False,
        session_confirmed=False,
        persisted_pref="ask",
    )
    assert action == EditAction.PATCH


def test_pending_unaffected_by_json_or_force() -> None:
    """No combination of flags changes the PENDING path."""
    for json_mode in (False, True):
        for force in (False, True):
            assert (
                decide_edit_action(
                    status="pending",
                    json_mode=json_mode,
                    force=force,
                    session_confirmed=False,
                    persisted_pref="ask",
                )
                == EditAction.PATCH
            )


def test_posted_goes_silently_to_replace() -> None:
    """POSTED edits transparently void+recreate — no prompt, no friction."""
    action = decide_edit_action(
        status="posted",
        json_mode=False,
        force=False,
        session_confirmed=False,
        persisted_pref="ask",
    )
    assert action == EditAction.REPLACE_SILENT


def test_posted_in_json_mode_still_silent() -> None:
    """``--json`` does not gate POSTED edits — only RECONCILED ones."""
    action = decide_edit_action(
        status="posted",
        json_mode=True,
        force=False,
        session_confirmed=False,
        persisted_pref="ask",
    )
    assert action == EditAction.REPLACE_SILENT


def test_reconciled_interactive_default_prompts() -> None:
    """RECONCILED + interactive + default prefs → caller must prompt."""
    action = decide_edit_action(
        status="reconciled",
        json_mode=False,
        force=False,
        session_confirmed=False,
        persisted_pref="ask",
    )
    assert action == EditAction.PROMPT_REQUIRED


def test_reconciled_with_session_confirmation_skips_prompt() -> None:
    """In-session ``[S]`` opt-in skips the prompt for the rest of the process."""
    action = decide_edit_action(
        status="reconciled",
        json_mode=False,
        force=False,
        session_confirmed=True,
        persisted_pref="ask",
    )
    assert action == EditAction.REPLACE_AFTER_PROMPT


def test_reconciled_with_persisted_never_ask_skips_prompt() -> None:
    """Persisted ``[A]lways`` opt-in skips the prompt across sessions."""
    action = decide_edit_action(
        status="reconciled",
        json_mode=False,
        force=False,
        session_confirmed=False,
        persisted_pref="never_ask",
    )
    assert action == EditAction.REPLACE_AFTER_PROMPT


def test_reconciled_with_force_flag_skips_prompt() -> None:
    """``--force-reconciled-edit`` is the explicit machine-friendly opt-in."""
    action = decide_edit_action(
        status="reconciled",
        json_mode=False,
        force=True,
        session_confirmed=False,
        persisted_pref="ask",
    )
    assert action == EditAction.REPLACE_AFTER_PROMPT


def test_reconciled_json_mode_without_force_rejects() -> None:
    """``--json`` + RECONCILED without ``--force`` fails fast with a Problem Detail."""
    action = decide_edit_action(
        status="reconciled",
        json_mode=True,
        force=False,
        session_confirmed=False,
        persisted_pref="ask",
    )
    assert action == EditAction.REJECT_JSON_MODE


def test_reconciled_json_mode_with_force_replaces() -> None:
    """``--json --force-reconciled-edit`` is the supported automation path."""
    action = decide_edit_action(
        status="reconciled",
        json_mode=True,
        force=True,
        session_confirmed=False,
        persisted_pref="ask",
    )
    assert action == EditAction.REPLACE_AFTER_PROMPT


def test_reconciled_json_mode_with_persisted_never_ask_replaces() -> None:
    """``never_ask`` is interpreted as a standing opt-in even in ``--json``."""
    action = decide_edit_action(
        status="reconciled",
        json_mode=True,
        force=False,
        session_confirmed=False,
        persisted_pref="never_ask",
    )
    assert action == EditAction.REPLACE_AFTER_PROMPT


def test_unknown_status_raises() -> None:
    """An unexpected status (e.g. ``voided``) is a programmer error → raise."""
    with pytest.raises(ValueError, match="status"):
        decide_edit_action(
            status="voided",
            json_mode=False,
            force=False,
            session_confirmed=False,
            persisted_pref="ask",
        )

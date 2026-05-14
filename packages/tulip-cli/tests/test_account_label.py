"""Unit tests for ``_format_account_label`` (closes #214).

The integration coverage in ``test_p36_read_edit.py`` asserts the
happy path on a live API — accounts that have both ``code`` and
``name``. Here we cover the no-code and orphaned-id fallback
branches without spawning the API:

* both code + name → ``<code>:<name>``
* name only       → ``<name>``
* id not present in the resolver dict → the raw UUID string as
  a graceful fallback (per the issue's acceptance criterion: an
  orphaned posting shouldn't crash the renderer)
"""

from __future__ import annotations

from tulip_cli.commands.transactions import _format_account_label


def test_format_account_label_uses_code_and_name() -> None:
    accounts = {"acct-1": {"id": "acct-1", "code": "5100", "name": "Groceries"}}
    assert _format_account_label(accounts, "acct-1") == "5100:Groceries"


def test_format_account_label_falls_back_to_name_when_no_code() -> None:
    accounts = {
        "acct-1": {"id": "acct-1", "code": None, "name": "Imbalance:Unknown"},
    }
    assert _format_account_label(accounts, "acct-1") == "Imbalance:Unknown"


def test_format_account_label_falls_back_to_uuid_when_account_missing() -> None:
    # An orphaned posting (account_id not present in the household's chart —
    # shouldn't happen, but the issue spells it out as a graceful-degrade
    # requirement) renders the raw UUID rather than raising.
    assert (
        _format_account_label({}, "00000000-0000-0000-0000-000000000000")
        == "00000000-0000-0000-0000-000000000000"
    )


def test_format_account_label_empty_account_id_renders_empty_string() -> None:
    # Defensive: the renderer shouldn't crash if a posting carries no id.
    assert _format_account_label({}, "") == ""

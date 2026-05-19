"""Reports-side path walker mirrors the CLI helper (#300).

The two packages can't share a module (architecture-boundary rules
forbid it), so this test pins the behavioural contract that both
implementations must satisfy. If the CLI side diverges, fix one or
the other so the user sees identical paths everywhere.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from tulip_reports._account_path import account_path


def _ns(**kwargs: object) -> SimpleNamespace:
    """Lightweight Account stand-in; the helper only reads .id/.name/.type/.parent_account_id."""
    return SimpleNamespace(**kwargs)


def test_walks_chain_to_root_with_title_case_type() -> None:
    root_id, mid_id, leaf_id = uuid4(), uuid4(), uuid4()
    type_obj = SimpleNamespace(value="asset")
    chart = {
        root_id: _ns(id=root_id, name="Assets", type=type_obj, parent_account_id=None),
        mid_id: _ns(id=mid_id, name="Current Assets", type=type_obj, parent_account_id=root_id),
        leaf_id: _ns(id=leaf_id, name="Checking", type=type_obj, parent_account_id=mid_id),
    }
    assert account_path(leaf_id, chart) == "Asset:Assets:Current Assets:Checking"


def test_missing_account_renders_raw_uuid() -> None:
    uid = uuid4()
    assert account_path(uid, {}) == str(uid)


def test_missing_parent_renders_question_mark() -> None:
    leaf_id = uuid4()
    orphan_parent_id = uuid4()
    chart = {
        leaf_id: _ns(
            id=leaf_id,
            name="Checking",
            type=SimpleNamespace(value="asset"),
            parent_account_id=orphan_parent_id,
        )
    }
    assert account_path(leaf_id, chart) == "Asset:?:Checking"


def test_name_with_colon_is_escaped() -> None:
    uid = uuid4()
    chart = {
        uid: _ns(
            id=uid,
            name="Imbalance:Unknown",
            type=SimpleNamespace(value="equity"),
            parent_account_id=None,
        )
    }
    assert account_path(uid, chart) == r"Equity:Imbalance\:Unknown"


def test_name_with_backslash_is_escaped() -> None:
    uid = uuid4()
    chart = {
        uid: _ns(
            id=uid,
            name=r"foo\bar",
            type=SimpleNamespace(value="asset"),
            parent_account_id=None,
        )
    }
    assert account_path(uid, chart) == r"Asset:foo\\bar"


def test_cycle_is_broken() -> None:
    uid = uuid4()
    chart = {
        uid: _ns(
            id=uid,
            name="loop",
            type=SimpleNamespace(value="asset"),
            parent_account_id=uid,
        )
    }
    assert account_path(uid, chart) == "Asset:loop"


def test_string_type_value_is_accepted() -> None:
    # Defensive: some callers may pass a plain string instead of an
    # enum-wrapped type. Helper handles both shapes.
    uid = uuid4()
    chart = {
        uid: _ns(id=uid, name="Sample", type="liability", parent_account_id=None),
    }
    assert account_path(uid, chart) == "Liability:Sample"

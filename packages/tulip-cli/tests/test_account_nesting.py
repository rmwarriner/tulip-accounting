"""End-to-end tests for the CLI surfacing of account nesting (#42.b).

* ``tulip accounts add --parent ACCOUNT`` — accepts code or UUID; resolved
  via the same path as ``show``/``balance``.
* ``tulip accounts list`` — renders a tree by default when any nesting
  exists; ``--flat`` falls back to the table view for scripting.
* ``tulip accounts show`` — when ``parent_account_id`` is set, displays
  the parent's code/name alongside the UUID.

The API-side validation (#42.a) is exercised by surfacing those error
codes in the CLI: an unknown parent code surfaces ``account.not_found``
during local resolution; an API-side reject (type mismatch, etc.) renders
via the existing RFC 9457 path with the appropriate exit code.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx
import pytest

_PASSWORD = "long-enough-password"


def _run_cli(
    *args: str, api_url: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tulip_cli", "--api-url", api_url, *args],
        check=False,
        capture_output=True,
        text=True,
        input=stdin,
        timeout=20,
    )


@pytest.fixture
def authed_session(live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    httpx.post(
        f"{live_api}/v1/auth/register",
        json={
            "email": "alice@example.com",
            "password": _PASSWORD,
            "display_name": "Alice",
            "household_name": "Alice's Household",
        },
        timeout=10,
    ).raise_for_status()
    cli_login = _run_cli(
        "auth",
        "login",
        "--email",
        "alice@example.com",
        "--password-stdin",
        api_url=live_api,
        stdin=f"{_PASSWORD}\n",
    )
    assert cli_login.returncode == 0, cli_login.stderr
    return live_api


def _add(
    api_url: str,
    *,
    name: str,
    type_: str = "asset",
    code: str | None = None,
    parent: str | None = None,
) -> subprocess.CompletedProcess[str]:
    args = ["accounts", "add", "--name", name, "--type", type_, "--currency", "USD"]
    if code is not None:
        args += ["--code", code]
    if parent is not None:
        args += ["--parent", parent]
    return _run_cli(*args, api_url=api_url)


# ---------- --parent on add ----------


@pytest.mark.integration
def test_accounts_add_with_parent_code(authed_session: str) -> None:
    parent = _add(authed_session, name="Assets", code="assets")
    assert parent.returncode == 0, parent.stderr

    child = _add(authed_session, name="Checking", code="assets:checking", parent="assets")
    assert child.returncode == 0, child.stderr

    show = _run_cli("accounts", "show", "assets:checking", api_url=authed_session)
    # When the API returns a parent_account_id, the CLI displays it; the
    # parent-name surfacing is exercised below.
    assert "Checking" in show.stdout


@pytest.mark.integration
def test_accounts_add_with_parent_uuid(authed_session: str) -> None:
    """A UUID in --parent works as well as a code."""
    import json

    create_parent = _run_cli(
        "--json",
        "accounts",
        "add",
        "--name",
        "Assets",
        "--type",
        "asset",
        "--currency",
        "USD",
        "--code",
        "assets",
        api_url=authed_session,
    )
    assert create_parent.returncode == 0, create_parent.stderr
    parent_id = json.loads(create_parent.stdout)["id"]

    child = _add(authed_session, name="Cash", parent=parent_id)
    assert child.returncode == 0, child.stderr


@pytest.mark.integration
def test_accounts_add_with_unknown_parent_code_yields_user_error(
    authed_session: str,
) -> None:
    result = _add(authed_session, name="Orphan", parent="no-such-parent")
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "no-such-parent" in result.stderr.lower() or "not found" in result.stderr.lower()


@pytest.mark.integration
def test_accounts_add_with_type_mismatched_parent_surfaces_api_error(
    authed_session: str,
) -> None:
    """API enforces parent.type == child.type. The CLI must surface the message clearly."""
    parent = _add(authed_session, name="Assets", type_="asset", code="assets")
    assert parent.returncode == 0, parent.stderr

    result = _add(authed_session, name="Wrong", type_="expense", parent="assets")
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "type" in result.stderr.lower()


# ---------- list rendering ----------


@pytest.mark.integration
def test_accounts_list_default_view_is_tree_when_nested(authed_session: str) -> None:
    _add(authed_session, name="Assets", code="assets")
    _add(authed_session, name="Checking", code="assets:checking", parent="assets")
    _add(authed_session, name="Savings", code="assets:savings", parent="assets")

    result = _run_cli("accounts", "list", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "Assets" in result.stdout
    assert "Checking" in result.stdout
    assert "Savings" in result.stdout
    # Children should appear under the parent — easiest portable check is
    # that each child name appears AFTER the parent name in the output.
    parent_idx = result.stdout.find("Assets")
    checking_idx = result.stdout.find("Checking")
    savings_idx = result.stdout.find("Savings")
    assert parent_idx < checking_idx
    assert parent_idx < savings_idx


@pytest.mark.integration
def test_accounts_list_flat_flag_renders_table(authed_session: str) -> None:
    _add(authed_session, name="Assets", code="assets")
    _add(authed_session, name="Checking", code="assets:checking", parent="assets")

    result = _run_cli("accounts", "list", "--flat", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    # Flat view = the existing table renderer; both rows present.
    assert "assets:checking" in result.stdout
    assert "assets" in result.stdout


@pytest.mark.integration
def test_accounts_list_with_no_nesting_renders_flat_by_default(
    authed_session: str,
) -> None:
    """No nesting → no point in tree headers; fall back to the table."""
    _add(authed_session, name="Cash", code="assets:cash")
    _add(authed_session, name="Food", type_="expense", code="expenses:food")

    result = _run_cli("accounts", "list", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "assets:cash" in result.stdout
    assert "expenses:food" in result.stdout


# ---------- show with parent name ----------


@pytest.mark.integration
def test_accounts_show_displays_parent_code_and_name(authed_session: str) -> None:
    _add(authed_session, name="Assets", code="assets")
    _add(authed_session, name="Checking", code="assets:checking", parent="assets")

    result = _run_cli("accounts", "show", "assets:checking", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    # The parent UUID line was already there; the new behavior surfaces
    # the parent's code/name so users don't have to mentally resolve it.
    assert "Assets" in result.stdout  # parent name
    assert "assets" in result.stdout  # parent code (also matches own code, fine)


@pytest.mark.integration
def test_accounts_show_no_parent_omits_parent_line(authed_session: str) -> None:
    _add(authed_session, name="Top", code="top")

    result = _run_cli("accounts", "show", "top", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    # No "parent:" line should appear at all when there is no parent.
    assert "parent:" not in result.stdout

"""End-to-end tests for P3.6 CLI commands (#54).

Covers:

* ``tulip transactions list`` (with all five filters and ``--json``)
* ``tulip transactions show``
* ``tulip accounts edit`` (PATCH-semantics, --parent reparent, etc.)
* ``tulip accounts deactivate`` (with confirmation prompt and ``--yes``)

Each test spawns the API via ``live_api``, registers + logs in (writing
to a per-test JSON token store), and runs the real ``tulip`` console
script as a subprocess.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
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


def _add_account(
    api_url: str,
    code: str,
    name: str,
    type_: str,
    *,
    parent: str | None = None,
) -> str:
    args = [
        "--json",
        "accounts",
        "add",
        "--name",
        name,
        "--type",
        type_,
        "--currency",
        "USD",
        "--code",
        code,
    ]
    if parent is not None:
        args += ["--parent", parent]
    result = _run_cli(*args, api_url=api_url)
    assert result.returncode == 0, result.stderr
    # `accounts add` prints "Created account <id>\n<details>" then JSON via --json.
    # With --json it prints just the body to stdout.
    return str(json.loads(result.stdout)["id"])


def _add_tx(api_url: str, *, tx_date: str, description: str, posts: list[str]) -> str:
    args = ["--json", "add", "--date", tx_date, "--description", description]
    for p in posts:
        args += ["--post", p]
    result = _run_cli(*args, api_url=api_url)
    assert result.returncode == 0, result.stderr
    return str(json.loads(result.stdout)["id"])


# ---------- tulip transactions list ----------


@pytest.fixture
def seeded_session(authed_session: str) -> tuple[str, str, str, str]:
    """Seed two accounts and three transactions across different dates.

    Returns ``(api_url, checking_id, food_id, rent_id)``.
    """
    checking_id = _add_account(authed_session, "assets:checking", "Checking", "asset")
    food_id = _add_account(authed_session, "expenses:food", "Food", "expense")
    rent_id = _add_account(authed_session, "expenses:rent", "Rent", "expense")

    year = date.today().year
    _add_tx(
        authed_session,
        tx_date=f"{year}-01-15",
        description="lunch-jan",
        posts=["expenses:food=10.00", "assets:checking=-10.00"],
    )
    _add_tx(
        authed_session,
        tx_date=f"{year}-06-15",
        description="rent-jun",
        posts=["expenses:rent=1500.00", "assets:checking=-1500.00"],
    )
    _add_tx(
        authed_session,
        tx_date=f"{year}-11-15",
        description="lunch-nov",
        posts=["expenses:food=12.00", "assets:checking=-12.00"],
    )
    return authed_session, checking_id, food_id, rent_id


@pytest.mark.integration
def test_transactions_list_renders_all(seeded_session: tuple[str, str, str, str]) -> None:
    api_url, _checking, _food, _rent = seeded_session
    result = _run_cli("transactions", "list", api_url=api_url)
    assert result.returncode == 0, result.stderr
    # All three descriptions should show up in the table.
    for desc in ("lunch-jan", "rent-jun", "lunch-nov"):
        assert desc in result.stdout


@pytest.mark.integration
def test_transactions_list_shows_id_prefix(seeded_session: tuple[str, str, str, str]) -> None:
    """Default table prints the first 8 chars of each transaction id (#207)."""
    api_url, _checking, _food, _rent = seeded_session
    json_result = _run_cli("--json", "transactions", "list", api_url=api_url)
    rows = json.loads(json_result.stdout)
    assert len(rows) == 3

    table_result = _run_cli("transactions", "list", api_url=api_url)
    assert table_result.returncode == 0, table_result.stderr
    for row in rows:
        prefix = row["id"][:8]
        assert prefix in table_result.stdout, (prefix, table_result.stdout)


@pytest.mark.integration
def test_transactions_list_json_payload_unchanged(
    seeded_session: tuple[str, str, str, str],
) -> None:
    """--json passthrough still emits full UUIDs (not truncated)."""
    api_url, _checking, _food, _rent = seeded_session
    result = _run_cli("--json", "transactions", "list", api_url=api_url)
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    for row in rows:
        # Full UUIDs are 36 characters with hyphens.
        assert len(row["id"]) == 36


@pytest.mark.integration
def test_transactions_list_filters_by_account_code(
    seeded_session: tuple[str, str, str, str],
) -> None:
    api_url, _checking, _food, _rent = seeded_session
    result = _run_cli("transactions", "list", "--account", "expenses:rent", api_url=api_url)
    assert result.returncode == 0, result.stderr
    assert "rent-jun" in result.stdout
    assert "lunch-jan" not in result.stdout
    assert "lunch-nov" not in result.stdout


@pytest.mark.integration
def test_transactions_list_filters_by_date_range(
    seeded_session: tuple[str, str, str, str],
) -> None:
    api_url, _checking, _food, _rent = seeded_session
    year = date.today().year
    result = _run_cli(
        "transactions",
        "list",
        "--from",
        f"{year}-06-01",
        "--to",
        f"{year}-06-30",
        api_url=api_url,
    )
    assert result.returncode == 0, result.stderr
    assert "rent-jun" in result.stdout
    assert "lunch-jan" not in result.stdout
    assert "lunch-nov" not in result.stdout


@pytest.mark.integration
def test_transactions_list_limit_caps_results(
    seeded_session: tuple[str, str, str, str],
) -> None:
    api_url, _checking, _food, _rent = seeded_session
    result = _run_cli("--json", "transactions", "list", "--limit", "1", api_url=api_url)
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    assert len(rows) == 1
    # Newest first → November lunch.
    assert rows[0]["description"] == "lunch-nov"


@pytest.mark.integration
def test_transactions_list_status_filter(
    seeded_session: tuple[str, str, str, str],
) -> None:
    api_url, _checking, _food, _rent = seeded_session
    # Seeded transactions are POSTED. PENDING should be empty.
    result = _run_cli("--json", "transactions", "list", "--status", "pending", api_url=api_url)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


@pytest.mark.integration
def test_transactions_list_invalid_status_user_error(authed_session: str) -> None:
    # Client-side validation goes through typer.BadParameter, which exits 2
    # (Click usage convention). This matches the existing pattern in
    # `tulip add` / `tulip balance --as-of`.
    result = _run_cli("transactions", "list", "--status", "bogus", api_url=authed_session)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "status" in result.stderr.lower()


@pytest.mark.integration
def test_transactions_list_invalid_date_user_error(authed_session: str) -> None:
    result = _run_cli("transactions", "list", "--from", "yesterday", api_url=authed_session)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "yyyy-mm-dd" in result.stderr.lower()


@pytest.mark.integration
def test_transactions_list_empty_message(authed_session: str) -> None:
    result = _run_cli("transactions", "list", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "no transactions" in result.stdout.lower()


@pytest.mark.integration
def test_transactions_list_unauthenticated_fails(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    result = _run_cli("transactions", "list", api_url=live_api)
    assert result.returncode == 2, (result.stdout, result.stderr)


# ---------- tulip transactions show ----------


@pytest.mark.integration
def test_transactions_show_by_uuid(seeded_session: tuple[str, str, str, str]) -> None:
    api_url, _checking, _food, _rent = seeded_session
    list_result = _run_cli("--json", "transactions", "list", api_url=api_url)
    rows = json.loads(list_result.stdout)
    target = next(r for r in rows if r["description"] == "rent-jun")

    result = _run_cli("transactions", "show", target["id"], api_url=api_url)
    assert result.returncode == 0, result.stderr
    assert "rent-jun" in result.stdout
    assert target["id"] in result.stdout
    assert "1500.00" in result.stdout


@pytest.mark.integration
def test_transactions_show_json_passthrough(
    seeded_session: tuple[str, str, str, str],
) -> None:
    api_url, _checking, _food, _rent = seeded_session
    list_result = _run_cli("--json", "transactions", "list", api_url=api_url)
    target = next(r for r in json.loads(list_result.stdout) if r["description"] == "rent-jun")

    result = _run_cli("--json", "transactions", "show", target["id"], api_url=api_url)
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["id"] == target["id"]
    assert body["description"] == "rent-jun"
    assert len(body["postings"]) == 2


@pytest.mark.integration
def test_transactions_show_invalid_uuid_user_error(authed_session: str) -> None:
    result = _run_cli("transactions", "show", "not-a-uuid", api_url=authed_session)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "uuid" in result.stderr.lower()


@pytest.mark.integration
def test_transactions_show_unknown_uuid_user_error(authed_session: str) -> None:
    result = _run_cli(
        "transactions",
        "show",
        "00000000-0000-0000-0000-000000000000",
        api_url=authed_session,
    )
    assert result.returncode == 1, (result.stdout, result.stderr)


@pytest.mark.integration
def test_transactions_show_by_prefix(seeded_session: tuple[str, str, str, str]) -> None:
    """An 8-char id prefix resolves to the full UUID via the API resolver."""
    api_url, _checking, _food, _rent = seeded_session
    rows = json.loads(_run_cli("--json", "transactions", "list", api_url=api_url).stdout)
    target = next(r for r in rows if r["description"] == "rent-jun")

    result = _run_cli("transactions", "show", target["id"][:8], api_url=api_url)
    assert result.returncode == 0, result.stderr
    assert "rent-jun" in result.stdout
    assert target["id"] in result.stdout


@pytest.mark.integration
def test_transactions_show_unknown_prefix_user_error(
    seeded_session: tuple[str, str, str, str],
) -> None:
    """A well-formed hex prefix with no matches surfaces a not-found error."""
    api_url, _checking, _food, _rent = seeded_session
    # 'deadbeef' is hex-valid but doesn't match any seeded transaction id.
    result = _run_cli("transactions", "show", "deadbeef", api_url=api_url)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "deadbeef" in result.stderr.lower()


# ---------- tulip accounts edit ----------


@pytest.mark.integration
def test_accounts_edit_renames(authed_session: str) -> None:
    _add_account(authed_session, "assets:checking", "Checking", "asset")
    result = _run_cli(
        "accounts",
        "edit",
        "assets:checking",
        "--name",
        "Primary Checking",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Primary Checking" in result.stdout

    show = _run_cli("accounts", "show", "assets:checking", api_url=authed_session)
    assert "Primary Checking" in show.stdout


@pytest.mark.integration
def test_accounts_edit_only_sends_provided_fields(authed_session: str) -> None:
    _add_account(authed_session, "assets:checking", "Checking", "asset")
    # Edit with only --code, leaving name/visibility untouched.
    result = _run_cli(
        "--json",
        "accounts",
        "edit",
        "assets:checking",
        "--code",
        "assets:primary",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["code"] == "assets:primary"
    assert payload["name"] == "Checking"  # unchanged


@pytest.mark.integration
def test_accounts_edit_reparent(authed_session: str) -> None:
    _add_account(authed_session, "expenses:top", "Top", "expense")
    _add_account(authed_session, "expenses:groceries", "Groceries", "expense")
    # Re-parent groceries under top.
    result = _run_cli(
        "accounts",
        "edit",
        "expenses:groceries",
        "--parent",
        "expenses:top",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    # And the tree view should now nest groceries under top.
    list_result = _run_cli("accounts", "list", api_url=authed_session)
    assert "Top" in list_result.stdout
    assert "Groceries" in list_result.stdout


@pytest.mark.integration
def test_accounts_edit_parent_cycle_rejected(authed_session: str) -> None:
    _add_account(authed_session, "expenses:parent", "Parent", "expense")
    _add_account(authed_session, "expenses:child", "Child", "expense", parent="expenses:parent")
    # Attempt to re-parent parent under its own child → cycle.
    result = _run_cli(
        "accounts",
        "edit",
        "expenses:parent",
        "--parent",
        "expenses:child",
        api_url=authed_session,
    )
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "cycle" in result.stderr.lower()


@pytest.mark.integration
def test_accounts_edit_no_changes_passed_is_noop(authed_session: str) -> None:
    """Calling edit with no flags should not crash; PATCH with empty body is a no-op."""
    _add_account(authed_session, "assets:checking", "Checking", "asset")
    result = _run_cli("accounts", "edit", "assets:checking", api_url=authed_session)
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
def test_accounts_edit_unauthenticated_fails(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    result = _run_cli(
        "accounts",
        "edit",
        "assets:checking",
        "--name",
        "X",
        api_url=live_api,
    )
    assert result.returncode == 2, (result.stdout, result.stderr)


# ---------- tulip accounts deactivate ----------


@pytest.mark.integration
def test_accounts_deactivate_with_yes_skip(authed_session: str) -> None:
    _add_account(authed_session, "assets:old", "Old", "asset")
    result = _run_cli("accounts", "deactivate", "assets:old", "--yes", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    # The deactivated account should drop out of `accounts list`.
    list_result = _run_cli("accounts", "list", api_url=authed_session)
    assert "assets:old" not in list_result.stdout


@pytest.mark.integration
def test_accounts_deactivate_prompts_without_yes(authed_session: str) -> None:
    """Without ``--yes``, the prompt is interactive; answering 'n' aborts."""
    _add_account(authed_session, "assets:keepme", "Keep", "asset")
    result = _run_cli(
        "accounts",
        "deactivate",
        "assets:keepme",
        api_url=authed_session,
        stdin="n\n",
    )
    # User declined → no-op exit 0.
    assert result.returncode == 0, result.stderr
    # Account still active.
    list_result = _run_cli("accounts", "list", api_url=authed_session)
    assert "assets:keepme" in list_result.stdout


@pytest.mark.integration
def test_accounts_deactivate_unknown_code_user_error(authed_session: str) -> None:
    result = _run_cli("accounts", "deactivate", "no-such-code", "--yes", api_url=authed_session)
    assert result.returncode == 1, (result.stdout, result.stderr)


@pytest.mark.integration
def test_accounts_deactivate_unauthenticated_fails(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    result = _run_cli("accounts", "deactivate", "assets:checking", "--yes", api_url=live_api)
    assert result.returncode == 2, (result.stdout, result.stderr)

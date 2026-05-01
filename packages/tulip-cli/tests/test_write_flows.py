"""End-to-end tests for ``tulip accounts add`` and ``tulip add`` (transactions)."""

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


# ---------- tulip accounts add ----------


@pytest.mark.integration
def test_accounts_add_happy_path(authed_session: str) -> None:
    result = _run_cli(
        "accounts",
        "add",
        "--name",
        "Checking",
        "--type",
        "asset",
        "--currency",
        "USD",
        "--code",
        "assets:checking",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Checking" in result.stdout
    assert "assets:checking" in result.stdout

    # The account should now be visible via the existing list command.
    list_result = _run_cli("accounts", "list", api_url=authed_session)
    assert "assets:checking" in list_result.stdout
    assert "Checking" in list_result.stdout


@pytest.mark.integration
def test_accounts_add_minimal_no_code(authed_session: str) -> None:
    result = _run_cli(
        "accounts",
        "add",
        "--name",
        "Cash",
        "--type",
        "asset",
        "--currency",
        "USD",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
def test_accounts_add_json_emits_created_body(authed_session: str) -> None:
    result = _run_cli(
        "--json",
        "accounts",
        "add",
        "--name",
        "Savings",
        "--type",
        "asset",
        "--currency",
        "USD",
        "--code",
        "assets:savings",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["name"] == "Savings"
    assert payload["code"] == "assets:savings"
    assert payload["type"] == "asset"


@pytest.mark.integration
def test_accounts_add_invalid_type_yields_user_error(authed_session: str) -> None:
    result = _run_cli(
        "accounts",
        "add",
        "--name",
        "Wat",
        "--type",
        "not-a-type",
        "--currency",
        "USD",
        api_url=authed_session,
    )
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "type" in result.stderr.lower()


@pytest.mark.integration
def test_accounts_add_unauthenticated_fails_clearly(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    result = _run_cli(
        "accounts",
        "add",
        "--name",
        "X",
        "--type",
        "asset",
        "--currency",
        "USD",
        api_url=live_api,
    )
    assert result.returncode == 2, (result.stdout, result.stderr)


# ---------- tulip add (transactions) ----------


def _add_account(api_url: str, code: str, name: str, type_: str) -> None:
    result = _run_cli(
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
        api_url=api_url,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
def test_add_transaction_happy_path(authed_session: str) -> None:
    _add_account(authed_session, "assets:checking", "Checking", "asset")
    _add_account(authed_session, "expenses:food", "Food", "expense")

    today = date.today().isoformat()
    result = _run_cli(
        "add",
        "--date",
        today,
        "--description",
        "Lunch",
        "--post",
        "expenses:food=12.50",
        "--post",
        "assets:checking=-12.50",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Lunch" in result.stdout
    assert "12.50" in result.stdout

    # Balance check round-trips: food should now be at 12.50.
    bal = _run_cli("balance", "expenses:food", api_url=authed_session)
    assert "12.50" in bal.stdout


@pytest.mark.integration
def test_add_transaction_json_returns_created_body(authed_session: str) -> None:
    _add_account(authed_session, "assets:cash", "Cash", "asset")
    _add_account(authed_session, "expenses:fun", "Fun", "expense")

    result = _run_cli(
        "--json",
        "add",
        "--date",
        date.today().isoformat(),
        "--description",
        "Movie",
        "--post",
        "expenses:fun=15.00",
        "--post",
        "assets:cash=-15.00",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["description"] == "Movie"
    assert payload["status"] == "posted"
    assert len(payload["postings"]) == 2


@pytest.mark.integration
def test_add_transaction_unbalanced_yields_user_error(authed_session: str) -> None:
    _add_account(authed_session, "assets:cash", "Cash", "asset")
    _add_account(authed_session, "expenses:fun", "Fun", "expense")

    result = _run_cli(
        "add",
        "--date",
        date.today().isoformat(),
        "--description",
        "Bad",
        "--post",
        "expenses:fun=10.00",
        "--post",
        "assets:cash=-9.00",
        api_url=authed_session,
    )
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "balance" in result.stderr.lower()


@pytest.mark.integration
def test_add_transaction_unknown_account_in_post_yields_user_error(
    authed_session: str,
) -> None:
    _add_account(authed_session, "assets:cash", "Cash", "asset")

    result = _run_cli(
        "add",
        "--date",
        date.today().isoformat(),
        "--description",
        "Bad post",
        "--post",
        "no-such-account=10.00",
        "--post",
        "assets:cash=-10.00",
        api_url=authed_session,
    )
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "no-such-account" in result.stderr.lower() or "not found" in result.stderr.lower()


@pytest.mark.integration
def test_add_transaction_requires_at_least_two_postings(
    authed_session: str,
) -> None:
    _add_account(authed_session, "assets:cash", "Cash", "asset")

    result = _run_cli(
        "add",
        "--date",
        date.today().isoformat(),
        "--description",
        "Solo",
        "--post",
        "assets:cash=10.00",
        api_url=authed_session,
    )
    assert result.returncode == 1, (result.stdout, result.stderr)


@pytest.mark.integration
def test_add_transaction_unauthenticated_fails_clearly(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    result = _run_cli(
        "add",
        "--date",
        date.today().isoformat(),
        "--description",
        "Hi",
        "--post",
        "x=1",
        "--post",
        "y=-1",
        api_url=live_api,
    )
    assert result.returncode == 2, (result.stdout, result.stderr)

"""End-to-end tests for ``tulip accounts list`` and ``tulip accounts show``.

Each test spawns the API via ``live_api``, registers a user, logs in
(populating a per-test JSON token store), and then runs the CLI as a
subprocess. The CLI's authenticated read path goes through P3.2.b's
transparent-refresh ``TulipClient``.
"""

from __future__ import annotations

import json
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


def _register_and_login(api_url: str, email: str = "alice@example.com") -> str:
    """Register a user and obtain an access token."""
    httpx.post(
        f"{api_url}/v1/auth/register",
        json={
            "email": email,
            "password": _PASSWORD,
            "display_name": "Alice",
            "household_name": "Alice's Household",
        },
        timeout=10,
    ).raise_for_status()
    r = httpx.post(
        f"{api_url}/v1/auth/login",
        json={"email": email, "password": _PASSWORD},
        timeout=10,
    )
    r.raise_for_status()
    return str(r.json()["access_token"])


def _create_account(
    api_url: str,
    access_token: str,
    *,
    code: str | None,
    name: str,
    type_: str = "asset",
) -> dict[str, object]:
    body: dict[str, object] = {
        "name": name,
        "type": type_,
        "currency": "USD",
        "visibility": "shared",
    }
    if code is not None:
        body["code"] = code
    r = httpx.post(
        f"{api_url}/v1/accounts",
        json=body,
        headers={"authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    r.raise_for_status()
    return dict(r.json())


@pytest.fixture
def authed_session(live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Log a user in via the CLI and return the API URL.

    Side effects:
    - ``TULIP_TOKEN_STORE`` is set to a per-test JSON file.
    - The user ``alice@example.com`` exists in the spawned API.
    """
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
    result = _run_cli(
        "auth",
        "login",
        "--email",
        "alice@example.com",
        "--password-stdin",
        api_url=live_api,
        stdin=f"{_PASSWORD}\n",
    )
    assert result.returncode == 0, result.stderr
    return live_api


@pytest.mark.integration
def test_accounts_list_when_empty(authed_session: str) -> None:
    result = _run_cli("accounts", "list", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    # Empty households still register an opening message — not just blank stdout.
    assert "no accounts" in result.stdout.lower()


@pytest.mark.integration
def test_accounts_list_renders_table(authed_session: str) -> None:
    access = _register_and_login(authed_session, email="bob@example.com")
    # Bob is a separate household; create a couple of accounts under
    # Alice's household instead by re-fetching Alice's token.
    alice_login = httpx.post(
        f"{authed_session}/v1/auth/login",
        json={"email": "alice@example.com", "password": _PASSWORD},
        timeout=10,
    )
    alice_login.raise_for_status()
    alice_access = str(alice_login.json()["access_token"])
    _create_account(authed_session, alice_access, code="assets:checking", name="Checking")
    _create_account(
        authed_session,
        alice_access,
        code="expenses:groceries",
        name="Groceries",
        type_="expense",
    )

    result = _run_cli("accounts", "list", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "assets:checking" in result.stdout
    assert "Checking" in result.stdout
    assert "expenses:groceries" in result.stdout
    # The other household's data must not leak in (Bob is irrelevant here, just
    # asserting the list is filtered to Alice's household).
    assert "bob" not in result.stdout.lower()
    # Unused fixture-side helper to keep the linter happy.
    _ = access


@pytest.mark.integration
def test_accounts_list_json_emits_array(authed_session: str) -> None:
    alice_login = httpx.post(
        f"{authed_session}/v1/auth/login",
        json={"email": "alice@example.com", "password": _PASSWORD},
        timeout=10,
    )
    alice_login.raise_for_status()
    alice_access = str(alice_login.json()["access_token"])
    _create_account(authed_session, alice_access, code="assets:cash", name="Cash")

    result = _run_cli("--json", "accounts", "list", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert any(a["code"] == "assets:cash" for a in payload)


@pytest.mark.integration
def test_accounts_show_by_code(authed_session: str) -> None:
    alice_login = httpx.post(
        f"{authed_session}/v1/auth/login",
        json={"email": "alice@example.com", "password": _PASSWORD},
        timeout=10,
    )
    alice_login.raise_for_status()
    alice_access = str(alice_login.json()["access_token"])
    _create_account(authed_session, alice_access, code="assets:checking", name="Checking")

    result = _run_cli("accounts", "show", "assets:checking", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "Checking" in result.stdout
    assert "assets:checking" in result.stdout


@pytest.mark.integration
def test_accounts_show_by_uuid(authed_session: str) -> None:
    alice_login = httpx.post(
        f"{authed_session}/v1/auth/login",
        json={"email": "alice@example.com", "password": _PASSWORD},
        timeout=10,
    )
    alice_login.raise_for_status()
    alice_access = str(alice_login.json()["access_token"])
    created = _create_account(authed_session, alice_access, code="assets:savings", name="Savings")

    result = _run_cli("accounts", "show", str(created["id"]), api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "Savings" in result.stdout


@pytest.mark.integration
def test_accounts_show_unknown_code_yields_user_error(authed_session: str) -> None:
    result = _run_cli("accounts", "show", "no-such-code", api_url=authed_session)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "no-such-code" in result.stderr.lower() or "not found" in result.stderr.lower()


@pytest.mark.integration
def test_accounts_show_ambiguous_code_yields_user_error(authed_session: str) -> None:
    """``code`` has no uniqueness constraint, so duplicates are possible. CLI must complain."""
    alice_login = httpx.post(
        f"{authed_session}/v1/auth/login",
        json={"email": "alice@example.com", "password": _PASSWORD},
        timeout=10,
    )
    alice_login.raise_for_status()
    alice_access = str(alice_login.json()["access_token"])
    _create_account(authed_session, alice_access, code="assets:duplicated", name="Dup A")
    _create_account(authed_session, alice_access, code="assets:duplicated", name="Dup B")

    result = _run_cli("accounts", "show", "assets:duplicated", api_url=authed_session)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "ambiguous" in result.stderr.lower() or "multiple" in result.stderr.lower()


@pytest.mark.integration
def test_accounts_list_unauthenticated_fails_clearly(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    result = _run_cli("accounts", "list", api_url=live_api)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "not logged in" in result.stderr.lower() or "log in" in result.stderr.lower()

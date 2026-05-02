"""End-to-end tests for ``tulip sinking-funds`` (P4.2).

Mirrors test_envelopes.py with the goal-bounded field set.
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


def _future_date() -> str:
    return date(date.today().year + 1, 1, 1).isoformat()


def _create_sinking_fund(
    api_url: str,
    access_token: str,
    *,
    name: str,
    currency: str = "USD",
    target_amount: str = "3000.00",
    contribution_strategy: str = "manual",
) -> dict[str, object]:
    body: dict[str, object] = {
        "name": name,
        "currency": currency,
        "target_amount": target_amount,
        "target_date": _future_date(),
        "contribution_strategy": contribution_strategy,
    }
    r = httpx.post(
        f"{api_url}/v1/sinking-funds",
        json=body,
        headers={"authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    r.raise_for_status()
    return dict(r.json())


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


@pytest.fixture
def access_token(authed_session: str) -> str:
    r = httpx.post(
        f"{authed_session}/v1/auth/login",
        json={"email": "alice@example.com", "password": _PASSWORD},
        timeout=10,
    )
    r.raise_for_status()
    return str(r.json()["access_token"])


@pytest.mark.integration
def test_sinking_funds_list_when_empty(authed_session: str) -> None:
    result = _run_cli("sinking-funds", "list", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "No sinking funds" in result.stdout


@pytest.mark.integration
def test_sinking_funds_add_minimal(authed_session: str) -> None:
    result = _run_cli(
        "sinking-funds",
        "add",
        "--name",
        "Vacation",
        "--currency",
        "USD",
        "--target-amount",
        "3000.00",
        "--target-date",
        _future_date(),
        "--contribution-strategy",
        "manual",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Created sinking fund" in result.stdout
    assert "Vacation" in result.stdout


@pytest.mark.integration
def test_sinking_funds_show_by_name(authed_session: str, access_token: str) -> None:
    _create_sinking_fund(authed_session, access_token, name="Vacation")
    result = _run_cli("sinking-funds", "show", "Vacation", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "Vacation" in result.stdout
    assert "balance" in result.stdout


@pytest.mark.integration
def test_sinking_funds_list_json(authed_session: str, access_token: str) -> None:
    _create_sinking_fund(authed_session, access_token, name="Vacation")
    result = _run_cli("--json", "sinking-funds", "list", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert payload[0]["name"] == "Vacation"


@pytest.mark.integration
def test_sinking_funds_edit_partial(authed_session: str, access_token: str) -> None:
    created = _create_sinking_fund(authed_session, access_token, name="Vacation")
    new_target = date(date.today().year + 2, 6, 1).isoformat()
    result = _run_cli(
        "sinking-funds",
        "edit",
        str(created["id"]),
        "--target-amount",
        "5000",
        "--target-date",
        new_target,
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Updated sinking fund" in result.stdout


@pytest.mark.integration
def test_sinking_funds_deactivate(authed_session: str, access_token: str) -> None:
    created = _create_sinking_fund(authed_session, access_token, name="Vacation")
    result = _run_cli(
        "sinking-funds",
        "deactivate",
        str(created["id"]),
        "--yes",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Deactivated sinking fund" in result.stdout


@pytest.mark.integration
def test_sinking_funds_show_unknown_yields_user_error(
    authed_session: str,
) -> None:
    result = _run_cli("sinking-funds", "show", "no-such-name", api_url=authed_session)
    assert result.returncode == 1
    assert "not found" in (result.stdout + result.stderr).lower()


@pytest.mark.integration
def test_sinking_funds_unauthenticated_yields_auth_error(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    result = _run_cli("sinking-funds", "list", api_url=live_api)
    assert result.returncode == 2

"""End-to-end tests for ``tulip refill``, ``tulip transfer``, ``tulip budget-inflow`` (P4.2)."""

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


def _create_envelope(
    api_url: str,
    token: str,
    name: str,
    currency: str = "USD",
) -> dict[str, object]:
    r = httpx.post(
        f"{api_url}/v1/envelopes",
        json={
            "name": name,
            "currency": currency,
            "budget_period": "monthly",
            "rollover_policy": "reset",
        },
        headers={"authorization": f"Bearer {token}"},
        timeout=10,
    )
    r.raise_for_status()
    return dict(r.json())


# ---- budget-inflow ----------------------------------------------------


@pytest.mark.integration
def test_budget_inflow_succeeds(authed_session: str) -> None:
    result = _run_cli(
        "budget-inflow",
        "--amount",
        "1000",
        "--currency",
        "USD",
        "--date",
        date.today().isoformat(),
        "--description",
        "Salary",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Declared inflow of 1000" in result.stdout
    assert "1000.00" in result.stdout  # quantized response balance


@pytest.mark.integration
def test_budget_inflow_lazy_creates_eur_pools(authed_session: str) -> None:
    result = _run_cli(
        "budget-inflow",
        "--amount",
        "500",
        "--currency",
        "EUR",
        "--date",
        date.today().isoformat(),
        "--description",
        "EUR salary",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "EUR" in result.stdout


@pytest.mark.integration
def test_budget_inflow_unknown_currency_yields_user_error(authed_session: str) -> None:
    result = _run_cli(
        "budget-inflow",
        "--amount",
        "100",
        "--currency",
        "ZZZ",
        "--date",
        date.today().isoformat(),
        "--description",
        "Bad",
        api_url=authed_session,
    )
    assert result.returncode == 1
    assert (
        "ZZZ" in (result.stdout + result.stderr)
        or "currency" in (result.stdout + result.stderr).lower()
    )


@pytest.mark.integration
def test_budget_inflow_json(authed_session: str) -> None:
    result = _run_cli(
        "--json",
        "budget-inflow",
        "--amount",
        "500",
        "--currency",
        "USD",
        "--date",
        date.today().isoformat(),
        "--description",
        "Salary",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["currency"] == "USD"


# ---- refill -----------------------------------------------------------


@pytest.mark.integration
def test_refill_succeeds(authed_session: str, access_token: str) -> None:
    # Seed with an inflow so Unallocated has positive balance
    # (refill works either way, but exercises the more realistic flow).
    _run_cli(
        "budget-inflow",
        "--amount",
        "500",
        "--currency",
        "USD",
        "--date",
        date.today().isoformat(),
        "--description",
        "Salary",
        api_url=authed_session,
    )
    _create_envelope(authed_session, access_token, "Groceries")
    result = _run_cli(
        "refill",
        "Groceries",
        "--amount",
        "250.00",
        "--date",
        date.today().isoformat(),
        "--description",
        "Monthly grocery refill",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Refilled Groceries by 250.00" in result.stdout
    assert "new balance: 250.00" in result.stdout


@pytest.mark.integration
def test_refill_unknown_envelope_yields_user_error(authed_session: str) -> None:
    result = _run_cli(
        "refill",
        "no-such-envelope",
        "--amount",
        "100",
        "--date",
        date.today().isoformat(),
        "--description",
        "X",
        api_url=authed_session,
    )
    assert result.returncode == 1
    assert "not found" in (result.stdout + result.stderr).lower()


# ---- transfer ---------------------------------------------------------


@pytest.mark.integration
def test_transfer_succeeds(authed_session: str, access_token: str) -> None:
    _create_envelope(authed_session, access_token, "Groceries")
    _create_envelope(authed_session, access_token, "Entertainment")
    # Refill source first.
    _run_cli(
        "budget-inflow",
        "--amount",
        "500",
        "--currency",
        "USD",
        "--date",
        date.today().isoformat(),
        "--description",
        "Salary",
        api_url=authed_session,
    )
    _run_cli(
        "refill",
        "Groceries",
        "--amount",
        "300",
        "--date",
        date.today().isoformat(),
        "--description",
        "Refill",
        api_url=authed_session,
    )
    result = _run_cli(
        "transfer",
        "--from",
        "Groceries",
        "--to",
        "Entertainment",
        "--amount",
        "100",
        "--date",
        date.today().isoformat(),
        "--description",
        "Move to entertainment",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Transferred 100" in result.stdout
    assert "new destination balance: 100.00" in result.stdout


@pytest.mark.integration
def test_transfer_currency_mismatch_yields_user_error(
    authed_session: str, access_token: str
) -> None:
    _create_envelope(authed_session, access_token, "USD env", currency="USD")
    _create_envelope(authed_session, access_token, "EUR env", currency="EUR")
    result = _run_cli(
        "transfer",
        "--from",
        "USD env",
        "--to",
        "EUR env",
        "--amount",
        "10",
        "--date",
        date.today().isoformat(),
        "--description",
        "Cross-ccy",
        api_url=authed_session,
    )
    assert result.returncode == 1
    assert "currenc" in (result.stdout + result.stderr).lower()


@pytest.mark.integration
def test_transfer_same_pool_yields_user_error(authed_session: str, access_token: str) -> None:
    _create_envelope(authed_session, access_token, "Groceries")
    result = _run_cli(
        "transfer",
        "--from",
        "Groceries",
        "--to",
        "Groceries",
        "--amount",
        "10",
        "--date",
        date.today().isoformat(),
        "--description",
        "Self",
        api_url=authed_session,
    )
    assert result.returncode == 1
    haystack = (result.stdout + result.stderr).lower()
    assert "same" in haystack or "differ" in haystack


@pytest.mark.integration
def test_transfer_unknown_pool_yields_user_error(authed_session: str) -> None:
    result = _run_cli(
        "transfer",
        "--from",
        "no-such-source",
        "--to",
        "no-such-dest",
        "--amount",
        "10",
        "--date",
        date.today().isoformat(),
        "--description",
        "Bad",
        api_url=authed_session,
    )
    assert result.returncode == 1


@pytest.mark.integration
def test_refill_unauthenticated_yields_auth_error(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    result = _run_cli(
        "refill",
        "anything",
        "--amount",
        "10",
        "--date",
        date.today().isoformat(),
        "--description",
        "X",
        api_url=live_api,
    )
    assert result.returncode == 2

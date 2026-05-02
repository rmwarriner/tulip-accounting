"""End-to-end tests for ``tulip refills`` (P4.3.c)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

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


def _make_envelope_with_rule(api_url: str, token: str, name: str = "Groceries") -> str:
    r = httpx.post(
        f"{api_url}/v1/envelopes",
        json={
            "name": name,
            "currency": "USD",
            "budget_period": "monthly",
            "rollover_policy": "reset",
            "refill_rule": {
                "strategy": "fixed_amount",
                "amount": "250.00",
                "currency": "USD",
            },
        },
        headers={"authorization": f"Bearer {token}"},
        timeout=10,
    )
    r.raise_for_status()
    return str(r.json()["id"])


# ---- schedule + show ------------------------------------------------


@pytest.mark.integration
def test_refills_schedule_succeeds(authed_session: str, access_token: str) -> None:
    _make_envelope_with_rule(authed_session, access_token, "Groceries")
    result = _run_cli(
        "refills",
        "schedule",
        "Groceries",
        "--rrule",
        "FREQ=MONTHLY",
        "--start",
        "2026-06-01T00:00:00+00:00",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Scheduled Groceries" in result.stdout


@pytest.mark.integration
def test_refills_schedule_unknown_envelope_returns_user_error(
    authed_session: str,
) -> None:
    result = _run_cli(
        "refills",
        "schedule",
        "no-such-envelope",
        "--rrule",
        "FREQ=MONTHLY",
        "--start",
        "2026-06-01T00:00:00+00:00",
        api_url=authed_session,
    )
    assert result.returncode == 1


@pytest.mark.integration
def test_refills_show_returns_schedule(authed_session: str, access_token: str) -> None:
    _make_envelope_with_rule(authed_session, access_token, "Groceries")
    _run_cli(
        "refills",
        "schedule",
        "Groceries",
        "--rrule",
        "FREQ=MONTHLY",
        "--start",
        "2026-06-01T00:00:00+00:00",
        api_url=authed_session,
    )
    result = _run_cli("refills", "show", "Groceries", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "FREQ=MONTHLY" in result.stdout


@pytest.mark.integration
def test_refills_show_no_schedule_returns_user_error(
    authed_session: str, access_token: str
) -> None:
    _make_envelope_with_rule(authed_session, access_token, "Groceries")
    result = _run_cli("refills", "show", "Groceries", api_url=authed_session)
    assert result.returncode == 1


# ---- list ----------------------------------------------------------


@pytest.mark.integration
def test_refills_list_empty(authed_session: str) -> None:
    result = _run_cli("refills", "list", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "No scheduled jobs" in result.stdout


@pytest.mark.integration
def test_refills_list_shows_schedule(authed_session: str, access_token: str) -> None:
    _make_envelope_with_rule(authed_session, access_token, "Groceries")
    _run_cli(
        "refills",
        "schedule",
        "Groceries",
        "--rrule",
        "FREQ=MONTHLY",
        "--start",
        "2026-06-01T00:00:00+00:00",
        api_url=authed_session,
    )
    result = _run_cli("refills", "list", api_url=authed_session)
    assert result.returncode == 0
    # Rich's table truncates long column values with `…`. Match the
    # prefix that survives that truncation.
    assert "envelope_refi" in result.stdout
    assert "FREQ=MONTHLY" in result.stdout


@pytest.mark.integration
def test_refills_list_json(authed_session: str, access_token: str) -> None:
    _make_envelope_with_rule(authed_session, access_token, "Groceries")
    _run_cli(
        "refills",
        "schedule",
        "Groceries",
        "--rrule",
        "FREQ=MONTHLY",
        "--start",
        "2026-06-01T00:00:00+00:00",
        api_url=authed_session,
    )
    result = _run_cli("--json", "refills", "list", api_url=authed_session)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["kind"] == "envelope_refill"


# ---- cancel --------------------------------------------------------


@pytest.mark.integration
def test_refills_cancel_with_yes(authed_session: str, access_token: str) -> None:
    _make_envelope_with_rule(authed_session, access_token, "Groceries")
    _run_cli(
        "refills",
        "schedule",
        "Groceries",
        "--rrule",
        "FREQ=MONTHLY",
        "--start",
        "2026-06-01T00:00:00+00:00",
        api_url=authed_session,
    )
    result = _run_cli("refills", "cancel", "Groceries", "--yes", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "Cancelled refill schedule" in result.stdout

    # show now 404s.
    show = _run_cli("refills", "show", "Groceries", api_url=authed_session)
    assert show.returncode == 1


@pytest.mark.integration
def test_refills_cancel_aborted(authed_session: str, access_token: str) -> None:
    _make_envelope_with_rule(authed_session, access_token, "Groceries")
    _run_cli(
        "refills",
        "schedule",
        "Groceries",
        "--rrule",
        "FREQ=MONTHLY",
        "--start",
        "2026-06-01T00:00:00+00:00",
        api_url=authed_session,
    )
    result = _run_cli("refills", "cancel", "Groceries", api_url=authed_session, stdin="n\n")
    assert result.returncode == 0
    assert "Aborted" in result.stdout

    # show still works (schedule unchanged).
    show = _run_cli("refills", "show", "Groceries", api_url=authed_session)
    assert show.returncode == 0


# ---- run-due -------------------------------------------------------


@pytest.mark.integration
def test_refills_run_due(authed_session: str, access_token: str) -> None:
    # Schedule a refill firing today and run it. The live_api fixture
    # spawns uvicorn with the runner enabled, so the handler is
    # registered automatically.
    _make_envelope_with_rule(authed_session, access_token, "Groceries")
    _run_cli(
        "refills",
        "schedule",
        "Groceries",
        "--rrule",
        "FREQ=MONTHLY",
        "--start",
        date.today().isoformat() + "T00:00:00+00:00",
        api_url=authed_session,
    )
    result = _run_cli("refills", "run-due", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    # "Ran 0 due job(s)" or "Ran 1 due job(s)" — either is acceptable
    # depending on whether the spawned runner already polled the job.
    assert "Ran" in result.stdout


# ---- unauthenticated -----------------------------------------------


@pytest.mark.integration
def test_refills_list_unauthenticated_yields_auth_error(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    result = _run_cli("refills", "list", api_url=live_api)
    assert result.returncode == 2


@pytest.mark.integration
def test_refills_schedule_unauthenticated_yields_auth_error(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    result = _run_cli(
        "refills",
        "schedule",
        str(uuid4()),
        "--rrule",
        "FREQ=MONTHLY",
        "--start",
        "2026-06-01T00:00:00+00:00",
        api_url=live_api,
    )
    assert result.returncode == 2

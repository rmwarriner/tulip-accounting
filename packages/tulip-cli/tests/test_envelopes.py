"""End-to-end tests for ``tulip envelopes`` (P4.2).

Each test spawns the API via ``live_api``, logs Alice in via the CLI, and
exercises the envelope subcommand group as a subprocess.
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


def _create_envelope(
    api_url: str,
    access_token: str,
    *,
    name: str,
    currency: str = "USD",
    budget_period: str = "monthly",
    rollover_policy: str = "reset",
    budget_amount: str | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "name": name,
        "currency": currency,
        "budget_period": budget_period,
        "rollover_policy": rollover_policy,
    }
    if budget_amount is not None:
        body["budget_amount"] = budget_amount
    r = httpx.post(
        f"{api_url}/v1/envelopes",
        json=body,
        headers={"authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    r.raise_for_status()
    return dict(r.json())


@pytest.fixture
def authed_session(live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Register Alice and log her in via the CLI."""
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
    """Return Alice's API access token (for direct API setup in tests)."""
    r = httpx.post(
        f"{authed_session}/v1/auth/login",
        json={"email": "alice@example.com", "password": _PASSWORD},
        timeout=10,
    )
    r.raise_for_status()
    return str(r.json()["access_token"])


# ---- list ----------------------------------------------------------


@pytest.mark.integration
def test_envelopes_list_when_empty(authed_session: str) -> None:
    result = _run_cli("envelopes", "list", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "No envelopes" in result.stdout


@pytest.mark.integration
def test_envelopes_list_renders_table(authed_session: str, access_token: str) -> None:
    _create_envelope(authed_session, access_token, name="Groceries")
    _create_envelope(authed_session, access_token, name="Rent", budget_amount="2500.00")
    result = _run_cli("envelopes", "list", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "Groceries" in result.stdout
    assert "Rent" in result.stdout
    assert "2500.00" in result.stdout


@pytest.mark.integration
def test_envelopes_list_json(authed_session: str, access_token: str) -> None:
    _create_envelope(authed_session, access_token, name="Groceries")
    result = _run_cli("--json", "envelopes", "list", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["name"] == "Groceries"


# ---- add -----------------------------------------------------------


@pytest.mark.integration
def test_envelopes_add_minimal(authed_session: str) -> None:
    result = _run_cli(
        "envelopes",
        "add",
        "--name",
        "Groceries",
        "--currency",
        "USD",
        "--budget-period",
        "monthly",
        "--rollover-policy",
        "reset",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Created envelope" in result.stdout
    assert "Groceries" in result.stdout


@pytest.mark.integration
def test_envelopes_add_with_budget_amount(authed_session: str) -> None:
    result = _run_cli(
        "envelopes",
        "add",
        "--name",
        "Rent",
        "--currency",
        "USD",
        "--budget-period",
        "monthly",
        "--rollover-policy",
        "accumulate",
        "--budget-amount",
        "2500.00",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "2500.00" in result.stdout


@pytest.mark.integration
def test_envelopes_add_invalid_period_yields_user_error(authed_session: str) -> None:
    result = _run_cli(
        "envelopes",
        "add",
        "--name",
        "Bad",
        "--currency",
        "USD",
        "--budget-period",
        "weirdly",
        "--rollover-policy",
        "reset",
        api_url=authed_session,
    )
    # Schema rejection from the API.
    assert result.returncode == 1, result.stderr


# ---- show ----------------------------------------------------------


@pytest.mark.integration
def test_envelopes_show_by_uuid(authed_session: str, access_token: str) -> None:
    created = _create_envelope(authed_session, access_token, name="Groceries")
    result = _run_cli("envelopes", "show", str(created["id"]), api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "Groceries" in result.stdout
    assert "balance" in result.stdout


@pytest.mark.integration
def test_envelopes_show_by_name(authed_session: str, access_token: str) -> None:
    _create_envelope(authed_session, access_token, name="Groceries")
    result = _run_cli("envelopes", "show", "Groceries", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "Groceries" in result.stdout


@pytest.mark.integration
def test_envelopes_show_unknown_yields_user_error(authed_session: str) -> None:
    result = _run_cli("envelopes", "show", "no-such-name", api_url=authed_session)
    assert result.returncode == 1
    assert "not found" in (result.stdout + result.stderr).lower()


@pytest.mark.integration
def test_envelopes_show_ambiguous_name_yields_user_error(
    authed_session: str, access_token: str
) -> None:
    _create_envelope(authed_session, access_token, name="Duplicated")
    _create_envelope(authed_session, access_token, name="Duplicated")
    result = _run_cli("envelopes", "show", "Duplicated", api_url=authed_session)
    assert result.returncode == 1
    # Both the title and the detail mention the ambiguity. The CLI
    # renderer uses "matches multiple" in the title; check for either
    # word so a future copy edit doesn't break the test pointlessly.
    haystack = (result.stdout + result.stderr).lower()
    assert "matches multiple" in haystack or "ambiguous" in haystack


# ---- edit ----------------------------------------------------------


@pytest.mark.integration
def test_envelopes_edit_partial_fields(authed_session: str, access_token: str) -> None:
    created = _create_envelope(authed_session, access_token, name="Groceries")
    result = _run_cli(
        "envelopes",
        "edit",
        str(created["id"]),
        "--budget-amount",
        "300.00",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Updated envelope" in result.stdout
    assert "300.00" in result.stdout


@pytest.mark.integration
def test_envelopes_edit_unknown_yields_user_error(authed_session: str) -> None:
    result = _run_cli(
        "envelopes",
        "edit",
        "no-such-name",
        "--name",
        "X",
        api_url=authed_session,
    )
    assert result.returncode == 1


# ---- deactivate ----------------------------------------------------


@pytest.mark.integration
def test_envelopes_deactivate_with_yes_flag(authed_session: str, access_token: str) -> None:
    created = _create_envelope(authed_session, access_token, name="Groceries")
    result = _run_cli(
        "envelopes",
        "deactivate",
        str(created["id"]),
        "--yes",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Deactivated envelope" in result.stdout

    # No longer appears in list.
    list_result = _run_cli("envelopes", "list", api_url=authed_session)
    assert "No envelopes" in list_result.stdout


@pytest.mark.integration
def test_envelopes_deactivate_aborted_via_prompt(authed_session: str, access_token: str) -> None:
    created = _create_envelope(authed_session, access_token, name="Groceries")
    # Send "n\n" to the confirm prompt.
    result = _run_cli(
        "envelopes",
        "deactivate",
        str(created["id"]),
        api_url=authed_session,
        stdin="n\n",
    )
    assert result.returncode == 0, result.stderr
    assert "Aborted" in result.stdout


# ---- unauthenticated -----------------------------------------------


@pytest.mark.integration
def test_envelopes_list_unauthenticated_yields_auth_error(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    result = _run_cli("envelopes", "list", api_url=live_api)
    assert result.returncode == 2  # EXIT_AUTH

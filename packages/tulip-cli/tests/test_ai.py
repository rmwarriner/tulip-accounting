"""End-to-end tests for ``tulip ai`` (P6.1, ADR-0005).

The CLI flow:

1. ``tulip ai set-key --provider X --key-stdin``  ← stdin
2. ``tulip ai list-keys``                          → ``["X"]``
3. ``tulip ai status``                             → policy + key-providers visible
4. ``tulip ai preview``                            → byte-faithful redacted payload
5. ``tulip ai forget-key --provider X``            → list empty again

All tests run against the ``live_api`` fixture; no real provider is touched.
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


@pytest.fixture
def token_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "tokens.json"
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(path))
    return path


@pytest.fixture
def authed(live_api: str, token_file: Path) -> str:
    """Register + login an admin user; return the api_url."""
    httpx.post(
        f"{live_api}/v1/auth/register",
        json={
            "email": "admin@example.com",
            "password": _PASSWORD,
            "display_name": "Admin",
            "household_name": "AI House",
        },
        timeout=10,
    ).raise_for_status()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--api-url",
            live_api,
            "auth",
            "login",
            "--email",
            "admin@example.com",
            "--password-stdin",
        ],
        check=True,
        capture_output=True,
        text=True,
        input=f"{_PASSWORD}\n",
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    return live_api


@pytest.mark.integration
def test_ai_set_key_list_forget_round_trip(authed: str) -> None:
    set_result = _run_cli(
        "ai",
        "set-key",
        "--provider",
        "anthropic",
        "--key-stdin",
        api_url=authed,
        stdin="sk-roundtrip-test\n",
    )
    assert set_result.returncode == 0, set_result.stderr
    assert "anthropic" in set_result.stdout

    list_result = _run_cli("--json", "ai", "list-keys", api_url=authed)
    body = json.loads(list_result.stdout)
    assert body["providers"] == ["anthropic"]
    # Critically: the key bytes are NEVER in the response.
    assert "sk-roundtrip-test" not in list_result.stdout

    forget_result = _run_cli("ai", "forget-key", "--provider", "anthropic", api_url=authed)
    assert forget_result.returncode == 0, forget_result.stderr

    after = json.loads(_run_cli("--json", "ai", "list-keys", api_url=authed).stdout)
    assert after["providers"] == []


@pytest.mark.integration
def test_ai_status_renders_defaults(authed: str) -> None:
    result = _run_cli("ai", "status", api_url=authed)
    assert result.returncode == 0, result.stderr
    assert "AI status" in result.stdout
    assert "categorize" in result.stdout
    # Fresh household has no providers configured.
    assert "(none)" in result.stdout


@pytest.mark.integration
def test_ai_preview_renders_payload(authed: str) -> None:
    # Seed two expense accounts so the chart isn't empty.
    httpx.post(
        f"{authed}/v1/auth/login",
        json={"email": "admin@example.com", "password": _PASSWORD},
        timeout=10,
    )
    # Use the CLI to add accounts so we don't have to thread a token here.
    _run_cli(
        "accounts",
        "add",
        "--code",
        "5100",
        "--name",
        "Groceries",
        "--type",
        "expense",
        "--currency",
        "USD",
        api_url=authed,
    )

    result = _run_cli(
        "--json",
        "ai",
        "preview",
        "--description",
        "WHOLE FOODS MARKET",
        "--amount",
        "-87.42",
        "--currency",
        "USD",
        "--date",
        "2026-05-03",
        api_url=authed,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["profile"] == "default"
    assert body["payload"]["task"] == "categorize"
    assert body["payload"]["line"]["description"] == "WHOLE FOODS MARKET"
    assert body["payload"]["line"]["amount"] == "-87.42"
    chart_codes = sorted(c["code"] for c in body["payload"]["chart"])
    assert chart_codes == ["5100"]


@pytest.mark.integration
def test_ai_suggest_budget_without_key_reports_error(authed: str) -> None:
    """``tulip ai suggest-budget`` surfaces the structured error when no key is configured."""
    # Need a pool + envelope to point the command at.
    httpx_token = httpx.post(
        f"{authed}/v1/auth/login",
        json={"email": "admin@example.com", "password": _PASSWORD},
        timeout=10,
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {httpx_token}"}
    env = httpx.post(
        f"{authed}/v1/envelopes",
        headers=headers,
        json={
            "name": "Groceries",
            "currency": "USD",
            "budget_period": "monthly",
            "rollover_policy": "reset",
            "budget_amount": "100.00",
        },
        timeout=10,
    ).json()
    result = _run_cli("ai", "suggest-budget", "--envelope", env["id"], api_url=authed)
    assert result.returncode == 1
    assert "no api key" in result.stderr.lower()

"""End-to-end tests for ``tulip periods`` (#136)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

_PASSWORD = "long-enough-password"


def _run_cli(*args: str, api_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tulip_cli", "--api-url", api_url, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.fixture
def token_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "tokens.json"
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(path))
    return path


@pytest.fixture
def authed(live_api: str, token_file: Path) -> str:
    """Register + login so the CLI has a usable token in the store."""
    httpx.post(
        f"{live_api}/v1/auth/register",
        json={
            "email": "p@example.com",
            "password": _PASSWORD,
            "display_name": "P",
            "household_name": "P House",
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
            "p@example.com",
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
def test_periods_list_renders_seeded_period(authed: str) -> None:
    result = _run_cli("periods", "list", api_url=authed)
    assert result.returncode == 0, result.stderr
    # Registration auto-seeds the current-year period; the table shows it.
    assert "open" in result.stdout


@pytest.mark.integration
def test_periods_list_json_output(authed: str) -> None:
    result = _run_cli("--json", "periods", "list", api_url=authed)
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["status"] == "open"


@pytest.mark.integration
def test_periods_close_then_reopen_round_trip(authed: str) -> None:
    """Acceptance: close + reopen round-trip works end-to-end."""
    listing = _run_cli("--json", "periods", "list", api_url=authed)
    period_id = json.loads(listing.stdout)[0]["id"]

    closed = _run_cli("periods", "close", period_id, api_url=authed)
    assert closed.returncode == 0, closed.stderr
    assert "soft_closed" in closed.stdout

    after_close = _run_cli("--json", "periods", "list", api_url=authed)
    assert json.loads(after_close.stdout)[0]["status"] == "soft_closed"

    reopened = _run_cli("periods", "reopen", period_id, api_url=authed)
    assert reopened.returncode == 0, reopened.stderr
    assert "open" in reopened.stdout

    after_reopen = _run_cli("--json", "periods", "list", api_url=authed)
    assert json.loads(after_reopen.stdout)[0]["status"] == "open"


@pytest.mark.integration
def test_periods_close_unknown_id_renders_problem(authed: str) -> None:
    result = _run_cli("periods", "close", "00000000-0000-0000-0000-000000000000", api_url=authed)
    assert result.returncode != 0, (result.stdout, result.stderr)
    # CliError renders the problem detail to stderr.
    assert "period" in result.stderr.lower()

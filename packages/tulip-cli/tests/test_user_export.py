"""E2E tests for ``tulip user export`` + ``tulip household member-export`` (#241).

Run against the ``live_api`` fixture — register + login, then exercise
the data-export CLI surface end to end.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from _cli_asserts import assert_cli_usage_error

_PASSWORD = "long-enough-password"


def _run_cli(*args: str, api_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tulip_cli", "--api-url", api_url, *args],
        check=False,
        capture_output=True,
        text=True,
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
            "household_name": "Export House",
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
def test_user_export_emits_parseable_json(authed: str) -> None:
    result = _run_cli("user", "export", api_url=authed)
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["user"]["email"] == "admin@example.com"
    assert body["user"]["password_hash"] == "***"
    assert "sessions" in body
    assert "transactions_created" in body


@pytest.mark.integration
def test_household_member_export_unknown_user_renders_404(authed: str) -> None:
    result = _run_cli(
        "household",
        "member-export",
        "00000000-0000-0000-0000-000000000000",
        api_url=authed,
    )
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    assert "not found" in combined or "user.not_found" in combined


@pytest.mark.integration
def test_household_member_export_rejects_non_uuid_arg(authed: str) -> None:
    result = _run_cli("household", "member-export", "not-a-uuid", api_url=authed)
    assert_cli_usage_error(result)

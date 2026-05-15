"""End-to-end tests for ``tulip admin`` (#245).

Covers:
- ``tulip admin audit-policy show`` prints all five tiers.
- ``tulip admin audit-policy set ledger_days 1825`` round-trips.
- ``tulip admin audit-prune`` returns per-tier counts.
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
    """Register + login an admin; return the api_url."""
    httpx.post(
        f"{live_api}/v1/auth/register",
        json={
            "email": "admin@example.com",
            "password": _PASSWORD,
            "display_name": "Admin",
            "household_name": "Admin House",
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
def test_audit_policy_show_renders_code_defaults(authed: str) -> None:
    """Fresh household: every tier at its code default."""
    result = _run_cli("admin", "audit-policy", "show", api_url=authed)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "ledger_days" in out
    assert "2555" in out  # 7y default
    assert "auth_days" in out
    assert "90" in out


@pytest.mark.integration
def test_audit_policy_set_round_trips(authed: str) -> None:
    """Set ledger_days = 1825; subsequent show reflects it."""
    set_result = _run_cli("admin", "audit-policy", "set", "ledger_days", "1825", api_url=authed)
    assert set_result.returncode == 0, set_result.stderr
    assert "1825" in set_result.stdout

    show_result = _run_cli("--json", "admin", "audit-policy", "show", api_url=authed)
    body = json.loads(show_result.stdout)
    assert body["ledger_days"] == 1825
    # Other tiers still at defaults.
    assert body["auth_days"] == 90


@pytest.mark.integration
def test_audit_policy_set_rejects_unknown_tier(authed: str) -> None:
    """An invalid tier name fails at the CLI layer with exit code 1."""
    result = _run_cli("admin", "audit-policy", "set", "made_up_tier", "30", api_url=authed)
    assert result.returncode == 1
    assert "tier must be one of" in result.stderr


@pytest.mark.integration
def test_audit_policy_set_rejects_non_positive_days(authed: str) -> None:
    """Zero or negative day-counts are rejected before the request fires."""
    result = _run_cli("admin", "audit-policy", "set", "ledger_days", "0", api_url=authed)
    assert result.returncode == 1
    assert "positive integer" in result.stderr


@pytest.mark.integration
def test_audit_prune_returns_zero_on_fresh_household(authed: str) -> None:
    """A fresh household has no rows old enough to prune."""
    result = _run_cli("admin", "audit-prune", api_url=authed)
    assert result.returncode == 0, result.stderr
    assert "0 row(s) deleted" in result.stdout
    # Per-tier table renders all five tiers.
    for tier in ("ledger_days", "auth_days", "ai_days", "admin_days", "default_days"):
        assert tier in result.stdout

"""Tests for ``tulip doctor`` (#135).

Two layers:

* Unit tests for the aggregator and the individual check helpers — fast,
  cover the matrix of pass/warn/fail combinations directly.
* Integration tests that spawn ``tulip doctor`` as a subprocess against
  the ``live_api`` fixture — confirm exit codes and stdout/stderr
  separation actually behave the way ``docker compose`` health probes
  expect.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from tulip_cli.commands.doctor import (
    CheckResult,
    _check_attachment_root,
    _check_master_key,
    _check_migration_head,
    aggregate_status,
)

_PASSWORD = "long-enough-password"


# ---- Unit tests ---------------------------------------------------------


class TestAggregateStatus:
    def test_empty_returns_pass(self) -> None:
        assert aggregate_status([]) == "pass"

    def test_all_pass_returns_pass(self) -> None:
        rs = [CheckResult("a", "pass", ""), CheckResult("b", "pass", "")]
        assert aggregate_status(rs) == "pass"

    def test_any_warning_promotes_to_warn(self) -> None:
        rs = [CheckResult("a", "pass", ""), CheckResult("b", "warn", "")]
        assert aggregate_status(rs) == "warn"

    def test_any_failure_promotes_to_fail(self) -> None:
        rs = [
            CheckResult("a", "pass", ""),
            CheckResult("b", "warn", ""),
            CheckResult("c", "fail", ""),
        ]
        assert aggregate_status(rs) == "fail"


class TestIndividualChecks:
    def test_master_key_ephemeral_is_failure(self) -> None:
        result = _check_master_key({"master_key_source": "ephemeral"})
        assert result.status == "fail"
        assert "ephemeral" in result.message.lower()

    def test_master_key_env_or_file_is_pass(self) -> None:
        for src in ("env", "file"):
            result = _check_master_key({"master_key_source": src})
            assert result.status == "pass"
            assert src in result.message

    def test_migration_head_match_is_pass(self) -> None:
        result = _check_migration_head({"alembic_head_match": True, "alembic_head_in_db": "abc123"})
        assert result.status == "pass"
        assert "abc123" in result.message

    def test_migration_head_mismatch_is_warning(self) -> None:
        result = _check_migration_head(
            {
                "alembic_head_match": False,
                "alembic_head_in_db": "old123",
                "alembic_head_expected": "new456",
            }
        )
        assert result.status == "warn"
        assert "alembic upgrade head" in result.message.lower()

    def test_attachment_root_writable_is_pass(self) -> None:
        result = _check_attachment_root({"attachment_root_writable": True})
        assert result.status == "pass"

    def test_attachment_root_unwritable_is_failure(self) -> None:
        result = _check_attachment_root({"attachment_root_writable": False})
        assert result.status == "fail"


# ---- Integration tests --------------------------------------------------


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
    """Per-test JSON-file token store; never touch the OS keyring."""
    path = tmp_path / "tokens.json"
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(path))
    return path


def _register_and_login(api_url: str) -> None:
    """Register a user and login so the token store is populated for the doctor."""
    httpx.post(
        f"{api_url}/v1/auth/register",
        json={
            "email": "doc@example.com",
            "password": _PASSWORD,
            "display_name": "Doctor Tester",
            "household_name": "Doctor House",
        },
        timeout=10,
    ).raise_for_status()
    # Use the CLI to login so it writes the token file in our chosen format.
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--api-url",
            api_url,
            "auth",
            "login",
            "--email",
            "doc@example.com",
            "--password-stdin",
        ],
        check=True,
        capture_output=True,
        text=True,
        input=f"{_PASSWORD}\n",
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.integration
def test_doctor_green_path_against_healthy_install(live_api: str, token_file: Path) -> None:
    _register_and_login(live_api)
    result = _run_cli("doctor", api_url=live_api)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "all checks passed" in result.stdout.lower()


@pytest.mark.integration
def test_doctor_json_output_against_healthy_install(live_api: str, token_file: Path) -> None:
    _register_and_login(live_api)
    result = _run_cli("--json", "doctor", api_url=live_api)
    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["overall"] == "pass"
    assert payload["exit_code"] == 0
    assert payload["summary"]["failed"] == 0
    assert payload["summary"]["warned"] == 0
    # Five checks: reachability + diagnostics fetch + key + migrations + attachment + token store.
    assert payload["summary"]["total"] == 6
    names = {c["name"] for c in payload["checks"]}
    assert "API reachability" in names
    assert "Master-key loaded" in names


@pytest.mark.integration
def test_doctor_warns_when_token_store_empty(live_api: str, token_file: Path) -> None:
    """No login yet → the token-store check is a warning (exit 1).

    All API-side checks pass, so the overall severity is `warn` — exactly the
    case the issue's acceptance calls out.
    """
    # token_file fixture sets TULIP_TOKEN_STORE but does not populate it.
    assert not token_file.exists()
    result = _run_cli("doctor", api_url=live_api)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "token store" in result.stderr.lower()


@pytest.mark.integration
def test_doctor_fails_when_api_unreachable(token_file: Path) -> None:
    """Point at a port nobody's listening on → exit 2 (hard failure)."""
    bogus_url = "http://127.0.0.1:1"  # nobody listens on port 1
    result = _run_cli("doctor", api_url=bogus_url)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "api reachability" in result.stderr.lower()
    assert "failed" in result.stderr.lower()

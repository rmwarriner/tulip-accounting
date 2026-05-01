"""End-to-end tests for ``tulip auth login`` (with MFA + recovery), ``logout``, and ``status``.

Each test spawns the API via the ``live_api`` fixture and runs the
``tulip`` console script as a subprocess. ``TULIP_TOKEN_STORE`` is set
to a per-test JSON file so the OS keyring is never touched.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pyotp
import pytest

from tulip_cli.auth.tokens import TokenStore

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


def _register(api_url: str, email: str) -> None:
    r = httpx.post(
        f"{api_url}/v1/auth/register",
        json={
            "email": email,
            "password": _PASSWORD,
            "display_name": "Test User",
            "household_name": "Test Household",
        },
        timeout=10,
    )
    r.raise_for_status()


def _login_for_access_token(api_url: str, email: str) -> str:
    r = httpx.post(
        f"{api_url}/v1/auth/login",
        json={"email": email, "password": _PASSWORD},
        timeout=10,
    )
    r.raise_for_status()
    return str(r.json()["access_token"])


def _enroll_mfa(api_url: str, access_token: str) -> tuple[str, list[str]]:
    """Enroll TOTP MFA and return (secret, recovery_codes)."""
    enroll = httpx.post(
        f"{api_url}/v1/auth/mfa/enroll",
        headers={"authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    enroll.raise_for_status()
    secret = str(enroll.json()["secret"])
    code = pyotp.TOTP(secret).now()
    verify = httpx.post(
        f"{api_url}/v1/auth/mfa/verify",
        json={"code": code},
        headers={"authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    verify.raise_for_status()
    recovery_codes = list(verify.json()["recovery_codes"])
    return secret, recovery_codes


@pytest.fixture
def token_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test JSON-file token store; the OS keyring is never touched."""
    path = tmp_path / "tokens.json"
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(path))
    return path


@pytest.mark.integration
def test_login_happy_path_stores_tokens(live_api: str, token_file: Path) -> None:
    _register(live_api, "alice@example.com")
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
    assert "alice@example.com" in result.stdout

    store = TokenStore(file_path=token_file)
    tokens = store.load(live_api)
    assert tokens is not None
    assert tokens.email == "alice@example.com"
    assert tokens.access_token
    assert tokens.refresh_token


@pytest.mark.integration
def test_login_with_mfa_totp_succeeds(live_api: str, token_file: Path) -> None:
    _register(live_api, "alice@example.com")
    access = _login_for_access_token(live_api, "alice@example.com")
    secret, _ = _enroll_mfa(live_api, access)

    code = pyotp.TOTP(secret).now()
    result = _run_cli(
        "auth",
        "login",
        "--email",
        "alice@example.com",
        "--password-stdin",
        "--code-stdin",
        api_url=live_api,
        stdin=f"{_PASSWORD}\n{code}\n",
    )
    assert result.returncode == 0, result.stderr
    store = TokenStore(file_path=token_file)
    assert store.load(live_api) is not None


@pytest.mark.integration
def test_login_with_recovery_code_succeeds(live_api: str, token_file: Path) -> None:
    _register(live_api, "alice@example.com")
    access = _login_for_access_token(live_api, "alice@example.com")
    _, recovery_codes = _enroll_mfa(live_api, access)

    result = _run_cli(
        "auth",
        "login",
        "--email",
        "alice@example.com",
        "--password-stdin",
        "--code-stdin",
        "--recovery",
        api_url=live_api,
        stdin=f"{_PASSWORD}\n{recovery_codes[0]}\n",
    )
    assert result.returncode == 0, result.stderr
    store = TokenStore(file_path=token_file)
    assert store.load(live_api) is not None


@pytest.mark.integration
def test_login_wrong_password_yields_exit_2(live_api: str, token_file: Path) -> None:
    _register(live_api, "alice@example.com")
    result = _run_cli(
        "auth",
        "login",
        "--email",
        "alice@example.com",
        "--password-stdin",
        api_url=live_api,
        stdin="wrong-password\n",
    )
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "invalid credentials" in result.stderr.lower()
    store = TokenStore(file_path=token_file)
    assert store.load(live_api) is None


@pytest.mark.integration
def test_status_when_logged_out(live_api: str, token_file: Path) -> None:
    result = _run_cli("auth", "status", api_url=live_api)
    assert result.returncode == 0, result.stderr
    assert "Not logged in" in result.stdout


@pytest.mark.integration
def test_status_after_login_shows_identity(live_api: str, token_file: Path) -> None:
    _register(live_api, "alice@example.com")
    _run_cli(
        "auth",
        "login",
        "--email",
        "alice@example.com",
        "--password-stdin",
        api_url=live_api,
        stdin=f"{_PASSWORD}\n",
    )
    result = _run_cli("auth", "status", api_url=live_api)
    assert result.returncode == 0, result.stderr
    assert "alice@example.com" in result.stdout
    assert "household_id" in result.stdout
    assert "role" in result.stdout


@pytest.mark.integration
def test_status_json_mode_emits_machine_readable_state(live_api: str, token_file: Path) -> None:
    _register(live_api, "alice@example.com")
    _run_cli(
        "auth",
        "login",
        "--email",
        "alice@example.com",
        "--password-stdin",
        api_url=live_api,
        stdin=f"{_PASSWORD}\n",
    )
    result = _run_cli("--json", "auth", "status", api_url=live_api)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["logged_in"] is True
    assert payload["email"] == "alice@example.com"
    assert payload["role"] == "admin"
    assert payload["access_expires_in_seconds"] > 0


@pytest.mark.integration
def test_logout_clears_tokens(live_api: str, token_file: Path) -> None:
    _register(live_api, "alice@example.com")
    _run_cli(
        "auth",
        "login",
        "--email",
        "alice@example.com",
        "--password-stdin",
        api_url=live_api,
        stdin=f"{_PASSWORD}\n",
    )
    store = TokenStore(file_path=token_file)
    assert store.load(live_api) is not None

    result = _run_cli("auth", "logout", api_url=live_api)
    assert result.returncode == 0, result.stderr
    assert store.load(live_api) is None

    status_result = _run_cli("auth", "status", api_url=live_api)
    assert "Not logged in" in status_result.stdout


@pytest.mark.integration
def test_logout_when_already_logged_out_is_a_noop(live_api: str, token_file: Path) -> None:
    result = _run_cli("auth", "logout", api_url=live_api)
    assert result.returncode == 0, result.stderr
    assert "already" in result.stdout.lower()

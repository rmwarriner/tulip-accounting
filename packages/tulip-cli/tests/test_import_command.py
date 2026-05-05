"""E2E tests for ``tulip import ofx`` (P5.2.a)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

_PASSWORD = "long-enough-password"

_OFX_FIXTURES = (
    Path(__file__).resolve().parents[2] / "tulip-importers" / "tests" / "fixtures" / "ofx"
)


def _run_cli(
    *args: str,
    api_url: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "tulip_cli", "--api-url", api_url, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
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
    login = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--api-url",
            live_api,
            "auth",
            "login",
            "--email",
            "alice@example.com",
            "--password-stdin",
        ],
        check=False,
        capture_output=True,
        text=True,
        input=f"{_PASSWORD}\n",
        timeout=10,
    )
    assert login.returncode == 0, login.stderr
    return live_api


def _seed_checking(api_url: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--api-url",
            api_url,
            "accounts",
            "add",
            "--name",
            "Checking",
            "--type",
            "asset",
            "--currency",
            "USD",
            "--code",
            "1110",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
def test_import_ofx_happy_path(authed_session: str) -> None:
    _seed_checking(authed_session)
    fixture = _OFX_FIXTURES / "minimal_ofx2.ofx"
    result = _run_cli(
        "import",
        "ofx",
        str(fixture),
        "--account",
        "1110",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Imported 2 statement lines" in result.stdout


@pytest.mark.integration
def test_import_ofx_json_output(authed_session: str) -> None:
    _seed_checking(authed_session)
    fixture = _OFX_FIXTURES / "minimal_ofx2.ofx"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--json",
            "--api-url",
            authed_session,
            "import",
            "ofx",
            str(fixture),
            "--account",
            "1110",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["statement_line_count"] == 2
    assert body["source_format"] == "ofx"


@pytest.mark.integration
def test_import_ofx_missing_file_typer_error(authed_session: str, tmp_path: Path) -> None:
    _seed_checking(authed_session)
    bogus = tmp_path / "does_not_exist.ofx"
    result = _run_cli(
        "import",
        "ofx",
        str(bogus),
        "--account",
        "1110",
        api_url=authed_session,
    )
    # Typer rejects the missing-file argument before the command runs;
    # exit code 2 is the standard "usage error" code.
    assert result.returncode == 2
    assert (
        "does not exist" in (result.stdout + result.stderr).lower()
        or "no such" in (result.stdout + result.stderr).lower()
        or "invalid value" in (result.stdout + result.stderr).lower()
    )


@pytest.mark.integration
def test_import_ofx_unknown_account(authed_session: str) -> None:
    _seed_checking(authed_session)
    fixture = _OFX_FIXTURES / "minimal_ofx2.ofx"
    result = _run_cli(
        "import",
        "ofx",
        str(fixture),
        "--account",
        "no-such-code",
        api_url=authed_session,
    )
    assert result.returncode != 0
    # Account resolver renders the problem; exact code surfaces in stderr.
    assert "account" in (result.stdout + result.stderr).lower()


@pytest.mark.integration
def test_import_qif_happy_path(authed_session: str) -> None:
    _seed_checking(authed_session)
    fixture = _OFX_FIXTURES.parent / "qif" / "minimal.qif"
    result = _run_cli(
        "import",
        "qif",
        str(fixture),
        "--account",
        "1110",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Imported 3 statement lines" in result.stdout


@pytest.mark.integration
def test_import_qif_garbage_returns_problem(authed_session: str, tmp_path: Path) -> None:
    _seed_checking(authed_session)
    bad = tmp_path / "bad.qif"
    bad.write_bytes(b"this is not qif at all")
    result = _run_cli(
        "import",
        "qif",
        str(bad),
        "--account",
        "1110",
        api_url=authed_session,
    )
    assert result.returncode != 0
    assert "qif" in (result.stdout + result.stderr).lower()


@pytest.mark.integration
def test_import_ofx_unauthenticated_exits_2(live_api: str, tmp_path: Path) -> None:
    fixture = _OFX_FIXTURES / "minimal_ofx2.ofx"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--api-url",
            live_api,
            "import",
            "ofx",
            str(fixture),
            "--account",
            "1110",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "TULIP_TOKEN_STORE": str(tmp_path / "no-tokens.json")},
    )
    assert result.returncode == 2, result.stderr

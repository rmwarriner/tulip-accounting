"""End-to-end tests for ``tulip pta {export,import}`` (#415, renamed from journal).

Round-trip is the primary acceptance criterion: export → write to file
→ import — the same payload that survived /v1/pta/export must
re-import as PENDING transactions.
"""

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
        timeout=30,
    )


def _register_and_login(api_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    httpx.post(
        f"{api_url}/v1/auth/register",
        json={
            "email": "alice@example.com",
            "password": _PASSWORD,
            "display_name": "Alice",
            "household_name": "Alice's Household",
        },
        timeout=10,
    ).raise_for_status()
    api_login = httpx.post(
        f"{api_url}/v1/auth/login",
        json={"email": "alice@example.com", "password": _PASSWORD},
        timeout=10,
    )
    api_login.raise_for_status()
    access = str(api_login.json()["access_token"])
    cli_login = _run_cli(
        "auth",
        "login",
        "--email",
        "alice@example.com",
        "--password-stdin",
        api_url=api_url,
        stdin=f"{_PASSWORD}\n",
    )
    assert cli_login.returncode == 0, cli_login.stderr
    return access


def _seed_two_accounts_and_tx(api_url: str, access: str) -> None:
    h = {"authorization": f"Bearer {access}"}
    cash = httpx.post(
        f"{api_url}/v1/accounts",
        json={
            "name": "Cash",
            "type": "asset",
            "currency": "USD",
            "code": "1110",
            "visibility": "shared",
        },
        headers=h,
        timeout=10,
    )
    cash.raise_for_status()
    food = httpx.post(
        f"{api_url}/v1/accounts",
        json={
            "name": "Food",
            "type": "expense",
            "currency": "USD",
            "code": "5100",
            "visibility": "shared",
        },
        headers=h,
        timeout=10,
    )
    food.raise_for_status()
    httpx.post(
        f"{api_url}/v1/transactions",
        json={
            "date": date(2026, 3, 1).isoformat(),
            "description": "Lunch",
            "postings": [
                {"account_id": food.json()["id"], "amount": "8.75", "currency": "USD"},
                {"account_id": cash.json()["id"], "amount": "-8.75", "currency": "USD"},
            ],
        },
        headers=h,
        timeout=10,
    ).raise_for_status()


@pytest.fixture
def authed_session(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
    access = _register_and_login(live_api, tmp_path, monkeypatch)
    return live_api, access


@pytest.mark.integration
def test_export_default_writes_pta_to_stdout(authed_session: tuple[str, str]) -> None:
    api_url, access = authed_session
    _seed_two_accounts_and_tx(api_url, access)
    r = _run_cli("pta", "export", api_url=api_url)
    assert r.returncode == 0, r.stderr
    assert "2026-03-01" in r.stdout
    assert "Lunch" in r.stdout


@pytest.mark.integration
def test_export_to_file(authed_session: tuple[str, str], tmp_path: Path) -> None:
    api_url, access = authed_session
    _seed_two_accounts_and_tx(api_url, access)
    out = tmp_path / "ledger.journal"
    r = _run_cli("pta", "export", "--output", str(out), api_url=api_url)
    assert r.returncode == 0, r.stderr
    assert out.exists()
    assert "Lunch" in out.read_text()
    assert "Wrote" in r.stdout


@pytest.mark.integration
def test_export_date_range_forwarded(authed_session: tuple[str, str]) -> None:
    api_url, access = authed_session
    _seed_two_accounts_and_tx(api_url, access)
    r = _run_cli(
        "pta",
        "export",
        "--start",
        "2026-01-01",
        "--end",
        "2026-02-01",
        api_url=api_url,
    )
    assert r.returncode == 0, r.stderr
    # The seeded tx is dated 2026-03-01, so the range excludes it.
    assert "Lunch" not in r.stdout


@pytest.mark.integration
def test_export_invalid_date_rejected(authed_session: tuple[str, str]) -> None:
    api_url, _ = authed_session
    r = _run_cli("pta", "export", "--start", "13/45/26", api_url=api_url)
    assert r.returncode != 0
    assert "yyyy-mm-dd" in r.stderr.lower()


@pytest.mark.integration
def test_import_roundtrip(authed_session: tuple[str, str], tmp_path: Path) -> None:
    api_url, access = authed_session
    _seed_two_accounts_and_tx(api_url, access)
    out = tmp_path / "ledger.journal"
    exp = _run_cli("pta", "export", "--output", str(out), api_url=api_url)
    assert exp.returncode == 0, exp.stderr
    imp = _run_cli("pta", "import", str(out), api_url=api_url)
    assert imp.returncode == 0, imp.stderr
    assert "Imported 1" in imp.stdout
    # Re-imported transaction should land as PENDING (not auto-posted).
    pending = httpx.get(
        f"{api_url}/v1/transactions",
        params={"status": "pending"},
        headers={"authorization": f"Bearer {access}"},
        timeout=10,
    )
    pending.raise_for_status()
    descs = [t["description"] for t in pending.json()]
    assert "Lunch" in descs


@pytest.mark.integration
def test_import_parse_error_surfaces(authed_session: tuple[str, str], tmp_path: Path) -> None:
    api_url, _ = authed_session
    bad = tmp_path / "bad.journal"
    bad.write_text("not-a-date no parse possible\n    foo  bar\n")
    r = _run_cli("pta", "import", str(bad), api_url=api_url)
    assert r.returncode != 0
    combined = (r.stdout + r.stderr).lower()
    assert "parse" in combined or "pta.parse_failed" in combined


@pytest.mark.integration
def test_import_json_passes_through_response(
    authed_session: tuple[str, str], tmp_path: Path
) -> None:
    api_url, access = authed_session
    _seed_two_accounts_and_tx(api_url, access)
    out = tmp_path / "ledger.journal"
    _run_cli("pta", "export", "--output", str(out), api_url=api_url)
    r = _run_cli("--json", "pta", "import", str(out), api_url=api_url)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["created"] >= 1
    assert "transaction_ids" in payload


@pytest.mark.integration
def test_pta_unauthenticated_fails_clearly(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    r = _run_cli("pta", "export", api_url=live_api)
    assert r.returncode == 2
    assert "not logged in" in r.stderr.lower() or "log in" in r.stderr.lower()

"""End-to-end tests for ``tulip reports`` (P7.1.b).

Each subcommand is a thin wrapper over a ``/v1/reports/<name>``
endpoint plus the shared format/output handling, so a representative
sample (trial-balance + audit-log + custom-query) exercises every
branch of the helper:

* default ``--format json`` to stdout,
* ``--format html`` to stdout,
* ``--format pdf`` requiring ``--output``,
* ``--format csv`` requiring ``--output``,
* per-report options forwarded as query params,
* auth error rendering.
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
    api_login = httpx.post(
        f"{api_url}/v1/auth/login",
        json={"email": "alice@example.com", "password": _PASSWORD},
        timeout=10,
    )
    api_login.raise_for_status()
    return str(api_login.json()["access_token"])


def _seed_one_tx(api_url: str, access: str) -> None:
    """Create cash + food + one posted transaction so reports have content."""
    cash = httpx.post(
        f"{api_url}/v1/accounts",
        json={
            "name": "Cash",
            "type": "asset",
            "currency": "USD",
            "code": "1110",
            "visibility": "shared",
        },
        headers={"authorization": f"Bearer {access}"},
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
        headers={"authorization": f"Bearer {access}"},
        timeout=10,
    )
    food.raise_for_status()
    httpx.post(
        f"{api_url}/v1/transactions",
        json={
            "date": date(2026, 3, 1).isoformat(),
            "description": "Grocery store",
            "postings": [
                {"account_id": food.json()["id"], "amount": "12.50", "currency": "USD"},
                {"account_id": cash.json()["id"], "amount": "-12.50", "currency": "USD"},
            ],
        },
        headers={"authorization": f"Bearer {access}"},
        timeout=10,
    ).raise_for_status()


@pytest.fixture
def authed_session(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
    access = _register_and_login(live_api, tmp_path, monkeypatch)
    return live_api, access


@pytest.mark.integration
def test_trial_balance_default_json_to_stdout(authed_session: tuple[str, str]) -> None:
    api_url, access = authed_session
    _seed_one_tx(api_url, access)
    r = _run_cli("reports", "trial-balance", api_url=api_url)
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout)
    assert "rows" in body and "totals_by_currency" in body


@pytest.mark.integration
def test_trial_balance_html_to_stdout(authed_session: tuple[str, str]) -> None:
    api_url, access = authed_session
    _seed_one_tx(api_url, access)
    r = _run_cli("reports", "trial-balance", "--format", "html", api_url=api_url)
    assert r.returncode == 0, r.stderr
    assert "<html" in r.stdout.lower() or "<!doctype" in r.stdout.lower()


@pytest.mark.integration
def test_trial_balance_pdf_requires_output(authed_session: tuple[str, str]) -> None:
    api_url, _ = authed_session
    r = _run_cli("reports", "trial-balance", "--format", "pdf", api_url=api_url)
    assert r.returncode != 0
    assert "--output" in r.stderr.lower() or "requires" in r.stderr.lower()


@pytest.mark.integration
def test_trial_balance_csv_writes_file(authed_session: tuple[str, str], tmp_path: Path) -> None:
    api_url, access = authed_session
    _seed_one_tx(api_url, access)
    out = tmp_path / "tb.csv"
    r = _run_cli(
        "reports",
        "trial-balance",
        "--format",
        "csv",
        "--output",
        str(out),
        api_url=api_url,
    )
    assert r.returncode == 0, r.stderr
    assert out.exists()
    text = out.read_text()
    assert "code" in text.lower() or "balance" in text.lower()
    assert "Wrote" in r.stdout


@pytest.mark.integration
def test_trial_balance_as_of_forwarded(authed_session: tuple[str, str]) -> None:
    api_url, access = authed_session
    _seed_one_tx(api_url, access)
    r = _run_cli("reports", "trial-balance", "--as-of", "2026-01-01", api_url=api_url)
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout)
    # Posted on 2026-03-01, so before-2026-01-01 has no rows.
    assert body["rows"] == []


@pytest.mark.integration
def test_trial_balance_invalid_as_of_rejected(authed_session: tuple[str, str]) -> None:
    api_url, _ = authed_session
    r = _run_cli("reports", "trial-balance", "--as-of", "not-a-date", api_url=api_url)
    assert r.returncode != 0
    assert "yyyy-mm-dd" in r.stderr.lower()


@pytest.mark.integration
def test_audit_log_default_paginated(authed_session: tuple[str, str]) -> None:
    api_url, access = authed_session
    _seed_one_tx(api_url, access)
    r = _run_cli("reports", "audit-log", "--limit", "5", api_url=api_url)
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout)
    # audit-log returns rows + metadata; just confirm structure surfaced.
    assert isinstance(body, dict)


@pytest.mark.integration
def test_custom_query_unsafe_sql_surfaces_400(authed_session: tuple[str, str]) -> None:
    api_url, _ = authed_session
    r = _run_cli(
        "reports",
        "custom-query",
        "--sql",
        "DROP TABLE accounts",
        api_url=api_url,
    )
    assert r.returncode != 0
    combined = (r.stdout + r.stderr).lower()
    assert "unsafe" in combined or "rejected" in combined or "not allowed" in combined


@pytest.mark.integration
def test_reports_unauthenticated(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    r = _run_cli("reports", "trial-balance", api_url=live_api)
    assert r.returncode == 2
    assert "not logged in" in r.stderr.lower() or "log in" in r.stderr.lower()

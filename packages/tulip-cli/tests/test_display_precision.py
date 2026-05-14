"""Regression tests for currency-natural display precision (issue #213).

The bug: amounts posted to the API at full storage precision (e.g.
``12.20000000`` from a QIF import) were rendered verbatim by ``tulip
transactions list``, ``tulip transactions show``, ``tulip balance``, and
``tulip imports show``. The display layer should quantise to the
currency's natural minor-unit precision (USD → 2 decimals → ``12.20``).

These end-to-end tests post a transaction whose amount has trailing
zeros beyond USD's two-decimal precision, then assert the CLI renders
``12.20`` and never ``12.20000000``. Strips ANSI before scanning so
Rich's styling can't make the substring miss.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

_PASSWORD = "long-enough-password"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _run_cli(
    *args: str, api_url: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    # COLUMNS=200 keeps Rich from wrapping table cells and breaking the
    # amount substring we're asserting on; see test_import_command.py for
    # the precedent.
    import os

    env = dict(os.environ)
    env.setdefault("COLUMNS", "200")
    return subprocess.run(
        [sys.executable, "-m", "tulip_cli", "--api-url", api_url, *args],
        check=False,
        capture_output=True,
        text=True,
        input=stdin,
        timeout=20,
        env=env,
    )


def _create_account(
    api_url: str,
    access_token: str,
    *,
    code: str,
    name: str,
    type_: str = "asset",
) -> dict[str, object]:
    r = httpx.post(
        f"{api_url}/v1/accounts",
        json={
            "code": code,
            "name": name,
            "type": type_,
            "currency": "USD",
            "visibility": "shared",
        },
        headers={"authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    r.raise_for_status()
    return dict(r.json())


def _post_tx_with_storage_precision(
    api_url: str,
    access_token: str,
    *,
    debit_id: str,
    credit_id: str,
) -> str:
    """Post a USD transaction whose amount carries trailing-zero noise.

    Returns the transaction id so the test can use it as a TXID for
    ``tulip transactions show``.
    """
    r = httpx.post(
        f"{api_url}/v1/transactions",
        json={
            "date": date.today().isoformat(),
            "description": "12.20-precision-noise",
            "postings": [
                {"account_id": debit_id, "amount": "12.20000000", "currency": "USD"},
                {"account_id": credit_id, "amount": "-12.20000000", "currency": "USD"},
            ],
        },
        headers={"authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    r.raise_for_status()
    return str(r.json()["id"])


@pytest.fixture
def authed_session(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
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
    cli_login = _run_cli(
        "auth",
        "login",
        "--email",
        "alice@example.com",
        "--password-stdin",
        api_url=live_api,
        stdin=f"{_PASSWORD}\n",
    )
    assert cli_login.returncode == 0, cli_login.stderr
    api_login = httpx.post(
        f"{live_api}/v1/auth/login",
        json={"email": "alice@example.com", "password": _PASSWORD},
        timeout=10,
    )
    api_login.raise_for_status()
    return live_api, str(api_login.json()["access_token"])


@pytest.mark.integration
def test_transactions_list_renders_currency_natural_precision(
    authed_session: tuple[str, str],
) -> None:
    """`tulip transactions list` must render USD amounts to 2 decimals.

    Regression test for issue #213: a posting persisted at storage
    precision (``12.20000000``) renders as ``12.20`` in the table, not
    as ``12.20000000``.
    """
    api_url, access = authed_session
    cash = _create_account(api_url, access, code="assets:cash", name="Cash")
    food = _create_account(api_url, access, code="expenses:food", name="Food", type_="expense")
    _post_tx_with_storage_precision(
        api_url, access, debit_id=str(food["id"]), credit_id=str(cash["id"])
    )

    result = _run_cli("transactions", "list", api_url=api_url)
    assert result.returncode == 0, result.stderr
    plain = _strip_ansi(result.stdout)
    # Quantised form is present, full-precision form is not.
    assert "12.20" in plain
    assert "12.20000000" not in plain


@pytest.mark.integration
def test_transactions_show_renders_currency_natural_precision(
    authed_session: tuple[str, str],
) -> None:
    """`tulip transactions show TXID` must render postings at 2-decimal USD precision."""
    api_url, access = authed_session
    cash = _create_account(api_url, access, code="assets:cash", name="Cash")
    food = _create_account(api_url, access, code="expenses:food", name="Food", type_="expense")
    tx_id = _post_tx_with_storage_precision(
        api_url, access, debit_id=str(food["id"]), credit_id=str(cash["id"])
    )

    result = _run_cli("transactions", "show", tx_id, api_url=api_url)
    assert result.returncode == 0, result.stderr
    plain = _strip_ansi(result.stdout)
    assert "12.20" in plain
    assert "12.20000000" not in plain


@pytest.mark.integration
def test_balance_renders_currency_natural_precision(
    authed_session: tuple[str, str],
) -> None:
    """`tulip balance` trial-balance must show USD totals at 2 decimals."""
    api_url, access = authed_session
    cash = _create_account(api_url, access, code="assets:cash", name="Cash")
    food = _create_account(api_url, access, code="expenses:food", name="Food", type_="expense")
    _post_tx_with_storage_precision(
        api_url, access, debit_id=str(food["id"]), credit_id=str(cash["id"])
    )

    result = _run_cli("balance", api_url=api_url)
    assert result.returncode == 0, result.stderr
    plain = _strip_ansi(result.stdout)
    assert "12.20" in plain
    assert "12.20000000" not in plain

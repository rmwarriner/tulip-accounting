"""End-to-end tests for ``tulip balance``.

* No argument → trial-balance summary.
* With an ACCOUNT argument (code or UUID) → that account's balance.
* ``--as-of YYYY-MM-DD`` → point-in-time balance.
* ``--json`` → raw API body.
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
        timeout=20,
    )


def _create_account(
    api_url: str,
    access_token: str,
    *,
    code: str | None,
    name: str,
    type_: str = "asset",
) -> dict[str, object]:
    body: dict[str, object] = {
        "name": name,
        "type": type_,
        "currency": "USD",
        "visibility": "shared",
    }
    if code is not None:
        body["code"] = code
    r = httpx.post(
        f"{api_url}/v1/accounts",
        json=body,
        headers={"authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    r.raise_for_status()
    return dict(r.json())


def _post_tx(
    api_url: str,
    access_token: str,
    *,
    debit_id: str,
    credit_id: str,
    amount: str,
    on: date | None = None,
) -> None:
    on = on or date.today()
    r = httpx.post(
        f"{api_url}/v1/transactions",
        json={
            "date": on.isoformat(),
            "description": amount,
            "postings": [
                {"account_id": debit_id, "amount": amount, "currency": "USD"},
                {"account_id": credit_id, "amount": f"-{amount}", "currency": "USD"},
            ],
        },
        headers={"authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    r.raise_for_status()


@pytest.fixture
def authed_session(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
    """Log a user in via the CLI and return (api_url, access_token).

    The access token is also returned so test setup can hit the API
    directly to seed accounts and transactions without going through
    the CLI for every fixture step.
    """
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
def test_balance_no_arg_when_empty(authed_session: tuple[str, str]) -> None:
    api_url, _ = authed_session
    result = _run_cli("balance", api_url=api_url)
    assert result.returncode == 0, result.stderr
    assert "no postings" in result.stdout.lower() or "0.00" in result.stdout


@pytest.mark.integration
def test_balance_no_arg_renders_trial_balance(authed_session: tuple[str, str]) -> None:
    api_url, access = authed_session
    cash = _create_account(api_url, access, code="assets:cash", name="Cash")
    food = _create_account(api_url, access, code="expenses:food", name="Food", type_="expense")
    _post_tx(api_url, access, debit_id=str(food["id"]), credit_id=str(cash["id"]), amount="12.50")

    result = _run_cli("balance", api_url=api_url)
    assert result.returncode == 0, result.stderr
    assert "expenses:food" in result.stdout
    assert "12.50" in result.stdout
    assert "assets:cash" in result.stdout
    assert "-12.50" in result.stdout


@pytest.mark.integration
def test_balance_no_arg_json(authed_session: tuple[str, str]) -> None:
    api_url, access = authed_session
    cash = _create_account(api_url, access, code="assets:cash", name="Cash")
    food = _create_account(api_url, access, code="expenses:food", name="Food", type_="expense")
    _post_tx(api_url, access, debit_id=str(food["id"]), credit_id=str(cash["id"]), amount="5.00")

    result = _run_cli("--json", "balance", api_url=api_url)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "rows" in payload
    assert "totals_by_currency" in payload
    assert "as_of" in payload


@pytest.mark.integration
def test_balance_for_single_account_by_code(authed_session: tuple[str, str]) -> None:
    api_url, access = authed_session
    cash = _create_account(api_url, access, code="assets:cash", name="Cash")
    food = _create_account(api_url, access, code="expenses:food", name="Food", type_="expense")
    _post_tx(api_url, access, debit_id=str(food["id"]), credit_id=str(cash["id"]), amount="20.00")

    result = _run_cli("balance", "expenses:food", api_url=api_url)
    assert result.returncode == 0, result.stderr
    assert "expenses:food" in result.stdout
    assert "20.00" in result.stdout
    assert "USD" in result.stdout


@pytest.mark.integration
def test_balance_for_single_account_by_uuid(authed_session: tuple[str, str]) -> None:
    api_url, access = authed_session
    cash = _create_account(api_url, access, code="assets:cash", name="Cash")
    food = _create_account(api_url, access, code="expenses:food", name="Food", type_="expense")
    _post_tx(api_url, access, debit_id=str(food["id"]), credit_id=str(cash["id"]), amount="3.50")

    result = _run_cli("balance", str(food["id"]), api_url=api_url)
    assert result.returncode == 0, result.stderr
    assert "3.50" in result.stdout


@pytest.mark.integration
def test_balance_respects_as_of(authed_session: tuple[str, str]) -> None:
    api_url, access = authed_session
    cash = _create_account(api_url, access, code="assets:cash", name="Cash")
    food = _create_account(api_url, access, code="expenses:food", name="Food", type_="expense")
    _post_tx(
        api_url,
        access,
        debit_id=str(food["id"]),
        credit_id=str(cash["id"]),
        amount="5.00",
        on=date(2026, 1, 15),
    )
    _post_tx(
        api_url,
        access,
        debit_id=str(food["id"]),
        credit_id=str(cash["id"]),
        amount="7.00",
        on=date(2026, 3, 1),
    )

    early = _run_cli("balance", "expenses:food", "--as-of", "2026-02-01", api_url=api_url)
    assert early.returncode == 0, early.stderr
    assert "5.00" in early.stdout
    assert "7.00" not in early.stdout

    later = _run_cli("balance", "expenses:food", "--as-of", "2026-04-01", api_url=api_url)
    assert "12.00" in later.stdout


def _seed_pending_tx(tmp_path: Path, *, debit_id: str, credit_id: str, amount: str) -> None:
    """Insert a PENDING transaction straight into the spawned API's DB.

    ``POST /v1/transactions`` always promotes to POSTED, so #274's
    --pending path needs a PENDING row planted via the repo. The live
    API's SQLite file lives at ``tmp_path / 'tulip.db'`` (see the
    ``live_api`` conftest fixture); a brief direct connection while
    uvicorn is idle is safe under SQLite's single-writer lock.
    """
    from datetime import date as _date
    from decimal import Decimal
    from uuid import UUID, uuid4

    from sqlalchemy import create_engine, event, select
    from sqlalchemy.orm import sessionmaker

    from tulip_core.money import Money
    from tulip_core.transactions import Posting as DomainPosting
    from tulip_core.transactions import Transaction as DomainTransaction
    from tulip_core.transactions import TransactionStatus as DomainTxStatus
    from tulip_storage.models import Household
    from tulip_storage.repositories import TransactionRepository

    engine = create_engine(f"sqlite:///{tmp_path / 'tulip.db'}", future=True)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    try:
        with sessionmaker(engine)() as s:
            household_id = s.execute(select(Household.id)).scalar_one()
            tx = DomainTransaction(
                id=uuid4(),
                household_id=household_id,
                date=_date.today(),
                description=f"pending {amount}",
                postings=(
                    DomainPosting(
                        id=uuid4(),
                        account_id=UUID(debit_id),
                        amount=Money(Decimal(amount), "USD"),
                    ),
                    DomainPosting(
                        id=uuid4(),
                        account_id=UUID(credit_id),
                        amount=Money(Decimal(f"-{amount}"), "USD"),
                    ),
                ),
                status=DomainTxStatus.PENDING,
            )
            TransactionRepository(s, household_id).save_balanced(tx)
            s.commit()
    finally:
        engine.dispose()


@pytest.mark.integration
def test_balance_pending_flag_folds_in_and_labels_output(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#274: --pending widens the balance and the output says so."""
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
    login = httpx.post(
        f"{live_api}/v1/auth/login",
        json={"email": "alice@example.com", "password": _PASSWORD},
        timeout=10,
    )
    login.raise_for_status()
    access = str(login.json()["access_token"])
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

    cash = _create_account(live_api, access, code="assets:cash", name="Cash")
    food = _create_account(live_api, access, code="expenses:food", name="Food", type_="expense")
    _post_tx(live_api, access, debit_id=str(food["id"]), credit_id=str(cash["id"]), amount="10.00")
    _seed_pending_tx(tmp_path, debit_id=str(food["id"]), credit_id=str(cash["id"]), amount="99.00")

    # Default: posted-only, plain "balance" label.
    default = _run_cli("balance", "expenses:food", api_url=live_api)
    assert default.returncode == 0, default.stderr
    assert "10.00" in default.stdout
    assert "incl. pending" not in default.stdout

    # --pending: widened balance, clearly labelled.
    pending = _run_cli("balance", "expenses:food", "--pending", api_url=live_api)
    assert pending.returncode == 0, pending.stderr
    assert "109.00" in pending.stdout
    assert "incl. pending" in pending.stdout
    assert "1 pending transaction" in pending.stdout

    # Trial-balance view also honours --pending and flags the row.
    trial = _run_cli("balance", "--pending", api_url=live_api)
    assert trial.returncode == 0, trial.stderr
    assert "109.00" in trial.stdout
    assert "(P)" in trial.stdout


@pytest.mark.integration
def test_balance_unknown_code_yields_user_error(authed_session: tuple[str, str]) -> None:
    api_url, _ = authed_session
    result = _run_cli("balance", "no-such-code", api_url=api_url)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "no-such-code" in result.stderr.lower() or "not found" in result.stderr.lower()


@pytest.mark.integration
def test_balance_unauthenticated_fails_clearly(
    live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    result = _run_cli("balance", api_url=live_api)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "not logged in" in result.stderr.lower() or "log in" in result.stderr.lower()

"""E2E tests for ``tulip reconcile`` (P5.4.d).

Mirrors the subprocess-against-live-API pattern from test_import_command.py.
Each test marked ``@pytest.mark.integration``: ``just test`` runs them, but
``just bench`` and unit-test loops can skip via the marker.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from _cli_asserts import assert_cli_usage_error

_PASSWORD = "long-enough-password"
_OFX_FIXTURES = (
    Path(__file__).resolve().parents[2] / "tulip-importers" / "tests" / "fixtures" / "ofx"
)


def _run_cli(
    *args: str, api_url: str, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    # Give Rich a wide enough terminal that Typer's usage / error panels
    # don't truncate mid-word — CI runs at a narrower default and was
    # silently losing the assertion-target substring.
    env.setdefault("COLUMNS", "200")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "tulip_cli", "--api-url", api_url, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


@pytest.fixture
def authed_session(live_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Register + login + seed a checking account + upload an OFX batch."""
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


def _api_token(token_store: Path, api_url: str) -> str:
    """Read the access token from the CLI's token store. Used to make raw API calls."""
    data = json.loads(token_store.read_text())
    # Layout: {api_url: json_string_of_TokenSet}.
    first_value = next(iter(data.values()))
    payload = json.loads(first_value) if isinstance(first_value, str) else first_value
    return str(payload["access_token"])


@pytest.fixture
def session_setup(authed_session: str, tmp_path: Path) -> dict[str, str]:
    """Seed: checking + expense accounts, OFX batch, two POSTED ledger txs that match."""
    token_store = tmp_path / "tokens.json"
    token = _api_token(token_store, authed_session)
    auth_h = {"Authorization": f"Bearer {token}"}

    checking = httpx.post(
        f"{authed_session}/v1/accounts",
        headers=auth_h,
        json={"name": "Checking", "type": "asset", "currency": "USD", "code": "1110"},
        timeout=10,
    ).json()
    expense = httpx.post(
        f"{authed_session}/v1/accounts",
        headers=auth_h,
        json={"name": "Misc", "type": "expense", "currency": "USD", "code": "5100"},
        timeout=10,
    ).json()

    body = (_OFX_FIXTURES / "minimal_ofx2.ofx").read_bytes()
    batch = httpx.post(
        f"{authed_session}/v1/imports",
        headers=auth_h,
        files={"file": ("x.ofx", body, "application/x-ofx")},
        data={"account_id": checking["id"], "source_format": "ofx"},
        timeout=10,
    ).json()

    # Two POSTED ledger txs matching the OFX lines.
    httpx.post(
        f"{authed_session}/v1/transactions",
        headers=auth_h,
        json={
            "date": "2026-05-12",
            "description": "PAYPAL",
            "postings": [
                {"account_id": checking["id"], "amount": "-42.17", "currency": "USD"},
                {"account_id": expense["id"], "amount": "42.17", "currency": "USD"},
            ],
        },
        timeout=10,
    ).raise_for_status()
    httpx.post(
        f"{authed_session}/v1/transactions",
        headers=auth_h,
        json={
            "date": "2026-05-15",
            "description": "PAYROLL",
            "postings": [
                {"account_id": checking["id"], "amount": "1500.00", "currency": "USD"},
                {"account_id": expense["id"], "amount": "-1500.00", "currency": "USD"},
            ],
        },
        timeout=10,
    ).raise_for_status()

    return {
        "api_url": authed_session,
        "checking_id": checking["id"],
        "expense_id": expense["id"],
        "batch_id": batch["id"],
        "token": token,
    }


# ---- create / list / show -----------------------------------------------


@pytest.mark.integration
def test_reconcile_create_happy_path(session_setup: dict[str, str]) -> None:
    result = _run_cli(
        "reconcile",
        "create",
        "--account",
        "1110",
        "--batch",
        session_setup["batch_id"],
        "--period",
        "2026-05-01..2026-05-31",
        "--starting",
        "0.00",
        "--ending",
        "1457.83",
        api_url=session_setup["api_url"],
    )
    assert result.returncode == 0, result.stderr
    assert "Created reconciliation" in result.stdout


@pytest.mark.integration
def test_reconcile_create_invalid_period_returns_2(session_setup: dict[str, str]) -> None:
    result = _run_cli(
        "reconcile",
        "create",
        "--account",
        "1110",
        "--batch",
        session_setup["batch_id"],
        "--period",
        "not-a-period",
        "--starting",
        "0.00",
        "--ending",
        "1457.83",
        api_url=session_setup["api_url"],
    )
    assert_cli_usage_error(result, contains="period")


@pytest.mark.integration
def test_reconcile_list_returns_created(session_setup: dict[str, str]) -> None:
    create = _run_cli(
        "reconcile",
        "create",
        "--account",
        "1110",
        "--batch",
        session_setup["batch_id"],
        "--period",
        "2026-05-01..2026-05-31",
        "--starting",
        "0.00",
        "--ending",
        "1457.83",
        "--json",
        api_url=session_setup["api_url"],
    )
    # When --json is global, it goes BEFORE the subcommand.
    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--json",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "create",
            "--account",
            "1110",
            "--batch",
            session_setup["batch_id"],
            "--period",
            "2026-05-01..2026-05-31",
            "--starting",
            "0.00",
            "--ending",
            "1457.83",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert create.returncode == 0, create.stderr
    recon_id = json.loads(create.stdout)["id"]

    listed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--json",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "list",
            "--account",
            "1110",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert listed.returncode == 0, listed.stderr
    items = json.loads(listed.stdout)["items"]
    assert any(item["id"] == recon_id for item in items)


@pytest.mark.integration
def test_reconcile_show_renders_four_sections(session_setup: dict[str, str]) -> None:
    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--json",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "create",
            "--account",
            "1110",
            "--batch",
            session_setup["batch_id"],
            "--period",
            "2026-05-01..2026-05-31",
            "--starting",
            "0.00",
            "--ending",
            "1457.83",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    recon_id = json.loads(create.stdout)["id"]

    show = _run_cli("reconcile", "show", recon_id, api_url=session_setup["api_url"])
    assert show.returncode == 0, show.stderr
    out = show.stdout
    assert "Reconciliation" in out
    assert "Matches" in out
    assert "Unmatched statement lines" in out
    assert "Unmatched ledger transactions" in out


# ---- auto-match / match / reject ----------------------------------------


# ---- interactive wizard --------------------------------------------------


def _create_recon(session_setup: dict[str, str]) -> str:
    """Create a recon envelope and return its UUID."""
    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--json",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "create",
            "--account",
            "1110",
            "--batch",
            session_setup["batch_id"],
            "--period",
            "2026-05-01..2026-05-31",
            "--starting",
            "0.00",
            "--ending",
            "1457.83",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert create.returncode == 0, create.stderr
    return str(json.loads(create.stdout)["id"])


@pytest.mark.integration
def test_reconcile_interactive_no_matches_hints_auto_match(
    session_setup: dict[str, str],
) -> None:
    """If no auto-matches exist yet, the wizard tells the user to run auto-match."""
    recon_id = _create_recon(session_setup)
    result = _run_cli(
        "reconcile",
        "interactive",
        recon_id,
        api_url=session_setup["api_url"],
    )
    assert result.returncode == 0, result.stderr
    assert "auto-match" in result.stdout.lower()


@pytest.mark.integration
def test_reconcile_interactive_accept_all(session_setup: dict[str, str]) -> None:
    """Pipe 'a\\na\\n' to accept both auto-matched candidates; summary should report 2 accepted."""
    recon_id = _create_recon(session_setup)
    # Pre-run auto-match so there are candidates to walk through.
    auto = _run_cli("reconcile", "auto-match", recon_id, api_url=session_setup["api_url"])
    assert auto.returncode == 0, auto.stderr

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "interactive",
            recon_id,
        ],
        check=False,
        capture_output=True,
        text=True,
        input="a\na\n",
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "2 accepted" in result.stdout
    assert "0 rejected" in result.stdout

    # And complete should still work since accepted matches are preserved.
    complete = _run_cli("reconcile", "complete", recon_id, api_url=session_setup["api_url"])
    assert complete.returncode == 0, complete.stderr


@pytest.mark.integration
def test_reconcile_interactive_reject_then_quit(session_setup: dict[str, str]) -> None:
    """Reject one, quit; the rejected match should be gone from the inbox."""
    recon_id = _create_recon(session_setup)
    auto = _run_cli("reconcile", "auto-match", recon_id, api_url=session_setup["api_url"])
    assert auto.returncode == 0, auto.stderr

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "interactive",
            recon_id,
        ],
        check=False,
        capture_output=True,
        text=True,
        input="r\nq\n",
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "1 rejected" in result.stdout

    # And the inbox should now show one fewer match.
    show = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--json",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "show",
            recon_id,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    body = json.loads(show.stdout)
    assert len(body["matches"]) == 1  # one remained (the one we quit on)


@pytest.mark.integration
def test_reconcile_auto_match_and_complete(session_setup: dict[str, str]) -> None:
    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--json",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "create",
            "--account",
            "1110",
            "--batch",
            session_setup["batch_id"],
            "--period",
            "2026-05-01..2026-05-31",
            "--starting",
            "0.00",
            "--ending",
            "1457.83",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    recon_id = json.loads(create.stdout)["id"]

    auto = _run_cli("reconcile", "auto-match", recon_id, api_url=session_setup["api_url"])
    assert auto.returncode == 0, auto.stderr
    assert "Auto-matched" in auto.stdout

    complete = _run_cli("reconcile", "complete", recon_id, api_url=session_setup["api_url"])
    assert complete.returncode == 0, complete.stderr
    assert "Completed reconciliation" in complete.stdout


@pytest.mark.integration
def test_reconcile_complete_unbalanced_returns_409(session_setup: dict[str, str]) -> None:
    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--json",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "create",
            "--account",
            "1110",
            "--batch",
            session_setup["batch_id"],
            "--period",
            "2026-05-01..2026-05-31",
            "--starting",
            "0.00",
            "--ending",
            "1457.83",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    recon_id = json.loads(create.stdout)["id"]
    # Don't auto-match — complete must fail with reconciliation.unbalanced.
    result = _run_cli("reconcile", "complete", recon_id, api_url=session_setup["api_url"])
    assert result.returncode != 0
    assert "balance" in (result.stdout + result.stderr).lower()


# ---- carry-forward + delete ---------------------------------------------


@pytest.mark.integration
def test_reconcile_carry_forward_and_complete(session_setup: dict[str, str]) -> None:
    """Carry-forward both ledger txs (sum 1457.83); complete should balance."""
    # Find tx IDs via the API.
    auth_h = {"Authorization": f"Bearer {session_setup['token']}"}
    txs = httpx.get(
        f"{session_setup['api_url']}/v1/transactions",
        headers=auth_h,
        timeout=10,
    ).json()
    tx_ids = [tx["id"] for tx in txs]
    assert len(tx_ids) >= 2

    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--json",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "create",
            "--account",
            "1110",
            "--batch",
            session_setup["batch_id"],
            "--period",
            "2026-05-01..2026-05-31",
            "--starting",
            "0.00",
            "--ending",
            "1457.83",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    recon_id = json.loads(create.stdout)["id"]

    # Carry-forward both txs via repeated --tx flags.
    cf = _run_cli(
        "reconcile",
        "carry-forward",
        recon_id,
        "--tx",
        tx_ids[0],
        "--tx",
        tx_ids[1],
        api_url=session_setup["api_url"],
    )
    assert cf.returncode == 0, cf.stderr
    assert "Carried forward 2" in cf.stdout

    # Complete should balance (matched=0, carry_forward=1457.83).
    complete = _run_cli("reconcile", "complete", recon_id, api_url=session_setup["api_url"])
    assert complete.returncode == 0, complete.stderr


@pytest.mark.integration
def test_reconcile_delete_requires_cascade(session_setup: dict[str, str]) -> None:
    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--json",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "create",
            "--account",
            "1110",
            "--batch",
            session_setup["batch_id"],
            "--period",
            "2026-05-01..2026-05-31",
            "--starting",
            "0.00",
            "--ending",
            "1457.83",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    recon_id = json.loads(create.stdout)["id"]

    # Without --cascade.
    no_cascade = _run_cli("reconcile", "delete", recon_id, api_url=session_setup["api_url"])
    assert no_cascade.returncode != 0
    assert "cascade" in (no_cascade.stdout + no_cascade.stderr).lower()

    # With --cascade.
    with_cascade = _run_cli(
        "reconcile", "delete", recon_id, "--cascade", api_url=session_setup["api_url"]
    )
    assert with_cascade.returncode == 0, with_cascade.stderr
    assert "Deleted" in with_cascade.stdout


# ---- paper-statement (no-OFX) flow (#275) --------------------------------


@pytest.mark.integration
def test_reconcile_start_paper_happy_path(session_setup: dict[str, str]) -> None:
    """`tulip reconcile start` opens a batch-less reconciliation."""
    result = _run_cli(
        "reconcile",
        "start",
        "--account",
        "1110",
        "--statement-date",
        "2026-05-31",
        "--period-start",
        "2026-05-01",
        "--closing-balance",
        "1457.83",
        "--starting-balance",
        "0.00",
        api_url=session_setup["api_url"],
    )
    assert result.returncode == 0, result.stderr
    assert "Opened paper reconciliation" in result.stdout
    assert "Closing balance asserted: 1457.83" in result.stdout


@pytest.mark.integration
def test_reconcile_start_invalid_date_returns_2(session_setup: dict[str, str]) -> None:
    result = _run_cli(
        "reconcile",
        "start",
        "--account",
        "1110",
        "--statement-date",
        "not-a-date",
        "--closing-balance",
        "1457.83",
        api_url=session_setup["api_url"],
    )
    assert_cli_usage_error(result)


@pytest.mark.integration
def test_reconcile_walk_paper_happy_path(session_setup: dict[str, str]) -> None:
    """Open a paper recon, walk through two txs marking them, then complete."""
    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--json",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "start",
            "--account",
            "1110",
            "--statement-date",
            "2026-05-31",
            "--period-start",
            "2026-05-01",
            "--closing-balance",
            "1457.83",
            "--starting-balance",
            "0.00",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert create.returncode == 0, create.stderr
    recon_id = json.loads(create.stdout)["id"]

    # Walk: pipe "m\nm\n" to match both txs.
    walk = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "walk",
            recon_id,
        ],
        check=False,
        capture_output=True,
        text=True,
        input="m\nm\n",
        timeout=20,
    )
    assert walk.returncode == 0, walk.stderr
    assert "matched" in walk.stdout

    # Complete: closing-balance assertion should pass.
    complete = _run_cli("reconcile", "complete", recon_id, api_url=session_setup["api_url"])
    assert complete.returncode == 0, complete.stderr
    assert "Completed reconciliation" in complete.stdout


@pytest.mark.integration
def test_reconcile_walk_abort_preserves_state(session_setup: dict[str, str]) -> None:
    """Quit mid-walk via 'q'; remaining tx stays unmatched."""
    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--json",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "start",
            "--account",
            "1110",
            "--statement-date",
            "2026-05-31",
            "--period-start",
            "2026-05-01",
            "--closing-balance",
            "1457.83",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert create.returncode == 0, create.stderr
    recon_id = json.loads(create.stdout)["id"]

    # Walk: match first, quit before second.
    walk = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "walk",
            recon_id,
        ],
        check=False,
        capture_output=True,
        text=True,
        input="m\nq\n",
        timeout=20,
    )
    assert walk.returncode == 0, walk.stderr
    assert "1 matched" in walk.stdout

    # /complete should refuse because balance is incomplete.
    complete = _run_cli("reconcile", "complete", recon_id, api_url=session_setup["api_url"])
    assert complete.returncode != 0


@pytest.mark.integration
def test_reconcile_complete_mismatch_refused(session_setup: dict[str, str]) -> None:
    """Wrong --closing-balance fails /complete with reconciliation.unbalanced."""
    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--json",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "start",
            "--account",
            "1110",
            "--statement-date",
            "2026-05-31",
            "--period-start",
            "2026-05-01",
            "--closing-balance",
            "9999.99",  # wrong by a wide margin
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert create.returncode == 0, create.stderr
    recon_id = json.loads(create.stdout)["id"]
    # Match both txs.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--api-url",
            session_setup["api_url"],
            "reconcile",
            "walk",
            recon_id,
        ],
        check=False,
        capture_output=True,
        text=True,
        input="m\nm\n",
        timeout=20,
    )
    complete = _run_cli("reconcile", "complete", recon_id, api_url=session_setup["api_url"])
    assert complete.returncode != 0

"""E2E tests for ``tulip transactions {void, delete, edit}`` (P5.0)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

_PASSWORD = "long-enough-password"


_FAKE_EDITOR_SOURCE = '''\
"""Fake editor: writes canned content to argv[1] (per-invocation queue)."""
import os, pathlib, sys
target = pathlib.Path(sys.argv[1])
counter_path = pathlib.Path(os.environ["TULIP_FAKE_EDITOR_COUNTER"])
try:
    n = int(counter_path.read_text())
except FileNotFoundError:
    n = 0
outputs = os.environ["TULIP_FAKE_EDITOR_OUTPUTS"].split("\\x1e")
chosen = outputs[min(n, len(outputs) - 1)]
target.write_text(chosen)
counter_path.write_text(str(n + 1))
'''


def _fake_editor(tmp_path: Path) -> Path:
    p = tmp_path / "fake_editor.py"
    p.write_text(_FAKE_EDITOR_SOURCE)
    return p


def _run_cli(
    *args: str,
    api_url: str,
    extra_env: dict[str, str] | None = None,
    stdin_text: str | None = None,
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
        input=stdin_text,
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


def _seed_accounts(api_url: str) -> None:
    for name, code, type_ in (
        ("Cash", "assets:cash", "asset"),
        ("Food", "expenses:food", "expense"),
    ):
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
                name,
                "--type",
                type_,
                "--currency",
                "USD",
                "--code",
                code,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr


def _post_via_cli(api_url: str) -> str:
    """Post a USD lunch transaction; return the new transaction's id."""
    today = date.today().isoformat()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--json",
            "--api-url",
            api_url,
            "add",
            "--date",
            today,
            "-m",
            "Lunch",
            "--post",
            "expenses:food=12.50@USD",
            "--post",
            "assets:cash=-12.50@USD",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["id"]


# PENDING-only happy-path tests for `transactions edit` and `transactions
# delete` are deferred to the API integration tests (see
# packages/tulip-api/tests/test_transactions_patch_delete_endpoint.py).
# The CLI cannot create PENDING rows until P5.2 (importers) ships, so the
# CLI E2E here only covers POSTED-source rejection paths plus the void
# happy path. Cross-layer coverage is otherwise complete.


# ---- void ------------------------------------------------------------------


@pytest.mark.integration
def test_void_with_yes_creates_reversal(authed_session: str) -> None:
    _seed_accounts(authed_session)
    tx_id = _post_via_cli(authed_session)
    result = _run_cli(
        "transactions",
        "void",
        tx_id,
        "--reason",
        "duplicate",
        "--yes",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Voided" in result.stdout
    assert tx_id in result.stdout


@pytest.mark.integration
def test_void_aborts_on_no_confirmation(authed_session: str) -> None:
    _seed_accounts(authed_session)
    tx_id = _post_via_cli(authed_session)
    result = _run_cli(
        "transactions",
        "void",
        tx_id,
        "--reason",
        "duplicate",
        api_url=authed_session,
        stdin_text="n\n",
    )
    assert result.returncode == 0, result.stderr
    assert "Aborted" in result.stdout


@pytest.mark.integration
def test_void_json_emits_response_body(authed_session: str) -> None:
    _seed_accounts(authed_session)
    tx_id = _post_via_cli(authed_session)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--json",
            "--api-url",
            authed_session,
            "transactions",
            "void",
            tx_id,
            "--reason",
            "x",
            "--yes",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["source_id"] == tx_id
    assert body["reversal_id"] != tx_id


@pytest.mark.integration
def test_void_already_voided_surfaces_problem(authed_session: str) -> None:
    _seed_accounts(authed_session)
    tx_id = _post_via_cli(authed_session)
    first = _run_cli(
        "transactions",
        "void",
        tx_id,
        "--reason",
        "x",
        "--yes",
        api_url=authed_session,
    )
    assert first.returncode == 0, first.stderr
    second = _run_cli(
        "transactions",
        "void",
        tx_id,
        "--reason",
        "y",
        "--yes",
        api_url=authed_session,
    )
    assert second.returncode != 0
    assert "already voided" in (second.stdout + second.stderr).lower()


# ---- delete ----------------------------------------------------------------


@pytest.mark.integration
def test_delete_posted_returns_problem(authed_session: str) -> None:
    _seed_accounts(authed_session)
    tx_id = _post_via_cli(authed_session)
    result = _run_cli(
        "transactions",
        "delete",
        tx_id,
        "--yes",
        api_url=authed_session,
    )
    assert result.returncode != 0
    assert "not deletable" in (result.stdout + result.stderr).lower()


@pytest.mark.integration
def test_delete_aborts_on_no(authed_session: str) -> None:
    _seed_accounts(authed_session)
    tx_id = _post_via_cli(authed_session)
    result = _run_cli(
        "transactions",
        "delete",
        tx_id,
        api_url=authed_session,
        stdin_text="n\n",
    )
    assert result.returncode == 0, result.stderr
    assert "Aborted" in result.stdout


# ---- edit ------------------------------------------------------------------


@pytest.mark.integration
def test_edit_posted_returns_not_editable(authed_session: str) -> None:
    _seed_accounts(authed_session)
    tx_id = _post_via_cli(authed_session)
    result = _run_cli(
        "transactions",
        "edit",
        tx_id,
        api_url=authed_session,
    )
    assert result.returncode != 0
    assert "not editable" in (result.stdout + result.stderr).lower()


# ---- unauthenticated rejections -------------------------------------------


@pytest.mark.integration
def test_void_unauthenticated_exits_2(live_api: str, tmp_path: Path) -> None:
    bogus = "11111111-1111-1111-1111-111111111111"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--api-url",
            live_api,
            "transactions",
            "void",
            bogus,
            "--reason",
            "x",
            "--yes",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "TULIP_TOKEN_STORE": str(tmp_path / "no-tokens.json"),
        },
    )
    assert result.returncode == 2, result.stderr

"""E2E tests for ``tulip add --edit`` (#43).

The ``EDITOR`` env var is pointed at a tiny fake-editor script so the
test drives the full subprocess flow without needing a real terminal.
The fake editor reads a list of canned replies (one per consecutive
``--edit`` open) from ``TULIP_FAKE_EDITOR_OUTPUTS``, so reopen-on-error
scenarios work too.
"""

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
"""Fake editor for tests: writes canned content to argv[1].

Reads outputs from TULIP_FAKE_EDITOR_OUTPUTS (newline-of-RECORD-SEPARATOR
delimited — we use \\x1e). Each invocation consumes the next entry; the
counter file tracks how many opens have happened so multi-call tests
work.
"""
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


def _seed_accounts(api_url: str) -> None:
    """Create cash + food accounts via the CLI."""
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


@pytest.mark.integration
def test_interactive_add_happy_path(authed_session: str, tmp_path: Path) -> None:
    _seed_accounts(authed_session)
    today = date.today().isoformat()
    fake_buffer = f"{today} Lunch\n  expenses:food   12.50\n  assets:cash    -12.50\n"

    editor = _fake_editor(tmp_path)
    counter = tmp_path / "counter.txt"

    result = _run_cli(
        "add",
        "--edit",
        api_url=authed_session,
        extra_env={
            "EDITOR": f"{sys.executable} {editor}",
            "TULIP_FAKE_EDITOR_OUTPUTS": fake_buffer,
            "TULIP_FAKE_EDITOR_COUNTER": str(counter),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "Lunch" in result.stdout
    assert "12.50" in result.stdout

    bal = subprocess.run(
        [
            sys.executable,
            "-m",
            "tulip_cli",
            "--api-url",
            authed_session,
            "balance",
            "expenses:food",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert "12.50" in bal.stdout


@pytest.mark.integration
def test_interactive_add_empty_buffer_aborts(authed_session: str, tmp_path: Path) -> None:
    """Saving a buffer with only comments and whitespace exits cleanly."""
    editor = _fake_editor(tmp_path)
    counter = tmp_path / "counter.txt"
    empty_buffer = "# nothing here\n\n# still nothing\n"

    result = _run_cli(
        "add",
        "--edit",
        api_url=authed_session,
        extra_env={
            "EDITOR": f"{sys.executable} {editor}",
            "TULIP_FAKE_EDITOR_OUTPUTS": empty_buffer,
            "TULIP_FAKE_EDITOR_COUNTER": str(counter),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "no transaction posted" in result.stdout.lower()


@pytest.mark.integration
def test_interactive_add_unbalanced_then_fixed_loops(authed_session: str, tmp_path: Path) -> None:
    """Unbalanced first save → reopen with banner; second save (balanced) succeeds."""
    _seed_accounts(authed_session)
    today = date.today().isoformat()

    bad_buffer = f"{today} Wrong amounts\n  expenses:food   12.50\n  assets:cash    -9.00\n"
    good_buffer = f"{today} Fixed\n  expenses:food   12.50\n  assets:cash    -12.50\n"

    editor = _fake_editor(tmp_path)
    counter = tmp_path / "counter.txt"
    outputs = bad_buffer + "\x1e" + good_buffer

    result = _run_cli(
        "add",
        "--edit",
        api_url=authed_session,
        extra_env={
            "EDITOR": f"{sys.executable} {editor}",
            "TULIP_FAKE_EDITOR_OUTPUTS": outputs,
            "TULIP_FAKE_EDITOR_COUNTER": str(counter),
        },
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "Fixed" in result.stdout
    # The fake editor was opened twice — once with the bad buffer, then
    # again with the banner-prefixed reopen, where the test wrote the
    # good buffer.
    assert int(counter.read_text()) == 2


@pytest.mark.integration
def test_interactive_add_unknown_account_then_fixed_loops(
    authed_session: str, tmp_path: Path
) -> None:
    _seed_accounts(authed_session)
    today = date.today().isoformat()

    bad_buffer = f"{today} Bad account\n  no-such-account   1.00\n  assets:cash      -1.00\n"
    good_buffer = f"{today} Fixed\n  expenses:food   1.00\n  assets:cash    -1.00\n"

    editor = _fake_editor(tmp_path)
    counter = tmp_path / "counter.txt"
    outputs = bad_buffer + "\x1e" + good_buffer

    result = _run_cli(
        "add",
        "--edit",
        api_url=authed_session,
        extra_env={
            "EDITOR": f"{sys.executable} {editor}",
            "TULIP_FAKE_EDITOR_OUTPUTS": outputs,
            "TULIP_FAKE_EDITOR_COUNTER": str(counter),
        },
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "Fixed" in result.stdout
    assert int(counter.read_text()) == 2


@pytest.mark.integration
def test_interactive_add_parse_error_then_fixed_loops(authed_session: str, tmp_path: Path) -> None:
    """Ledger-parse errors also reopen with a banner."""
    _seed_accounts(authed_session)
    today = date.today().isoformat()

    # Posting amount is unparseable.
    bad_buffer = f"{today} Garbage\n  expenses:food   not-a-number\n  assets:cash    -1.00\n"
    good_buffer = f"{today} Fixed\n  expenses:food   1.00\n  assets:cash    -1.00\n"

    editor = _fake_editor(tmp_path)
    counter = tmp_path / "counter.txt"
    outputs = bad_buffer + "\x1e" + good_buffer

    result = _run_cli(
        "add",
        "--edit",
        api_url=authed_session,
        extra_env={
            "EDITOR": f"{sys.executable} {editor}",
            "TULIP_FAKE_EDITOR_OUTPUTS": outputs,
            "TULIP_FAKE_EDITOR_COUNTER": str(counter),
        },
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "Fixed" in result.stdout
    assert int(counter.read_text()) == 2


@pytest.mark.integration
def test_interactive_add_json_output(authed_session: str, tmp_path: Path) -> None:
    _seed_accounts(authed_session)
    today = date.today().isoformat()
    fake_buffer = f"{today} JSON test\n  expenses:food   3.50\n  assets:cash    -3.50\n"

    editor = _fake_editor(tmp_path)
    counter = tmp_path / "counter.txt"

    result = _run_cli(
        "--json",
        "add",
        "--edit",
        api_url=authed_session,
        extra_env={
            "EDITOR": f"{sys.executable} {editor}",
            "TULIP_FAKE_EDITOR_OUTPUTS": fake_buffer,
            "TULIP_FAKE_EDITOR_COUNTER": str(counter),
        },
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["description"] == "JSON test"
    assert payload["status"] == "posted"

"""CLI tests for ``tulip accounts add --create-parents`` (#416).

Covers the name-path mode that lets the user write the hierarchy
without inventing codes — the user-facing pain point from the issue:
*"everything is insisting on using an account code when that was
always supposed to be optional."*

The backend contract these tests exercise is pinned in
``packages/tulip-api/tests/test_accounts_create_parents.py``;
here we drive the CLI as a subprocess against ``live_api`` so the
end-to-end flag handling, validation messages, and JSON output
round-trip with the server.
"""

from __future__ import annotations

import json
import subprocess
import sys
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
    return live_api


@pytest.mark.integration
def test_create_parents_with_name_path_no_code(authed_session: str) -> None:
    """The user-facing PTA convention: hierarchy via ``--name``, no codes."""
    result = _run_cli(
        "--json",
        "accounts",
        "add",
        "--name",
        "Assets:Current Assets:Checking",
        "--type",
        "asset",
        "--currency",
        "USD",
        "--create-parents",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["name"] == "Checking"
    assert body["code"] is None
    parents = body.get("parents_created") or []
    assert [p["name"] for p in parents] == ["Assets", "Current Assets"]
    assert all(p["code"] is None for p in parents)


@pytest.mark.integration
def test_create_parents_name_path_with_leaf_code(authed_session: str) -> None:
    """``--code`` without a colon is the leaf's short code."""
    result = _run_cli(
        "--json",
        "accounts",
        "add",
        "--name",
        "Assets:Current Assets:Checking",
        "--code",
        "1100",
        "--type",
        "asset",
        "--currency",
        "USD",
        "--create-parents",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["name"] == "Checking"
    assert body["code"] == "1100"
    parents = body.get("parents_created") or []
    assert all(p["code"] is None for p in parents)


@pytest.mark.integration
def test_create_parents_legacy_code_path_still_works(authed_session: str) -> None:
    """The pre-#416 form (``--code assets:current:checking``) is unchanged."""
    result = _run_cli(
        "--json",
        "accounts",
        "add",
        "--name",
        "Checking",
        "--code",
        "assets:current:checking",
        "--type",
        "asset",
        "--currency",
        "USD",
        "--create-parents",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["name"] == "Checking"
    assert body["code"] == "assets:current:checking"
    parents = body.get("parents_created") or []
    # Legacy: intermediate names come from the code segments.
    assert [p["name"] for p in parents] == ["assets", "current"]


@pytest.mark.integration
def test_create_parents_without_any_colon_path_is_rejected(authed_session: str) -> None:
    """Neither --name nor --code carries a hierarchy → user error."""
    result = _run_cli(
        "accounts",
        "add",
        "--name",
        "Leaf",
        "--type",
        "asset",
        "--currency",
        "USD",
        "--create-parents",
        api_url=authed_session,
    )
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    assert "colon" in combined or "path" in combined


@pytest.mark.integration
def test_create_parents_with_both_paths_is_rejected_as_ambiguous(
    authed_session: str,
) -> None:
    """Colons in both --name and --code → user error before or during request."""
    result = _run_cli(
        "accounts",
        "add",
        "--name",
        "Assets:Current Assets:Checking",
        "--code",
        "assets:current:checking",
        "--type",
        "asset",
        "--currency",
        "USD",
        "--create-parents",
        api_url=authed_session,
    )
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    assert "both" in combined or "ambiguous" in combined


@pytest.mark.integration
def test_create_parents_with_parent_flag_is_rejected(authed_session: str) -> None:
    """``--create-parents`` derives the chain; passing ``--parent`` is ambiguous."""
    result = _run_cli(
        "accounts",
        "add",
        "--name",
        "Assets:Current Assets:Checking",
        "--type",
        "asset",
        "--currency",
        "USD",
        "--create-parents",
        "--parent",
        "some-parent-code",
        api_url=authed_session,
    )
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    assert "parent" in combined or "ambiguous" in combined

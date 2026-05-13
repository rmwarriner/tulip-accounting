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
    # Give Rich a wide terminal so Typer's usage / error panels don't
    # wrap mid-word in CI and drop substrings the tests assert on.
    env.setdefault("COLUMNS", "200")
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
def test_imports_apply_happy_path(authed_session: str) -> None:
    """`tulip imports apply BATCH_ID` promotes every line into a PENDING tx."""
    _seed_checking(authed_session)
    fixture = _OFX_FIXTURES / "minimal_ofx2.ofx"
    upload = subprocess.run(
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
    assert upload.returncode == 0, upload.stderr
    batch_id = json.loads(upload.stdout)["id"]

    apply_result = _run_cli(
        "imports",
        "apply",
        batch_id,
        api_url=authed_session,
    )
    assert apply_result.returncode == 0, apply_result.stderr
    assert "created 2" in apply_result.stdout
    assert batch_id in apply_result.stdout


@pytest.mark.integration
def test_imports_show_renders_header_and_lines(authed_session: str) -> None:
    """`tulip imports show BATCH_ID` renders batch header + per-line table."""
    _seed_checking(authed_session)
    fixture = _OFX_FIXTURES / "minimal_ofx2.ofx"
    upload = subprocess.run(
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
    assert upload.returncode == 0, upload.stderr
    batch_id = json.loads(upload.stdout)["id"]

    show = _run_cli("imports", "show", batch_id, api_url=authed_session)
    assert show.returncode == 0, show.stderr
    assert batch_id in show.stdout
    assert "OFX" in show.stdout
    assert "Statement lines" in show.stdout


@pytest.mark.integration
def test_imports_show_json_passthrough(authed_session: str) -> None:
    """`tulip --json imports show BATCH_ID` emits raw ImportBatchRead body."""
    _seed_checking(authed_session)
    fixture = _OFX_FIXTURES / "minimal_ofx2.ofx"
    upload = subprocess.run(
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
    batch_id = json.loads(upload.stdout)["id"]

    show = _run_cli("--json", "imports", "show", batch_id, api_url=authed_session)
    assert show.returncode == 0, show.stderr
    payload = json.loads(show.stdout)
    assert payload["id"] == batch_id
    assert isinstance(payload.get("lines"), list)


@pytest.mark.integration
def test_imports_show_unknown_batch_returns_problem(authed_session: str) -> None:
    """Unknown batch UUID surfaces the typed import_batch.not_found problem."""
    from uuid import uuid4

    show = _run_cli("imports", "show", str(uuid4()), api_url=authed_session)
    assert show.returncode != 0
    combined = (show.stdout + show.stderr).lower()
    assert "import_batch.not_found" in combined or "not found" in combined


@pytest.mark.integration
def test_imports_apply_already_applied_returns_409(authed_session: str) -> None:
    """Re-applying renders the typed Problem and exits non-zero."""
    _seed_checking(authed_session)
    fixture = _OFX_FIXTURES / "minimal_ofx2.ofx"
    upload = subprocess.run(
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
    batch_id = json.loads(upload.stdout)["id"]
    _run_cli("imports", "apply", batch_id, api_url=authed_session)
    second = _run_cli("imports", "apply", batch_id, api_url=authed_session)
    assert second.returncode != 0
    assert "Import already applied" in (second.stdout + second.stderr)


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
def test_csv_profile_create_and_import_e2e(authed_session: str) -> None:
    _seed_checking(authed_session)
    # Create a profile via CLI.
    create = _run_cli(
        "imports",
        "profiles",
        "add",
        "--name",
        "chase",
        "--date-column",
        "Posting Date",
        "--date-format",
        "%m/%d/%Y",
        "--amount-column",
        "Amount",
        "--description-column",
        "Description",
        "--reference-column",
        "Check or Slip #",
        api_url=authed_session,
    )
    assert create.returncode == 0, create.stderr
    assert "chase" in create.stdout

    # List shows it.
    listed = _run_cli("imports", "profiles", "list", api_url=authed_session)
    assert listed.returncode == 0
    assert "chase" in listed.stdout

    # Import a CSV referencing the profile.
    fixture = _OFX_FIXTURES.parent / "csv" / "chase_checking.csv"
    result = _run_cli(
        "import",
        "csv",
        str(fixture),
        "--account",
        "1110",
        "--profile",
        "chase",
        api_url=authed_session,
    )
    assert result.returncode == 0, result.stderr
    assert "Imported 4 statement lines" in result.stdout


@pytest.mark.integration
def test_csv_profile_export_round_trip(authed_session: str, tmp_path: Path) -> None:
    _seed_checking(authed_session)
    _run_cli(
        "imports",
        "profiles",
        "add",
        "--name",
        "amex",
        "--date-column",
        "Date",
        "--date-format",
        "%Y-%m-%d",
        "--amount-column",
        "Amount",
        "--description-column",
        "Description",
        "--amount-negative-means",
        "credit",
        api_url=authed_session,
    )

    # Export to a file.
    out = tmp_path / "amex.yaml"
    exported = _run_cli(
        "imports",
        "profiles",
        "export",
        "amex",
        "--out",
        str(out),
        api_url=authed_session,
    )
    assert exported.returncode == 0
    body = out.read_text()
    assert "name: amex" in body

    # Delete + re-import.
    _run_cli(
        "imports",
        "profiles",
        "delete",
        "amex",
        "--yes",
        api_url=authed_session,
    )
    imported = _run_cli(
        "imports",
        "profiles",
        "import",
        str(out),
        api_url=authed_session,
    )
    assert imported.returncode == 0, imported.stderr
    assert "amex" in imported.stdout


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


@pytest.mark.integration
def test_imports_list_empty_household(authed_session: str) -> None:
    """`tulip imports list` says so when the household has no batches yet."""
    result = _run_cli("imports", "list", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "No import batches match" in result.stdout


@pytest.mark.integration
def test_imports_list_renders_table(authed_session: str) -> None:
    """`tulip imports list` prints a table with ID prefixes after an upload."""
    _seed_checking(authed_session)
    fixture = _OFX_FIXTURES / "minimal_ofx2.ofx"
    upload = subprocess.run(
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
    assert upload.returncode == 0, upload.stderr
    batch_id = json.loads(upload.stdout)["id"]

    result = _run_cli("imports", "list", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    # ID prefix (first 8 chars) of the new batch should appear in the table.
    assert batch_id[:8] in result.stdout
    assert "OFX" in result.stdout
    # Full UUID is intentionally truncated in the table.
    assert batch_id not in result.stdout


@pytest.mark.integration
def test_imports_list_json_passthrough(authed_session: str) -> None:
    """`tulip --json imports list` emits the raw ImportBatchListResponse body."""
    _seed_checking(authed_session)
    fixture = _OFX_FIXTURES / "minimal_ofx2.ofx"
    subprocess.run(
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

    result = _run_cli("--json", "imports", "list", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload.get("items"), list)
    assert len(payload["items"]) == 1
    # Full UUIDs are preserved in JSON output.
    assert len(payload["items"][0]["id"]) == 36


@pytest.mark.integration
def test_imports_list_status_filter(authed_session: str) -> None:
    """`tulip imports list --status applied` filters via the API query param."""
    _seed_checking(authed_session)
    fixture = _OFX_FIXTURES / "minimal_ofx2.ofx"
    subprocess.run(
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

    # The fresh upload is parsed, not applied — applied filter sees nothing.
    result = _run_cli("imports", "list", "--status", "applied", api_url=authed_session)
    assert result.returncode == 0, result.stderr
    assert "No import batches match" in result.stdout


@pytest.mark.integration
def test_imports_list_invalid_status_rejected(authed_session: str) -> None:
    """`tulip imports list --status bogus` exits with a usage error."""
    result = _run_cli("imports", "list", "--status", "bogus", api_url=authed_session)
    assert result.returncode != 0
    assert "--status" in (result.stdout + result.stderr)

"""Shared fixtures for ``tulip-cli`` tests.

The marquee fixture is :func:`live_api` — it migrates a fresh SQLite
database, spawns ``uvicorn`` as a subprocess against the API package
factory, and yields the base URL. Tests then run the real ``tulip``
console script as a subprocess pointed at that URL, which is the only
honest way to exercise the CLI end to end (HTTP framing, exit codes,
stdout/stderr separation, signal handling — none of those are visible
through an in-process ``TestClient``).

Per-test scope, deliberately. Fresh DB per test means tests don't have
to coordinate emails or household names. Spawn is ~1s; we'll revisit
session scoping when the test count makes that cost meaningful.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from alembic.command import upgrade
from alembic.config import Config

# Same base64 of 32 bytes the API conftest uses; never appears outside tests.
_TEST_MASTER_KEY_B64 = "q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s="
_TEST_JWT_SECRET = "test-secret-32bytes-test-secret!!"

_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "tulip-storage" / "alembic.ini"


def _free_port() -> int:
    """Return an ephemeral port that's free at the moment of the call.

    There is an unavoidable TOCTOU window between ``close()`` and
    ``Popen``, but on a dev machine the chance of collision is negligible
    and the alternative (parsing uvicorn's startup output for the chosen
    port) is much more fragile.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _make_alembic_cfg(db_url: str) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option(
        "script_location",
        str(_ALEMBIC_INI.parent / "src" / "tulip_storage" / "migrations"),
    )
    return cfg


def _wait_for_health(base_url: str, *, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/health", timeout=1.0)
            if r.status_code == 200:
                return
        except httpx.HTTPError as exc:
            last_exc = exc
        time.sleep(0.1)
    raise RuntimeError(
        f"uvicorn never became healthy at {base_url} "
        f"(last error: {type(last_exc).__name__ if last_exc else 'n/a'})"
    )


@pytest.fixture
def live_api(tmp_path: Path) -> Iterator[str]:
    """Spawn uvicorn against a freshly migrated SQLite DB; yield the base URL."""
    db_path = tmp_path / "tulip.db"
    db_url = f"sqlite:///{db_path}"
    upgrade(_make_alembic_cfg(db_url), "head")

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = {
        "PATH": __import__("os").environ.get("PATH", ""),
        "TULIP_DATABASE_URL": db_url,
        "TULIP_JWT_SECRET": _TEST_JWT_SECRET,
        "TULIP_MASTER_KEY": _TEST_MASTER_KEY_B64,
    }

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tulip_api.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_health(base_url)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

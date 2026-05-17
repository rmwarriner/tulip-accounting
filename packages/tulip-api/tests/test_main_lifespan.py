"""Regression tests for the FastAPI lifespan / runner handler registration.

Privacy audit M-17 (#340): the ``daily_insights`` handler is exported
from ``tulip_storage.runner.handlers`` but deliberately not registered
with the runner in ``create_app``'s lifespan. The deferral is
documented in ADR-0005 §"Daily-insights handler registration"; this
test pins the invariant so a future "we should just wire it" patch
doesn't silently fire background AI egress.

When the wiring lands, delete this test in the same PR.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tulip_api.main import create_app


@pytest.mark.integration
def test_daily_insights_handler_is_not_registered_by_default(monkeypatch, tmp_path):
    """``daily_insights`` is gated behind explicit operator wiring (M-17 / #340).

    The lifespan should NOT register a handler named ``daily_insights``;
    the three handlers expected today are ``attachment_gc``,
    ``ai_retention``, ``audit_retention``.
    """
    # Point the app at an isolated SQLite file + attachment root so the
    # default-runner lifespan boots cleanly.
    db_path = tmp_path / "tulip.db"
    attach_root = tmp_path / "attachments"
    attach_root.mkdir()
    monkeypatch.setenv("TULIP_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("TULIP_ATTACHMENT_ROOT", str(attach_root))
    monkeypatch.setenv(
        "TULIP_MASTER_KEY",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",  # base64 of 32 zero bytes
    )
    monkeypatch.setenv("TULIP_JWT_SECRET", "test-secret-for-lifespan-only")
    # Stamp the DB head so the runner can boot cleanly.
    from pathlib import Path

    from alembic.command import upgrade
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[2] / "tulip-storage" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option(
        "script_location",
        str(
            Path(__file__).resolve().parents[2]
            / "tulip-storage"
            / "src"
            / "tulip_storage"
            / "migrations"
        ),
    )
    upgrade(cfg, "head")

    app = create_app(enable_runner=True)
    with TestClient(app) as _:
        runner = app.state.runner
        assert runner is not None, "lifespan should set app.state.runner"
        registered = set(runner._handlers.keys())
        assert "daily_insights" not in registered, (
            "daily_insights handler must NOT be registered by default — see "
            "ADR-0005 §'Daily-insights handler registration' / privacy audit M-17"
        )
        # Positive control — the three handlers we do register.
        assert {
            "attachment_gc",
            "ai_retention",
            "audit_retention",
            "session_retention",
        } <= registered

"""Tests for ``GET /v1/system/diagnostics`` (#135).

Endpoint is unauthenticated by design — the doctor CLI runs it before
the user has any tokens. The settings fixture is overridden per-test
because the diagnostics shape depends on environment-derived state
(master-key source, attachment-root writability) that the default
fixture intentionally leaves at "ephemeral".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tulip_api.config import Settings, get_settings
from tulip_storage.migrations_meta import expected_alembic_head


def _override_settings(app: FastAPI, settings: Settings) -> None:
    app.dependency_overrides[get_settings] = lambda: settings


class TestHappyPath:
    def test_healthy_install_returns_all_green(
        self, app: FastAPI, client: TestClient, tmp_path: Path
    ) -> None:
        _override_settings(
            app,
            Settings(
                database_url="sqlite:///:memory:",
                jwt_secret="test-secret-32bytes-test-secret!!",
                master_key=b"\xab" * 32,
                master_key_source="env",
                attachment_root=tmp_path / "attachments",
            ),
        )
        r = client.get("/v1/system/diagnostics")
        assert r.status_code == 200
        body = r.json()

        assert body["alembic_head_in_db"] == expected_alembic_head()
        assert body["alembic_head_expected"] == expected_alembic_head()
        assert body["alembic_head_match"] is True
        assert body["master_key_source"] == "env"
        assert body["master_key_loaded"] is True
        assert body["attachment_root_writable"] is True

    def test_file_master_key_source_reported(
        self, app: FastAPI, client: TestClient, tmp_path: Path
    ) -> None:
        _override_settings(
            app,
            Settings(
                database_url="sqlite:///:memory:",
                jwt_secret="test-secret-32bytes-test-secret!!",
                master_key=b"\xcd" * 32,
                master_key_source="file",
                attachment_root=tmp_path / "attachments",
            ),
        )
        body = client.get("/v1/system/diagnostics").json()
        assert body["master_key_source"] == "file"
        assert body["master_key_loaded"] is True

    def test_no_auth_required(self, client: TestClient) -> None:
        """Unauthenticated by design — the doctor runs before login."""
        r = client.get("/v1/system/diagnostics")
        assert r.status_code == 200


class TestDegradedStates:
    def test_ephemeral_master_key_reports_not_loaded(
        self, app: FastAPI, client: TestClient, tmp_path: Path
    ) -> None:
        _override_settings(
            app,
            Settings(
                database_url="sqlite:///:memory:",
                jwt_secret="test-secret-32bytes-test-secret!!",
                master_key=b"\x99" * 32,
                master_key_source="ephemeral",
                attachment_root=tmp_path / "attachments",
            ),
        )
        body = client.get("/v1/system/diagnostics").json()
        assert body["master_key_source"] == "ephemeral"
        assert body["master_key_loaded"] is False

    def test_unwritable_attachment_root_reports_false(
        self, app: FastAPI, client: TestClient, tmp_path: Path
    ) -> None:
        # Create a file at the path so mkdir(parents=True, exist_ok=True)
        # raises NotADirectoryError → probe returns False.
        bogus = tmp_path / "definitely-not-a-dir"
        bogus.write_text("blocking the path")
        _override_settings(
            app,
            Settings(
                database_url="sqlite:///:memory:",
                jwt_secret="test-secret-32bytes-test-secret!!",
                master_key=b"\xee" * 32,
                master_key_source="env",
                attachment_root=bogus / "subdir",
            ),
        )
        body = client.get("/v1/system/diagnostics").json()
        assert body["attachment_root_writable"] is False

    def test_alembic_head_mismatch_reports_false(
        self,
        app: FastAPI,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from tulip_api.routers import system as system_router

        monkeypatch.setattr(system_router, "expected_alembic_head", lambda: "deadbeef0000")
        _override_settings(
            app,
            Settings(
                database_url="sqlite:///:memory:",
                jwt_secret="test-secret-32bytes-test-secret!!",
                master_key=b"\x77" * 32,
                master_key_source="env",
                attachment_root=tmp_path / "attachments",
            ),
        )
        body = client.get("/v1/system/diagnostics").json()
        assert body["alembic_head_match"] is False
        assert body["alembic_head_expected"] == "deadbeef0000"
        assert body["alembic_head_in_db"] != "deadbeef0000"

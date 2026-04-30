"""Tests for /.well-known/errors/ index and per-code pages."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tulip_api.errors import TulipProblem
from tulip_api.routers.well_known_errors import _registry


class TestRegistry:
    def test_includes_known_codes(self) -> None:
        codes = set(_registry().keys())
        # A representative sample — any of these missing means the
        # auto-discovery walk lost a subclass.
        assert {
            "auth.unauthorized",
            "auth.forbidden",
            "auth.invalid_credentials",
            "auth.mfa_required",
            "auth.mfa_invalid_code",
            "auth.mfa_invalid_recovery_code",
            "account.not_found",
            "account.unknown",
            "transaction.unbalanced",
            "transaction.not_found",
            "period.closed",
        } <= codes

    def test_every_subclass_resolves_or_has_placeholder(self) -> None:
        # If a TulipProblem subclass isn't in the registry, that means
        # _instantiate returned None — i.e. it has required args without
        # a placeholder entry. That's acceptable for now (we surface the
        # gap visibly here) but should be a tiny set; alert if it grows.
        from tulip_api.routers.well_known_errors import _all_subclasses

        codes_in_registry = set(_registry().keys())
        all_subs = _all_subclasses(TulipProblem)
        # Construct everything we can and make sure missing ones are
        # explicitly missing (don't silently lose codes).
        missing_codes = set()
        for sub in all_subs:
            inst = _try_instantiate(sub)
            if inst is None:
                missing_codes.add(sub.__name__)
        # If you've added a new subclass with required args, register a
        # placeholder in well_known_errors._PLACEHOLDER_ARGS.
        assert not missing_codes, (
            f"Subclasses needing placeholder args in _PLACEHOLDER_ARGS: {missing_codes}"
        )
        assert codes_in_registry  # sanity


def _try_instantiate(sub: type[TulipProblem]) -> TulipProblem | None:
    from tulip_api.routers.well_known_errors import _instantiate

    return _instantiate(sub)


class TestPages:
    def test_index_lists_all_codes(self, client: TestClient) -> None:
        r = client.get("/.well-known/errors/")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        body = r.text
        for code in _registry():
            assert code in body, f"index page missing {code}"

    def test_per_code_page_renders(self, client: TestClient) -> None:
        r = client.get("/.well-known/errors/auth.unauthorized")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        body = r.text
        assert "auth.unauthorized" in body
        assert "Authentication required" in body  # title

    def test_per_code_page_shows_extensions(self, client: TestClient) -> None:
        # MfaRequiredError carries `mfa_token` + `mfa_token_expires_in`.
        r = client.get("/.well-known/errors/auth.mfa_required")
        assert r.status_code == 200
        body = r.text
        assert "Extension fields" in body
        assert "mfa_token" in body
        assert "mfa_token_expires_in" in body

    def test_unknown_code_returns_404_html(self, client: TestClient) -> None:
        r = client.get("/.well-known/errors/totally.fake.code")
        assert r.status_code == 404
        # Must be HTML — these pages target browsers, not API clients.
        assert r.headers["content-type"].startswith("text/html")
        assert "Unknown error code" in r.text

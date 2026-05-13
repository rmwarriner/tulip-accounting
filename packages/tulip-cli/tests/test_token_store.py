"""Unit tests for ``tulip_cli.auth.tokens``.

Two backends ship: ``keyring`` (default) and a JSON-file backend used in
tests and CI via the ``TULIP_TOKEN_STORE`` env var. These tests exercise
the file backend directly and the env-var-driven dispatcher.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tulip_cli.auth.tokens import TokenSet, TokenStore, default_token_store


def _tokens(**overrides: object) -> TokenSet:
    base = {
        "email": "alice@example.com",
        "access_token": "access.jwt.payload",
        "refresh_token": "refresh-opaque-token",
        "access_expires_at": 1_800_000_000,
    }
    base.update(overrides)
    return TokenSet(**base)  # type: ignore[arg-type]


def test_token_round_trip_through_file_backend(tmp_path: Path) -> None:
    store = TokenStore(file_path=tmp_path / "tokens.json")
    store.save("https://api.example.com", _tokens())

    loaded = store.load("https://api.example.com")
    assert loaded is not None
    assert loaded.email == "alice@example.com"
    assert loaded.access_token == "access.jwt.payload"
    assert loaded.refresh_token == "refresh-opaque-token"
    assert loaded.access_expires_at == 1_800_000_000


def test_load_returns_none_when_no_tokens_stored(tmp_path: Path) -> None:
    store = TokenStore(file_path=tmp_path / "tokens.json")
    assert store.load("https://api.example.com") is None


def test_clear_removes_only_the_target_url(tmp_path: Path) -> None:
    store = TokenStore(file_path=tmp_path / "tokens.json")
    store.save("https://api.one.example.com", _tokens(email="alice@one"))
    store.save("https://api.two.example.com", _tokens(email="alice@two"))

    store.clear("https://api.one.example.com")

    assert store.load("https://api.one.example.com") is None
    remaining = store.load("https://api.two.example.com")
    assert remaining is not None
    assert remaining.email == "alice@two"


def test_clear_is_idempotent(tmp_path: Path) -> None:
    store = TokenStore(file_path=tmp_path / "tokens.json")
    store.clear("https://api.example.com")  # never been saved → no error
    store.save("https://api.example.com", _tokens())
    store.clear("https://api.example.com")
    store.clear("https://api.example.com")  # second clear → no error
    assert store.load("https://api.example.com") is None


def test_save_overwrites_existing_tokens(tmp_path: Path) -> None:
    store = TokenStore(file_path=tmp_path / "tokens.json")
    store.save("https://api.example.com", _tokens(access_token="first"))
    store.save("https://api.example.com", _tokens(access_token="second"))
    loaded = store.load("https://api.example.com")
    assert loaded is not None
    assert loaded.access_token == "second"


def test_url_normalization_strips_trailing_slash(tmp_path: Path) -> None:
    """The store treats trailing-slash variants as the same URL."""
    store = TokenStore(file_path=tmp_path / "tokens.json")
    store.save("https://api.example.com/", _tokens())
    assert store.load("https://api.example.com") is not None


def test_default_token_store_uses_file_backend_when_env_var_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``TULIP_TOKEN_STORE`` env var routes through the file backend."""
    monkeypatch.setenv("TULIP_TOKEN_STORE", str(tmp_path / "tokens.json"))
    store = default_token_store()
    store.save("https://api.example.com", _tokens())
    assert store.load("https://api.example.com") is not None


def test_default_token_store_uses_keyring_when_env_var_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``TULIP_TOKEN_STORE`` → keyring-backed (real backend; just verify shape)."""
    monkeypatch.delenv("TULIP_TOKEN_STORE", raising=False)
    store = default_token_store()
    # Don't actually write to the user's keyring during tests; just check
    # that the store reports keyring mode.
    assert store.is_keyring_backed


class TestKeyringUnavailable:
    """When the OS keyring backend is missing, raise TokenStoreError (#227)."""

    def test_save_raises_token_store_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import keyring
        import keyring.errors

        from tulip_cli.auth.tokens import TokenStoreError

        def _no_keyring(*_a: object, **_kw: object) -> None:
            raise keyring.errors.NoKeyringError("no usable backend")

        monkeypatch.setattr(keyring, "set_password", _no_keyring)
        store = TokenStore()  # keyring-backed
        with pytest.raises(TokenStoreError) as excinfo:
            store.save("https://api.example.com", _tokens())
        # Operator guidance must mention how to recover.
        assert "keyring" in str(excinfo.value).lower()

    def test_load_raises_token_store_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import keyring
        import keyring.errors

        from tulip_cli.auth.tokens import TokenStoreError

        def _no_keyring(*_a: object, **_kw: object) -> None:
            raise keyring.errors.NoKeyringError("no usable backend")

        monkeypatch.setattr(keyring, "get_password", _no_keyring)
        store = TokenStore()
        with pytest.raises(TokenStoreError):
            store.load("https://api.example.com")

    def test_clear_raises_token_store_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import keyring
        import keyring.errors

        from tulip_cli.auth.tokens import TokenStoreError

        def _no_keyring(*_a: object, **_kw: object) -> None:
            raise keyring.errors.NoKeyringError("no usable backend")

        monkeypatch.setattr(keyring, "delete_password", _no_keyring)
        store = TokenStore()
        with pytest.raises(TokenStoreError):
            store.clear("https://api.example.com")

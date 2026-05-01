"""HTTP client wrapper for the Tulip CLI.

A thin layer over ``httpx.Client`` that:

* Builds requests against the resolved API base URL.
* For authenticated requests, loads tokens from a :class:`TokenStore`,
  pre-emptively refreshes the access token if it's near expiry, and
  injects ``Authorization: Bearer ...``.
* Translates non-2xx responses and network failures into
  :class:`tulip_cli.errors.CliError` so callers don't have to care
  about the difference.

Pre-emptive (vs reactive) refresh: the API doesn't expose a distinct
``auth.token_expired`` code, so a reactive retry on 401 would also kick
in for legitimately-bad credentials. The JWT carries an ``exp`` claim;
checking it locally avoids the ambiguity.
"""

from __future__ import annotations

import time
from typing import Final

import httpx

from tulip_cli.auth.tokens import TokenSet, TokenStore
from tulip_cli.config import Config
from tulip_cli.errors import EXIT_AUTH, CliError, parse_problem_response

#: Refresh the access token when it's within this many seconds of expiry.
#: Pulled from a small leeway window rather than the literal expiry so a
#: long-running command doesn't get caught by a token that goes stale
#: mid-request.
REFRESH_LEEWAY_SECONDS: Final[int] = 30


def _not_logged_in_problem(api_url: str) -> dict[str, object]:
    return {
        "type": "/.well-known/errors/auth.not_logged_in",
        "title": "Not logged in",
        "status": 0,
        "detail": (f"No tokens stored for {api_url}. Run `tulip auth login` first."),
        "instance": "",
        "code": "auth.not_logged_in",
    }


def _session_expired_problem() -> dict[str, object]:
    return {
        "type": "/.well-known/errors/auth.session_expired",
        "title": "Session expired",
        "status": 0,
        "detail": (
            "Your refresh token was rejected by the API. "
            "Run `tulip auth login` to start a new session."
        ),
        "instance": "",
        "code": "auth.session_expired",
    }


class TulipClient:
    """Thin convenience wrapper over an ``httpx.Client`` with auth + refresh."""

    def __init__(
        self,
        config: Config,
        *,
        token_store: TokenStore | None = None,
        as_json: bool = False,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Build a client bound to ``config.api_url``.

        ``token_store`` is required only for authenticated requests;
        unauth'd commands (``register``, ``login`` itself) can omit it.
        ``transport`` is a test seam — the production code path passes
        ``None`` and lets ``httpx.Client`` build its default transport.
        """
        self._config = config
        self._token_store = token_store
        self._as_json = as_json
        self._client = httpx.Client(
            base_url=config.api_url,
            headers={"accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> TulipClient:
        """Enter the context manager; the underlying ``httpx.Client`` opens lazily."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Close the underlying ``httpx.Client`` on context-manager exit."""
        self.close()

    def close(self) -> None:
        """Close the underlying ``httpx.Client``."""
        self._client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = False,
        json: object = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Issue a request, raising :class:`CliError` on failure.

        With ``authenticated=True`` the client loads tokens from the
        configured store, refreshes if near-expiry, and injects a Bearer
        header.
        """
        merged: dict[str, str] = dict(headers) if headers else {}
        if authenticated:
            merged["authorization"] = f"Bearer {self._access_token()}"
        try:
            response = self._client.request(method, path, json=json, params=params, headers=merged)
        except httpx.HTTPError as exc:
            raise CliError.from_network_error(exc, as_json=self._as_json) from exc
        if response.status_code >= 400:
            raise CliError.from_response(response, as_json=self._as_json)
        return response

    def get(
        self,
        path: str,
        *,
        authenticated: bool = False,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """``GET path`` against the configured API."""
        return self.request(
            "GET", path, authenticated=authenticated, params=params, headers=headers
        )

    def post(
        self,
        path: str,
        *,
        authenticated: bool = False,
        json: object = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """``POST path`` against the configured API."""
        return self.request("POST", path, authenticated=authenticated, json=json, headers=headers)

    def _access_token(self) -> str:
        store = self._token_store
        if store is None:
            raise CliError(
                problem=_not_logged_in_problem(self._config.api_url),
                as_json=self._as_json,
                exit_code=EXIT_AUTH,
            )
        tokens = store.load(self._config.api_url)
        if tokens is None:
            raise CliError(
                problem=_not_logged_in_problem(self._config.api_url),
                as_json=self._as_json,
                exit_code=EXIT_AUTH,
            )
        if tokens.access_expires_at - int(time.time()) < REFRESH_LEEWAY_SECONDS:
            tokens = self._refresh(store, tokens)
        return tokens.access_token

    def _refresh(self, store: TokenStore, tokens: TokenSet) -> TokenSet:
        try:
            response = self._client.post(
                "/v1/auth/refresh",
                json={"refresh_token": tokens.refresh_token},
            )
        except httpx.HTTPError as exc:
            raise CliError.from_network_error(exc, as_json=self._as_json) from exc
        if response.status_code >= 400:
            store.clear(self._config.api_url)
            problem = _session_expired_problem()
            problem["upstream"] = parse_problem_response(response)
            raise CliError(problem=problem, as_json=self._as_json, exit_code=EXIT_AUTH)
        body = response.json()
        new_tokens = TokenSet(
            email=tokens.email,
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            access_expires_at=int(time.time()) + int(body["expires_in"]),
        )
        store.save(self._config.api_url, new_tokens)
        return new_tokens

"""HTTP client wrapper for the Tulip CLI.

A thin layer over ``httpx.Client`` that:

* Builds requests against the resolved API base URL.
* Injects a bearer token when one is available (token storage lands in
  the auth slice — for now ``token`` is always ``None``).
* Translates non-2xx responses and network failures into
  :class:`tulip_cli.errors.CliError` so callers don't have to care about
  the difference.
"""

from __future__ import annotations

import httpx

from tulip_cli.config import Config
from tulip_cli.errors import CliError


class TulipClient:
    """Thin convenience wrapper over an ``httpx.Client``."""

    def __init__(
        self,
        config: Config,
        *,
        token: str | None = None,
        as_json: bool = False,
        timeout: float = 10.0,
    ) -> None:
        """Build a client bound to ``config.api_url`` with optional bearer token."""
        headers: dict[str, str] = {"accept": "application/json"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        self._client = httpx.Client(base_url=config.api_url, headers=headers, timeout=timeout)
        self._as_json = as_json

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
        json: object = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Issue a request, raising :class:`CliError` on failure."""
        try:
            response = self._client.request(method, path, json=json, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise CliError.from_network_error(exc, as_json=self._as_json) from exc
        if response.status_code >= 400:
            raise CliError.from_response(response, as_json=self._as_json)
        return response

    def get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """``GET path`` against the configured API."""
        return self.request("GET", path, params=params, headers=headers)

    def post(
        self,
        path: str,
        *,
        json: object = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """``POST path`` against the configured API."""
        return self.request("POST", path, json=json, headers=headers)

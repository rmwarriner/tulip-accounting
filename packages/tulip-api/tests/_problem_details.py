"""Test helpers for asserting RFC 9457 Problem Details responses.

Per ARCHITECTURE §7.8.8, every error-path test should assert on the
problem+json body shape — not just the HTTP status. ``assert_problem``
is the canonical way to do that.
"""

from __future__ import annotations

from typing import Any

from httpx import Response


def assert_problem(
    response: Response,
    *,
    code: str,
    status: int,
    title: str | None = None,
) -> dict[str, Any]:
    """Assert ``response`` is a well-formed RFC 9457 Problem Details body.

    Returns the parsed JSON so callers can drill into extension fields.
    """
    assert response.status_code == status, (
        f"expected status {status}, got {response.status_code}: {response.text}"
    )
    assert response.headers["content-type"].startswith("application/problem+json"), (
        f"expected application/problem+json, got {response.headers.get('content-type')}"
    )
    body = response.json()
    assert isinstance(body, dict), f"problem body must be an object, got {type(body)}"
    for required in ("type", "title", "status", "detail", "instance", "code"):
        assert required in body, f"problem body missing required field {required!r}: {body}"
    assert body["status"] == status, f"body.status={body['status']!r} disagrees with HTTP status"
    assert body["code"] == code, f"expected code {code!r}, got {body['code']!r}"
    if title is not None:
        assert body["title"] == title
    return body

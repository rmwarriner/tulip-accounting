"""Request-id middleware: stamp every request with a UUID and propagate it.

The id is read from the `X-Request-Id` header if the client supplied one
(useful for correlating client + server logs across an outage), otherwise
it's generated. The id is bound into structlog's contextvars so every log
line emitted during request handling carries it, and it's echoed back on
the response.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-Id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Bind a request_id to structlog contextvars and echo it on the response."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Bind request_id contextvar for the duration of the request."""
        rid = self._extract_or_generate(request)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=rid)
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = rid
            return response
        finally:
            structlog.contextvars.clear_contextvars()

    @staticmethod
    def _extract_or_generate(request: Request) -> str:
        supplied = request.headers.get(REQUEST_ID_HEADER)
        if supplied:
            try:
                return str(UUID(supplied))
            except ValueError:
                # Malformed — generate a fresh one rather than echoing
                # whatever the caller sent; logs deserve well-formed ids.
                pass
        return str(uuid4())

"""Streaming upload-size enforcement (security audit M-17, #336).

The naive pattern — ``data = await file.read()`` then
``if len(data) > MAX: raise`` — slurps the whole body into RAM before
the size check, defeating the cap as a DoS guard. Starlette's
``UploadFile.read()`` and ``Request.body()`` both have this shape.

The helpers here stream the body and bail the moment the running total
exceeds the cap, so a malicious request can't allocate more than
``max_bytes + chunk_size`` bytes of RAM no matter how big the upload.
A ``Content-Length`` header check happens first when the header is
present and trustworthy (the proxy in front of the API is trusted).

Both helpers raise :class:`RequestPayloadTooLargeError` (RFC 9457 413)
on overflow — same shape the post-slurp callers used to emit, so the
client-facing contract doesn't change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tulip_api.errors import RequestPayloadTooLargeError

if TYPE_CHECKING:
    from fastapi import Request, UploadFile


#: Default per-chunk read size. Small enough that worst-case RAM use is
#: bounded; large enough that the syscall overhead per upload stays
#: negligible for reasonable file sizes (~1k chunks for a 25 MB upload).
_CHUNK_SIZE = 64 * 1024


def _check_content_length(declared: str | None, max_bytes: int) -> None:
    """Reject eagerly when the ``Content-Length`` header alone exceeds the cap."""
    if declared is None:
        return
    try:
        n = int(declared)
    except ValueError:
        return  # malformed; let the stream loop decide
    if n > max_bytes:
        raise RequestPayloadTooLargeError(max_bytes=max_bytes)


async def read_request_body_capped(request: Request, *, max_bytes: int) -> bytes:
    """Read the request body in chunks, raising as soon as the cap is exceeded.

    Used for endpoints that accept a raw body (``Request.body()`` pattern),
    e.g. ``POST /v1/pta/import``.
    """
    _check_content_length(request.headers.get("content-length"), max_bytes)

    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise RequestPayloadTooLargeError(max_bytes=max_bytes)
    return bytes(buf)


async def read_upload_file_capped(file: UploadFile, *, max_bytes: int) -> bytes:
    """Read an ``UploadFile`` in chunks, raising as soon as the cap is exceeded.

    Used for ``multipart/form-data`` endpoints (``POST /v1/imports`` and
    its re-import sibling) — Starlette already gave us an ``UploadFile``;
    we just need to stream-and-bail rather than ``read()``ing the whole
    thing into memory.
    """
    buf = bytearray()
    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise RequestPayloadTooLargeError(max_bytes=max_bytes)
    return bytes(buf)

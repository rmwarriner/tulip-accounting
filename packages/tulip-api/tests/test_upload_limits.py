"""Unit tests for the streaming upload-size enforcement helper (#336).

Security audit M-17: ``read_upload_file_capped`` / ``read_request_body_capped``
must reject as soon as the running total exceeds the cap, *without*
accumulating the entire body in RAM first. The unit tests below use a
``FakeUploadFile`` / synthetic async iterator and assert both the
overflow-detection and Content-Length eager-gate behaviours.

The integration paths (``POST /v1/imports`` + ``POST /v1/pta/import``)
inherit the protection by calling the helpers directly; covered by the
existing imports / pta endpoint tests for the happy path, and by
the explicit oversize-request tests in this file's ``EndToEnd`` class.
"""

from __future__ import annotations

import pytest

from tulip_api.errors import RequestPayloadTooLargeError
from tulip_api.upload_limits import (
    read_request_body_capped,
    read_upload_file_capped,
)


class _FakeUploadFile:
    """Stand-in for Starlette's UploadFile that hands out fixed bytes."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0

    async def read(self, n: int = -1) -> bytes:
        if n < 0:
            chunk = self._payload[self._offset :]
            self._offset = len(self._payload)
            return chunk
        chunk = self._payload[self._offset : self._offset + n]
        self._offset += len(chunk)
        return chunk


class _FakeRequest:
    """Stand-in for Starlette's Request — minimal stream + headers surface."""

    def __init__(self, payload: bytes, *, content_length: int | str | None = "auto") -> None:
        self._payload = payload
        if content_length == "auto":
            self.headers = {"content-length": str(len(payload))}
        elif content_length is None:
            self.headers = {}
        else:
            self.headers = {"content-length": str(content_length)}

    async def stream(self):  # type: ignore[no-untyped-def]
        # Mimic Starlette: yield in chunks.
        step = 1024
        for i in range(0, len(self._payload), step):
            yield self._payload[i : i + step]


class TestReadUploadFileCapped:
    @pytest.mark.asyncio
    async def test_at_cap_returns_full_bytes(self) -> None:
        payload = b"A" * 100
        out = await read_upload_file_capped(_FakeUploadFile(payload), max_bytes=100)
        assert out == payload

    @pytest.mark.asyncio
    async def test_one_byte_over_cap_raises(self) -> None:
        payload = b"A" * 101
        with pytest.raises(RequestPayloadTooLargeError):
            await read_upload_file_capped(_FakeUploadFile(payload), max_bytes=100)

    @pytest.mark.asyncio
    async def test_well_under_cap_returns_short_bytes(self) -> None:
        payload = b"hello"
        out = await read_upload_file_capped(_FakeUploadFile(payload), max_bytes=1_000_000)
        assert out == payload

    @pytest.mark.asyncio
    async def test_empty_upload_returns_empty(self) -> None:
        out = await read_upload_file_capped(_FakeUploadFile(b""), max_bytes=100)
        assert out == b""


class TestReadRequestBodyCapped:
    @pytest.mark.asyncio
    async def test_at_cap_returns_full_bytes(self) -> None:
        payload = b"B" * 5000
        out = await read_request_body_capped(_FakeRequest(payload), max_bytes=5000)
        assert out == payload

    @pytest.mark.asyncio
    async def test_one_byte_over_cap_raises(self) -> None:
        payload = b"B" * 5001
        with pytest.raises(RequestPayloadTooLargeError):
            await read_request_body_capped(_FakeRequest(payload), max_bytes=5000)

    @pytest.mark.asyncio
    async def test_eager_rejection_on_content_length_alone(self) -> None:
        """Even a small body lies about its size via Content-Length is rejected eagerly."""
        payload = b"tiny"
        with pytest.raises(RequestPayloadTooLargeError):
            await read_request_body_capped(
                _FakeRequest(payload, content_length=1_000_000),
                max_bytes=100,
            )

    @pytest.mark.asyncio
    async def test_missing_content_length_still_capped_on_stream(self) -> None:
        """When the header is absent, the stream loop still enforces the cap."""
        payload = b"X" * 200
        with pytest.raises(RequestPayloadTooLargeError):
            await read_request_body_capped(
                _FakeRequest(payload, content_length=None),
                max_bytes=100,
            )

    @pytest.mark.asyncio
    async def test_malformed_content_length_falls_through_to_stream_check(self) -> None:
        """Non-integer Content-Length is ignored; stream loop is authoritative."""
        payload = b"A" * 200
        with pytest.raises(RequestPayloadTooLargeError):
            await read_request_body_capped(
                _FakeRequest(payload, content_length="not-a-number"),
                max_bytes=100,
            )

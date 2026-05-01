"""Unverified JWT-payload decode for ``tulip auth status``.

The CLI does not have the API's signing secret and cannot verify
signatures. It only needs to read the payload locally — to display the
logged-in identity and the access-token expiry. The next real call to
the API will exercise the token; if the server rejects it, the CLI drops
it. So this module deliberately does **not** validate.

A signature library (``pyjwt``) would technically work for the
"decode-only" case, but pulling it in just for ``b64decode`` would be
silly. Stdlib only.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any


def _b64decode_segment(segment: str) -> bytes | None:
    # JWT uses URL-safe base64 with padding stripped. Restore the padding
    # to whatever ``len % 4 == 0`` requires.
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except (binascii.Error, ValueError):
        return None


def decode_jwt_payload(token: str) -> dict[str, Any] | None:
    """Return the JWT payload as a dict, or ``None`` if the token is malformed.

    "Malformed" includes: not three segments, payload not base64, payload
    not JSON, payload not a JSON object. Signature is not checked.
    """
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    raw = _b64decode_segment(parts[1])
    if raw is None:
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None

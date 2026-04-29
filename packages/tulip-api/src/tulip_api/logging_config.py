"""structlog configuration + PII redaction processor.

`configure_logging()` wires structlog to emit JSON to stdout (or whatever
the root logger's handler is), with timestamp, level, and request-scope
contextvars (request_id, household_id, user_id).

`redact_pii(logger, method_name, event_dict)` is a structlog processor
that replaces known-sensitive field values with the literal string
`<redacted>`. Field names match an explicit whitelist so a future log
call passing a new sensitive field will leak it; that's by design — we
catch leaks via tests, not by guessing patterns.
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any, Final

import structlog

REDACTED: Final[str] = "<redacted>"

# Whitelist of field names whose values must be redacted. Add to this set
# whenever a new sensitive payload type appears; tests in
# tests/test_logging.py enforce that every entry here is actually redacted.
_SENSITIVE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "password_hash",
        "totp_secret",
        "totp_secret_encrypted",
        "recovery_codes",
        "api_key",
        "authorization",
        "external_account_number",
        "external_account_number_encrypted",
        "notes_encrypted",
        "master_key",
        "master_key_wrapped",
    }
)


def redact_pii(
    _logger: object,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Replace known-sensitive values with the literal string '<redacted>'.

    Recurses into nested dicts and into list/tuple elements. Fields whose
    names are not on the whitelist pass through unchanged — the assumption
    is that we know what's sensitive, and unknown fields are explicitly
    treated as safe.
    """
    redacted = _redact_value(dict(event_dict))
    if not isinstance(
        redacted, dict
    ):  # pragma: no cover — _redact_value preserves dict at top level
        raise TypeError("redacted top-level value lost dict type")
    return redacted


def _redact_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            k: (REDACTED if k in _SENSITIVE_FIELDS else _redact_value(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(v) for v in value)
    return value


def configure_logging(level: int = logging.INFO) -> None:
    """Configure structlog to emit JSON-serialized records.

    Idempotent — calling more than once just rewires the same processors.
    """
    structlog.reset_defaults()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_pii,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

"""structlog configuration + PII redaction processor.

`configure_logging()` wires structlog to emit JSON to stdout, with
timestamp, level, and request-scope contextvars (request_id, household_id,
user_id). It also installs `_RedactExtraFilter` on the stdlib root logger
so any caller using `logging.getLogger(...).info("...", extra={...})`
(config module, dependency SDKs) gets the same whitelist applied to
`extra=` keys. Native `structlog.get_logger(...)` calls run through the
`redact_pii` processor directly. See #220 for context.

Residual gap: %-style positional args (e.g. `log.info("user %s", email)`)
inside the format string are NOT covered — neither pipeline parses the
formatted message. This affects `uvicorn.access` URL paths with embedded
UUIDs; documented in `THREAT_MODEL.md` as a Phase-9 concern.

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
        # Email is personal data under GDPR Art. 4 §1; threat-model §1.4 + §2
        # promise it's redacted-by-default. #220 (H-5).
        "email",
        "user_email",
        # IP + user-agent are personal data per GDPR Recital 30 / Art. 4(1).
        # They're captured on every auth event (register / login / MFA /
        # refresh / logout / recovery) into ``sessions.ip_address`` /
        # ``audit_log.ip_address`` — that's the at-rest fate. This whitelist
        # entry keeps the same values from leaking into structlog files in
        # the clear. #246 (M-2).
        "ip_address",
        "user_agent",
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


_REDACTOR_INSTALLED_MARKER: Final[str] = "_tulip_stdlib_redactor_installed"


def _install_stdlib_redactor() -> None:
    """Monkeypatch `Logger.makeRecord` so stdlib `extra={...}` keys get redacted.

    Structlog calls are already redacted by the `redact_pii` processor.
    Stdlib callers — `tulip_api.config`, dependency SDKs, uvicorn — bypass
    that pipeline. Neither the LogRecord factory (extras land AFTER the
    factory) nor a Filter on the root logger (Python's filter chain
    doesn't re-run on propagation from child loggers) catches the case
    we care about, which is `log.info("...", extra={"password": x})` on
    a child logger.

    `Logger.makeRecord` is the right chokepoint: it's the method that
    constructs the LogRecord *and* applies the `extra` dict to it, so
    redacting after `makeRecord` returns catches both cases. The marker
    on the wrapper prevents double-installation across repeated
    `configure_logging` calls.

    Caveat: %-style positional args inside the format string (e.g.
    `log.info("user %s", email)`) are NOT redacted — the wrapper doesn't
    know the field name of a positional arg. That residual gap (mostly
    `uvicorn.access` URL paths with embedded UUIDs) is documented in
    `THREAT_MODEL.md` as a Phase-9 concern.
    """
    if getattr(logging.Logger.makeRecord, _REDACTOR_INSTALLED_MARKER, False):
        return

    original = logging.Logger.makeRecord

    def _make_record_redacted(
        self: logging.Logger,
        *args: Any,  # noqa: ANN401 — passthrough to Logger.makeRecord signature
        **kwargs: Any,  # noqa: ANN401 — passthrough to Logger.makeRecord signature
    ) -> logging.LogRecord:
        record = original(self, *args, **kwargs)
        for key in list(record.__dict__.keys()):
            if key in _SENSITIVE_FIELDS:
                record.__dict__[key] = REDACTED
        if isinstance(record.args, dict):
            record.args = {
                k: (REDACTED if k in _SENSITIVE_FIELDS else v) for k, v in record.args.items()
            }
        return record

    setattr(_make_record_redacted, _REDACTOR_INSTALLED_MARKER, True)
    logging.Logger.makeRecord = _make_record_redacted  # type: ignore[method-assign]


def configure_logging(level: int = logging.INFO) -> None:
    """Configure structlog to emit JSON-serialized records.

    Also installs a LogRecord factory wrapper so any `extra={...}` payload
    passed to `logging.getLogger(...).info(...)` gets the same whitelist
    redaction the structlog pipeline applies (#220 H-6). Idempotent.
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
    _install_stdlib_redactor()

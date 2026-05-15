"""Schemathesis contract test — drives the OpenAPI spec against the live app.

Generates inputs for every documented operation and asserts the response
status code is in the operation's declared ``responses`` set, the
content-type matches the response media type, and the body conforms to
the declared schema. After P2.x.2 every error path is RFC 9457 Problem
Details, so this test gates against future regressions of either:

- An endpoint returning a status code it didn't document.
- An endpoint returning a body that doesn't match its declared schema
  (e.g. a 401 that drops to FastAPI's plain ``{"detail": ...}`` shape
  instead of the Problem Details model).

Settings: 25 examples per operation. Tunable by setting the
``HYPOTHESIS_PROFILE=thorough`` env var (see ``hypothesis.settings``
profiles below) for occasional deeper sweeps; default is ``ci``.
"""

from __future__ import annotations

import os

import pytest
import schemathesis
from hypothesis import HealthCheck
from hypothesis import settings as hyp_settings

from tulip_api.config import Settings, get_settings
from tulip_api.deps import get_session
from tulip_api.main import create_app

# Hypothesis profiles: "ci" is the default for the dev/CI loop; "thorough"
# exists for ad-hoc deeper runs (HYPOTHESIS_PROFILE=thorough uv run pytest …).
#
# filter_too_much is suppressed because a few endpoints (notably POST
# /v1/transactions) have body schemas where hypothesis can't easily
# generate "interesting" valid data — 3-letter currency codes, Decimal
# amounts, UUID account_ids. Suppressing the health check lets those
# operations still run; coverage is naturally lower for them but the
# basic contract (status code in declared set, body conforms to schema)
# still gets exercised.
_SUPPRESSED = [HealthCheck.filter_too_much, HealthCheck.data_too_large]
# 10 examples per endpoint x ~80 endpoints = ~800 fuzz iterations per CI run.
# Per ADR-0006, schemathesis was the dominant cost in the tulip-api shard
# under the option 3 matrix (9:10 of a 10-min CI wall-clock). Dropping from
# 25 → 10 examples cuts schemathesis time by ~60% with diminishing returns
# beyond ~10 examples per endpoint on the kinds of bugs hypothesis catches
# at this layer (status-code-in-declared-set + body-conforms-to-schema).
# The "thorough" profile (max_examples=200) remains for ad-hoc deeper runs.
hyp_settings.register_profile(
    "ci", max_examples=10, deadline=None, suppress_health_check=_SUPPRESSED
)
hyp_settings.register_profile(
    "thorough", max_examples=200, deadline=None, suppress_health_check=_SUPPRESSED
)
hyp_settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


def _build_app():
    """Create a fresh app instance with the test settings + an in-memory DB.

    Migrations are run by ``packages/tulip-api/tests/conftest.py``'s
    ``db_url`` fixture for the regular tests, but schemathesis bypasses
    those fixtures and goes straight against ``app``. We need a fully
    migrated database here too.
    """
    # Per-process DB so the contract test is hermetic — schemathesis
    # generates random inputs and we don't want it to touch any other
    # test's state. The PID suffix makes the path unique per xdist worker;
    # without it, parallel collection races on the same file and yields
    # "Different tests were collected between gw0 and gwN" errors.
    import os
    import tempfile
    from collections.abc import Iterator
    from pathlib import Path

    from alembic.command import upgrade
    from alembic.config import Config
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import NullPool

    db_path = Path(tempfile.gettempdir()) / f"tulip-schemathesis-{os.getpid()}.db"
    db_path.unlink(missing_ok=True)
    db_url = f"sqlite:///{db_path}"

    alembic_ini = Path(__file__).resolve().parents[2] / "tulip-storage" / "alembic.ini"
    cfg = Config(str(alembic_ini))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option(
        "script_location",
        str(alembic_ini.parent / "src" / "tulip_storage" / "migrations"),
    )
    upgrade(cfg, "head")

    # NullPool: this engine lives at module scope (until process exit),
    # so a normal QueuePool would retain up to 5 connections per xdist
    # worker for the full test run. NullPool closes each connection
    # when it's checked back in, eliminating the FD residency. See #90.
    engine = create_engine(db_url, future=True, poolclass=NullPool)

    @event.listens_for(engine, "connect")
    def _enable_fk(dc, _r):  # type: ignore[no-untyped-def]
        c = dc.cursor()
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    sm: sessionmaker[Session] = sessionmaker(engine, expire_on_commit=False)

    test_settings = Settings(
        database_url=db_url,
        jwt_secret="test-secret-32bytes-test-secret!!",
        master_key=b"\xab" * 32,
    )

    app = create_app()

    def _override_session() -> Iterator[Session]:
        with sm() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_settings] = lambda: test_settings
    return app


_APP = _build_app()
_SCHEMA = schemathesis.openapi.from_asgi("/openapi.json", _APP)


@pytest.fixture(autouse=True)
def _disable_auth_rate_limit_for_schemathesis():  # type: ignore[no-untyped-def]
    # H-4 (#219): each parametrized schemathesis case can fire dozens of
    # requests against the same endpoint within one pytest test, easily
    # exceeding the 10/min /v1/auth/* limit. The contract test's brief is
    # schema conformance, not the limiter — so we disable the gate for
    # the duration of each case.
    from tulip_api.auth.rate_limit import limiter as _auth_limiter

    previous = _auth_limiter.enabled
    _auth_limiter.enabled = False
    try:
        yield
    finally:
        _auth_limiter.enabled = previous


@_SCHEMA.parametrize()
def test_api_conforms_to_schema(case: schemathesis.Case) -> None:
    """Every documented operation: response shape matches the OpenAPI spec.

    Schemathesis runs the case against the in-process app (no real
    network) and asserts:

    - Response status code is in the operation's declared ``responses``.
    - Response Content-Type matches the declared media type.
    - Response body conforms to the declared schema (which for error
      paths is ``ProblemDetailsResponse``).

    A failing assertion here means either an endpoint emits an
    undocumented response, or the documented schema doesn't actually
    describe what comes back. Both are real bugs.
    """
    # Path-collision skip: a static path segment is syntactically a valid
    # value for a parameterised sibling, so FastAPI routes an
    # undocumented-method probe to that sibling instead of returning 405,
    # and schemathesis can't tell the difference. Examples:
    #   - GET /v1/imports/multi-account → routes to GET /v1/imports/{batch_id}
    #     (`multi-account` is a valid {batch_id}) → 401 on auth, not 405.
    #   - DELETE /v1/ai/proposals/kinds → routes to DELETE
    #     /v1/ai/proposals/{proposal_id} (#240) → 422 (bad UUID), not 405.
    #   - DELETE /v1/users/me → routes to DELETE /v1/users/{user_id} (#242)
    #     → 401, not 405.
    # Each collision is harmless. Map every shadowed static path to the one
    # method it actually documents; skip schemathesis cases targeting the
    # other methods, and (for the documented method's own case) exclude
    # the unsupported-method check so the probe-driven 405 expectation
    # doesn't false-positive on the parametric sibling's response.
    _SHADOWED_STATIC_PATHS = {
        "/v1/imports/profiles/import": "POST",
        "/v1/imports/multi-account": "POST",
        "/v1/ai/proposals/kinds": "GET",
        "/v1/users/me": "PATCH",
    }
    documented_method = _SHADOWED_STATIC_PATHS.get(str(case.path))
    if documented_method is not None and case.method.upper() != documented_method:
        pytest.skip(
            "path-collision: an undocumented method on a static path routes to "
            "a parameterised sibling; harmless — see comment."
        )

    from schemathesis.specs.openapi.checks import unsupported_method

    excluded = [unsupported_method] if documented_method is not None else None
    case.call_and_validate(excluded_checks=excluded)

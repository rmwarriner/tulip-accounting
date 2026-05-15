# ADR-0006: CI test-runtime — what was measured, what works, what to try next

**Status:** Open / informational. No code change shipped with this ADR.

**Date:** 2026-05-11

## Context

By Phase 6 close (1426 tests, 1398→1418→1426 across the last three slices)
the CI ``Test (pytest + coverage)`` job had grown to **~14 minutes
wall-clock**. The aggregate ``All checks passed`` gate sits behind it,
so PR cycle time is bottlenecked on this single job.

This ADR is the snapshot of "what we measured, what we tried, what
the data says to try next" so a future session doesn't redo the
investigation blind.

## Measurement

Reproducer: ``uv run pytest --durations=50 -q`` against ``main``
(post-PR #173, urllib3 CVE bump). Variants:

| Configuration | Wall-clock (full suite) | CLI subset (294 tests) |
|---|---|---|
| Sequential (no xdist) | 7m37s (457s) | 4m55s (295s) |
| ``-n auto --maxprocesses 4`` (xdist, baseline) | (~5–6 min) | **1m35s (95s)** |
| ``-n auto`` + template-DB pattern (option 1 below) | (~5–6 min) | 2m04s (124s) — *worse, see below* |

CI uses ``-n auto --maxprocesses 4`` per ``justfile`` so the xdist
numbers are the operative ones.

### Where time goes (xdist, CLI subset)

Top 50 ``--durations`` are dominated by **setup** time in the CLI
tests, not call time:

- **Top 2 calls:** ``test_imports_apply_*`` at ~6s each (CLI integration
  with subprocess + uvicorn round-trip).
- **Next ~48 entries:** 1.17–2.30s **of setup** per CLI test, dominated
  by ``packages/tulip-cli/tests/test_p36_read_edit.py``,
  ``test_envelopes.py``, ``test_reconcile_command.py``, etc.
- **Schemathesis / hypothesis property tests:** zero in the top 50.
  They're each fast; the count (~80 schemathesis instances) is
  manageable.

The CLI ``live_api`` fixture (``packages/tulip-cli/tests/conftest.py``)
is the culprit. It is per-test scope and does two expensive things:

1. ``alembic upgrade head`` against a fresh SQLite (walks ~14
   migrations) — historically ~700ms.
2. ``Popen(uvicorn ... tulip_api.main:create_app)`` + ``_wait_for_health``
   polling — ~400ms–1s subprocess boot for the FastAPI app.

Under xdist with 4 workers and 294 CLI tests, each worker runs ~73
tests serially. The alembic walk's cost is amortised at the worker
level (each worker pays it once, not per-test in the pytest sense)
**when scoped correctly**. The uvicorn subprocess is paid per-test
regardless.

### Why option 1 (template-DB) didn't help under xdist

The naive read was: replace the per-test ``alembic upgrade head`` with
a session-scoped template DB + ``shutil.copyfile`` per test. Predicted
saving: ~700ms × 73 tests/worker × 4 workers ÷ 4 workers wall-clock
= ~50s.

What actually happened: measured wall-clock went **up** by ~30s under
xdist. The explanation is that pytest's session-scoped fixtures are
already per-worker under xdist (each worker gets its own session); the
alembic walk only ran ~4 times to begin with, not 294. The
``shutil.copyfile`` per test added IO contention without removing real
work. Net negative.

**Take-away:** sequential and parallel test runs have different
bottlenecks. Don't optimise based on sequential profiles.

## Options considered (with revised priority)

### Option 2 — Session-scoped uvicorn + UUID-based test isolation *(implemented and reverted)*

Hoist the ``live_api`` fixture from ``scope=function`` to
``scope="session"``. One uvicorn per xdist worker × 4 workers = 4
boots total instead of ~150 (one per CLI test that uses the fixture).
Per-worker savings ≈ ~70 tests × 800ms boot avg = ~55s.

**Status: tried 2026-05-11, reverted.** Measured wall-clock dropped
~50% (sequential 7m37s → 3m40s; CI extrapolation 14 min → ~7 min),
but the implementation introduces a **persistent ~25-50% flake rate**
that smaller mitigations did not eliminate. Documented here so a
future attempt has the full forensics.

What was implemented:

- ``live_api`` fixture changed to ``scope="session"`` per xdist worker.
- New shared fixtures in ``conftest.py``: ``unique_id`` (per-test 8-hex
  string) and ``registered_user`` (UUID-derived email + household +
  per-test token store).
- Every CLI test file's local ``authed_session`` / ``access_token``
  fixtures refactored to derive their email + household name from
  ``unique_id`` so concurrent tests don't collide.
- ``test_auth_login.py``'s helper-based pattern got a bespoke
  refactor (each test gets an ``email = f"alice-{unique_id}..."``
  local variable threaded through).
- CLI subprocess timeouts bumped from 10s/15s/20s → 30s across all
  ~99 ``subprocess.run`` call sites.
- ``TulipClient`` default HTTP timeout bumped 10s → 30s.

What broke:

- Login operations occasionally exceed even 30s under load.
- The root cause is **CPU contention from argon2id password
  verification**. Tulip's API uses argon2id at OWASP-2024 minimum
  parameters (memory ≈ 19MiB, time ≈ 2). Under xdist with 4 workers
  each running their own uvicorn (so 8+ Python processes on a
  4-core machine), argon2id verifications queue and starve the
  uvicorn asyncio loop.
- Reducing xdist to 2 workers reduced flake rate but didn't
  eliminate it (~30-50% per-run rate at 2 workers, vs ~25-50% at 4).
- Bumping HTTP timeouts to 30s only partially helps because the
  delays sometimes exceed even that bound.

What would need to happen to make option 2 work:

1. **Add an env-driven argon2 parameter knob** (e.g.
   ``TULIP_ARGON2_TEST_MODE=1`` selects ``time_cost=1, memory_cost=128``
   for tests). This is intrusive but eliminates the CPU contention
   class. The production parameters stay at OWASP-2024 minimum.
2. **Or add ``pytest-rerunfailures`` retry** as a safety net. Standard
   industry pattern for genuinely-flaky-under-load suites. Re-runs
   each timeout failure once before reporting. Tolerates the contention
   without changing it.
3. **Or run uvicorn with multiple workers** so concurrent argon2
   verifications can run in parallel processes. Adds boot complexity
   to the fixture; uvicorn ``--workers N`` doesn't compose cleanly
   with ``--factory`` in dev/test setups.

Without one of those mitigations, option 2 trades 50% wall-clock for
unacceptable flake rate. The git history of branch
``perf/p2-session-live-api`` (since deleted) has the full
implementation if anyone wants to reproduce the measurement.

### Option 3 — Per-package CI matrix *(zero test changes, modest wall-clock win)*

Split the ``Test (pytest + coverage)`` GitHub Actions job into a
matrix over the seven workspace packages. Each shard runs in its own
runner and produces a partial ``.coverage`` file; a final
``coverage combine + coverage report --fail-under=85`` job aggregates.

Cost: low — a YAML refactor of ``.github/workflows/ci.yml``. No test
changes. Risk: low — coverage aggregation is standard but slightly
fiddly; the architecture-test boundary between packages is enforced
anyway so dependencies are predictable. Reward: dominant package
(probably ``tulip-cli``) becomes the wall-clock pole. Today the CLI
subset is ~95s under xdist; if it stays at 95s and other shards land
in 60–90s, total wall-clock ≈ **2–3 min** instead of 14.

**Hidden cost:** total CI minutes consumed goes *up* (every shard pays
the ``uv sync`` cold-start; ~30–60s × 7 shards). On free GitHub
runners that's fine; on paid minutes it's a tradeoff.

### Option 4 — Coverage on a separate shard *(complementary to 2 or 3)*

Coverage instrumentation typically adds 20–40%. Run the coverage gate
on ``push`` to ``main`` only, and on PR builds run uninstrumented
tests. Two flavours:

- A ``post-merge`` job that gates the deploy/release rather than the
  PR.
- A label-driven extra check (``ci:coverage`` label opts in for the
  PR build).

Net: 20–40% off the PR's test-job wall-clock. Tradeoff: coverage
regressions hit ``main`` rather than getting blocked at PR. With
``main`` branch protection requiring linear history, a coverage
failure on push forces a quick fix — manageable for a solo project.

### Option 5 — pytest-testmon for incremental selection *(disregard for now)*

Skip tests whose dependency tree didn't change since last green.
Powerful but requires storing the testmon DB across runs (S3, cache
action). High setup cost, brittle, and the savings vanish on
``main``-target PRs. Not worth it at the current scale.

### Option 6 — Schemathesis ``max_examples`` cap *(implemented 2026-05-12)*

Reduce the "ci" hypothesis profile's ``max_examples`` from 25 to 10 in
``packages/tulip-api/tests/test_openapi_contract.py``. The "thorough"
profile at 200 remains available for ad-hoc deeper runs via
``HYPOTHESIS_PROFILE=thorough``.

**Status: shipped on top of option 3.** When option 3's matrix split
landed, the tulip-api shard surfaced as the dominant wall-clock pole
at ~9:10 — schemathesis (~80 fuzz endpoints × 25 examples = ~2000
iterations) was carrying most of that cost. Cutting examples to 10
reduces schemathesis fuzz coverage by ~60% with diminishing returns
on the bug classes hypothesis catches at this layer (status-code-in-
declared-set + body-conforms-to-schema; structural bugs surface
within ~5 examples per endpoint, deeper iterations are extra
property-test runs against the same hot path).

After the option 2 experiment (2026-05-11):

1. **Option 3 (CI matrix)** is now the highest-ROI next step. Zero
   code changes, no flake risk, modest wall-clock win. Layered with
   option 4 it could reasonably hit 4-6 min CI without touching tests.
2. **Option 2 (session-scoped uvicorn)** is **not recommended as-is**
   — see the "What broke" subsection above. Worth revisiting only if
   paired with one of the three mitigations listed there (argon2 test
   parameters, ``pytest-rerunfailures``, or uvicorn multi-worker).
3. **Option 4 (coverage shard)** is independent and complementary to
   either of the above.

**Do NOT do Option 1** (template-DB). Measured negative under xdist;
the documentation here is to prevent rediscovering this.

## Decision tree for the next session

If revisiting CI runtime:

1. **First, re-measure.** The baseline shifts as tests are added.
   ``time just test`` and ``time uv run pytest packages/tulip-cli/tests/
   -n auto --maxprocesses 4 -q`` against current ``main``.
2. **Default next step: Option 3** (CI matrix). It's the only
   remaining option that has no flake risk and requires no test
   refactoring. Start there.
3. **If option 3 plus option 4 (coverage shard) isn't enough** to hit
   the desired wall-clock: revisit option 2 *but only after*
   introducing argon2 test-mode parameters or
   ``pytest-rerunfailures``. The plain session-scoped fixture is
   measurably flaky.
4. **Always re-measure after each change.** xdist behaviour is
   counter-intuitive; "obvious" optimisations like option 1 can
   regress. The 2026-05-11 option-2 attempt looked like a 50%
   wall-clock win in single runs but degraded into a flake parade
   across repeated runs.

## Consequences

If we ship option 2 + option 3:

- CI wall-clock drops from ~14 min to ~3–5 min.
- Test isolation discipline tightens (tests must be UUID-safe). This
  is positive for parallel-friendliness in general.
- Coverage gate stays at 85% project / 90% ``tulip-core``; aggregation
  step in CI is the only new operational complexity.

If we ship nothing:

- ~14 minute CI is the steady state. Painful but not blocking; PRs
  still merge in <30 minutes including local pre-push checks.
- Future test additions slowly creep the gate longer; the eventual
  intervention is still options 2 + 3.

## Status update mechanism

Update this ADR's measurement section when:

- Any of options 2–4 ship (record the new wall-clock).
- Test count crosses 2000 or wall-clock crosses 20 min (whichever
  first) — re-profile and decide whether to invest.
- A change to the ``live_api`` fixture in
  ``packages/tulip-cli/tests/conftest.py`` (any scoping change
  invalidates the analysis here).

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

### Option 2 — Session-scoped uvicorn + UUID-based test isolation *(highest measured leverage)*

Hoist the ``live_api`` fixture from ``scope=function`` to
``scope="session"``. One uvicorn per xdist worker × 4 workers = 4
boots total instead of ~150 (one per CLI test that uses the fixture).
Per-worker savings ≈ ~70 tests × 800ms boot avg = ~55s; wall-clock
delta with 4 workers ≈ **same ~55s** since each worker independently
amortises.

What it requires:

- Every CLI test that registers a household must use a **unique email +
  household name** per test (e.g. ``f"user-{uuid4().hex[:8]}@example.com"``).
  Today tests assume a fresh DB and reuse ``me@example.com`` /
  ``admin@example.com``. Refactor: ~30–50 tests, mechanical.
- The ``live_api`` fixture's underlying DB still needs to start fresh
  per worker (so worker A doesn't see worker B's data). That's
  naturally session-scoped already if we session-scope the fixture.
- Tests that depend on a *specific* DB state (e.g. "an empty
  household") need an explicit per-test reset. Most CLI tests are
  insert-only and don't care.

Cost: medium (refactor the ~30–50 tests that hard-code emails / names).
Risk: medium (test isolation failure shows up as flaky tests; needs
careful audit). Reward: ~55s wall-clock, gets the test job under
~9 minutes.

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

### Option 6 — Schemathesis ``max_examples`` cap *(now redundant)*

The schemathesis suite is fast enough that none of its tests appear
in the top 50 ``--durations``. Capping examples would help in absolute
total but not visibly. Skip unless it later becomes a long pole.

## Recommendation

Roughly in priority order:

1. **Option 2** (session-scoped uvicorn) is the single highest-leverage
   change. The refactor is mechanical (UUID-ify the household-creating
   test code paths) but touches a number of files. Best ROI per LoC.
2. **Option 3** (CI matrix) layered on top of option 2 reduces
   wall-clock further by parallelising the packages. Zero test
   changes.
3. **Option 4** (coverage shard) is independent and complementary.

**Do NOT do Option 1** (template-DB). Measured negative under xdist;
the documentation here is to prevent rediscovering this.

## Decision tree for the next session

If revisiting CI runtime:

1. **First, re-measure.** The baseline shifts as tests are added.
   ``time just test`` and ``time uv run pytest packages/tulip-cli/tests/
   -n auto --maxprocesses 4 -q`` against current ``main``.
2. **If wall-clock is dominated by a single package** (likely
   ``tulip-cli``): option 2 is the target.
3. **If wall-clock is spread roughly evenly across packages**:
   option 3 is the target.
4. **If both look similar**: option 2 first (it reduces real CPU work,
   not just parallelism), then option 3 if needed.
5. **Always re-measure after each change.** xdist behaviour is
   counter-intuitive; "obvious" optimisations like option 1 can
   regress.

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

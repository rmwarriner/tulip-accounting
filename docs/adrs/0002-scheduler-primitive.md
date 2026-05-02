# ADR 0002 — In-process scheduler primitive (`scheduled_jobs`)

**Status:** Accepted (2026-05-02) — adopted on P4.3.a merge.
**Phase:** 4 (P4.3.a — runner primitive).
**Supersedes:** None.

---

## Context

[ARCHITECTURE.md §5.7](../ARCHITECTURE.md) and [issue #7](https://github.com/rmwarriner/tulip-accounting/issues/7) commit Tulip to a server-side scheduler. P4.3 (envelope refill execution) is the first consumer; future consumers will include reconciliation reminders (Phase 5), AI cost roll-ups (Phase 6), and report-pack generation (Phase 7).

The architecture pre-commits to an **in-process** runner for v1 — single-household-on-a-Pi is the deployment target, and a separate worker process or external broker (Redis, RabbitMQ) is unjustified at that scale. Phase 9 will revisit when multi-tenant cloud requires worker-process separation.

What §5.7 leaves open, and what this ADR settles:

1. Which scheduler library — `apscheduler`, `rq`, `taskiq`, custom, or none?
2. The public surface of the runner (handlers, scheduling, cancellation).
3. The data model — one table or two? How does it relate to the `scheduled_transactions` schema sketch in §4.1?
4. Cadence representation — RRULE, cron expression, custom DSL?
5. How tests inject deterministic time.
6. Failure handling — retry policy, dead-letter behavior.
7. Concurrency — what does v1 assume about uvicorn worker count?

ADR-0001 set the precedent of resolving these design questions explicitly before code lands. P4.3.a follows the same discipline.

## Decision

### 1. Runner architecture: simple async loop in a FastAPI lifespan task

A `Runner` class spawned via `asyncio.create_task` in the FastAPI app's `lifespan` context. The loop polls `scheduled_jobs` for rows where `next_run_at <= clock()` and `is_active = true`, dispatches each to its registered handler, records the run in `scheduled_job_runs`, and re-schedules recurring jobs. ~80 LOC for the core loop.

Rejected alternatives:

- **`apscheduler`** — adds a 60+ KB code surface, ships threaded and asyncio modes that interact awkwardly with SQLAlchemy session lifecycles, and brings its own jobstore abstraction (which we'd then have to bridge to our DB anyway).
- **`rq` / `taskiq` / `dramatiq`** — broker-backed; require Redis or similar, which violates v1's single-process target.
- **`celery`** — heaviest of the bunch, and a notoriously sharp configuration edge.
- **Pure cron (system-level)** — invisible to in-process observability, can't share the application's DB session, and complicates the deployment story.

The simple async loop is small enough that maintaining it ourselves is cheaper than learning a library's quirks.

### 2. Public surface

The runner exposes four methods. Handlers are async; the runner construction is sync:

```python
def register_handler(
    kind: str,
    callback: Callable[[ScheduledJob, Clock], Awaitable[None]],
) -> None: ...

def schedule_one(
    kind: str,
    payload: dict,
    fire_at: datetime,
    *,
    idempotency_key: str | None = None,
) -> UUID: ...  # returns scheduled_job.id

def schedule_recurring(
    kind: str,
    payload: dict,
    rrule: str,
    *,
    start_at: datetime | None = None,
    idempotency_key: str | None = None,
) -> UUID: ...

def cancel(job_id: UUID) -> None: ...  # flips is_active=False; no run rows touched
```

`idempotency_key` is included from day one — preventing the "envelope created twice creates two refill schedules" footgun is essentially free at table-create time (unique partial index per household). `register_handler` is sync because handler registration happens at import time, not in the event loop.

There is deliberately no `list_jobs` method on the runner — that's a query, not a runner concern, and it lives on `ScheduledJobRepository`.

### 3. Two-table data model

- **`scheduled_jobs`** — the schedule itself. User-meaningful state: `kind`, `payload`, `rrule`, `next_run_at`, `last_run_at`, `is_active`, `idempotency_key`.
- **`scheduled_job_runs`** — per-fire operational state: `scheduled_job_id`, `started_at`, `completed_at`, `status` (`success` | `failed` | `dead_letter`), `retry_count`, `last_error`.

Both are distinct from `audit_log`. Audit answers "what changed to user-visible state" — one row per materialized shadow tx. Run records answer "did the runner fire, did it fail, retry count, last error" — operational state irrelevant to the user but essential for the operator. Folding them would conflate two readers and force `audit_log` to grow a `retry_count` column.

### 4. Single generic `scheduled_jobs` (reconciles §4.1's `scheduled_transactions` sketch)

ARCHITECTURE.md §4.1 sketches a domain-specific `scheduled_transactions` table; issue #7 sketches a generic `scheduled_jobs`. These are **not** the same thing, and we must reconcile.

**Decision**: `scheduled_jobs` is the literal table. "Schedule a transaction" becomes `kind="materialize_transaction"` with `payload={template, household_id}`. P4.3.b's first consumer is `kind="envelope_refill"` with `payload={"envelope_id": ...}`. The §4.1 sketch becomes a documentation artifact — the literal table that ships is the generic one.

This avoids parallel scheduling infrastructure and keeps the "one runner, many handlers" abstraction clean.

### 5. RRULE via `python-dateutil`

`scheduled_jobs.rrule` stores an RFC 5545 RRULE string (e.g., `"FREQ=MONTHLY;BYMONTHDAY=1"`). `python-dateutil`'s `rrulestr` parses it; `rrule.after(dtstart=last_run_at)` computes the next fire. After each successful run, the runner writes `next_run_at = rrule.after(now)` and updates the row. Rolling our own cadence DSL is the wrong build/buy call.

`python-dateutil` is added to the root `pyproject.toml`. It's not currently a transitive dep of SQLAlchemy or pydantic v2 in this project.

### 6. Clock injection

`Clock = Callable[[], datetime]`, threaded through the `Runner` constructor (default `lambda: datetime.now(UTC)`) and into every handler invocation as the second positional argument.

This is the only project-wide time-injection seam in v1. The refill-rule evaluation engine (P4.3.b) is pure and takes no clock — it accepts `current_balance: Money` and `recent_inflow: Money | None` as inputs, computed by the handler from a query at fire time.

`freezegun` is **not** added. Tests that need to exercise the runner pass a fake `Clock` whose return value the test advances explicitly. Tests that don't exercise the runner don't need time injection.

### 7. Retry policy

On handler exception, the runner records a `scheduled_job_runs` row with `status="failed"` and `retry_count=N`, then schedules a retry at `now + backoff[N]` where `backoff = [60s, 300s, 1800s]`. After the third failure, the runner writes a row with `status="dead_letter"`, marks the parent `scheduled_jobs.is_active=false`, and stops attempting. Manual reactivation (CLI / admin endpoint) is required to resume.

The user is notified of dead-lettering only via the operational log in v1 — in-app notifications are out of scope for Phase 4.

### 8. Concurrency: single-worker assumption

The poll loop does not lock rows. Running `uvicorn` with `--workers > 1` would race two pollers against the same `next_run_at <= now` query and double-fire jobs. v1 assumes single-worker deployment.

**This is a known limitation, documented prominently.** Multi-worker safety lands in Phase 9 via either:
- `SELECT … FOR UPDATE SKIP LOCKED` (PostgreSQL native; SQLite has no equivalent).
- A leader-election layer (e.g., advisory locks or an external coordination service).

The v1 deployment story (Docker compose, single uvicorn worker, single-household home server) does not encounter this constraint.

## Consequences

### Positive

1. **Minimal new surface area.** The whole runner is ~150 LOC; handlers are pure functions of `(job, clock)`. Easy to audit, easy to test.
2. **Single-loop, single-process** matches the deployment target and avoids any broker-style operational complexity.
3. **Generic `scheduled_jobs` enables future consumers** without schema changes: Phase 5 reconciliation reminders, Phase 6 AI cost reports, Phase 7 weekly report packs all register a `kind` and reuse the same infra.
4. **RRULE via dateutil** is stable, well-trodden, and compatible with calendar tools users already understand.
5. **Clock injection** keeps tests deterministic without a global time-mocking dep.
6. **Idempotency keys** prevent the most obvious schedule-duplication footgun.

### Negative

1. **Single-worker constraint** must be enforced operationally. If a deployer runs `uvicorn --workers 4`, the runner will misbehave silently. Documented in DEPLOYMENT.md (lands in Phase 8) and in the runner module's docstring.
2. **Owning the loop** means we own its bugs. Unfair scheduling under heavy load, async-task cancellation edge cases, and SQLAlchemy session lifecycle around long-running handlers are all our responsibility.
3. **Phase 9 worker-process extraction** will require a meaningful refactor. The runner module is colocated in `tulip-storage` for v1 (it's coupled to the SQLAlchemy session); extracting it means either pulling it into a new package or vendoring the SQLAlchemy bits into a worker.
4. **No multi-tenant fairness in v1.** A household with thousands of scheduled jobs could starve another household's jobs in a future multi-tenant deployment. Addressed alongside the Phase 9 worker split.

### Neutral

1. **`python-dateutil` is a real new dep.** ~250 KB, zero-maintenance, gold standard for calendar arithmetic. Worth the cost.
2. **The runner module is colocated in `tulip-storage`** because it's tightly coupled to the SQLAlchemy session via the repositories it calls. A standalone `tulip-runner` package would either need to import `tulip-storage` (defeating the separation) or duplicate the session machinery (worse). Revisit at Phase 9.
3. **Handler responsibility for idempotency at the action level** — the runner guarantees at-most-once successful fire per `next_run_at` cycle, but the handler is responsible for not double-counting if the same job is replayed (e.g., after manual reactivation). For envelope refills (P4.3.b), the shadow tx's `idempotency` is already handled by the per-fire `started_at` boundary.

## Alternatives considered (and why rejected)

### apscheduler

The most plausible alternative. Has good RRULE support, a sane API, and active maintenance. Rejected because:

- 60+ KB code surface for what is fundamentally an in-process polling loop.
- Threading and asyncio executor modes interact awkwardly with SQLAlchemy session scopes — handlers running in the threaded executor can't share the request-scoped session.
- apscheduler's `SQLAlchemyJobStore` would persist *its* state model alongside ours, doubling the bookkeeping.

The simple async loop is genuinely simpler at our scale.

### `rq` / `taskiq` / `dramatiq`

All require a broker (Redis or AMQP). Even the lightest broker is a deployment ask we don't want to make at single-household scale. Reconsidered when Phase 9 splits the runner into its own process.

### Celery

Strictly more configuration than `rq`, with a steeper learning curve. Same broker constraint.

### System cron + a CLI handler

Doable: cron triggers `tulip refills run-due` every N minutes. Rejected because:

- Two coordination layers (cron + Tulip's own scheduling state) is a recipe for drift.
- No in-process observability — the runner's internal state (next_run_at, dead-letter status) wouldn't be queryable from inside the API.
- Deployment story complicates: every Tulip install needs a cron entry as well as the API process.

### Pure event-loop scheduling (asyncio.call_at)

Trivial for in-process scheduling but loses persistence. A restarted process forgets every scheduled job. The DB-backed design is non-negotiable for an accounting tool.

## Implementation notes

### Schema (P4.3.a migration sketch)

```sql
CREATE TABLE scheduled_jobs (
  id                BLOB    NOT NULL,           -- UUID
  household_id      BLOB    NOT NULL,
  kind              TEXT    NOT NULL,
  payload           TEXT    NOT NULL,           -- JSON
  rrule             TEXT,                       -- nullable; schedule_one jobs have no rrule
  next_run_at       TIMESTAMP NOT NULL,
  last_run_at       TIMESTAMP,
  idempotency_key   TEXT,                       -- nullable
  is_active         BOOLEAN NOT NULL DEFAULT 1,
  created_by_user_id BLOB,
  created_at        TIMESTAMP NOT NULL,
  updated_at        TIMESTAMP NOT NULL,
  PRIMARY KEY (household_id, id),
  FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE CASCADE
);
CREATE INDEX ix_scheduled_jobs_next_run ON scheduled_jobs(next_run_at)
  WHERE is_active = 1;
CREATE UNIQUE INDEX ix_scheduled_jobs_idempotency
  ON scheduled_jobs(household_id, kind, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE TABLE scheduled_job_runs (
  id                BLOB    NOT NULL,           -- UUID
  household_id      BLOB    NOT NULL,
  scheduled_job_id  BLOB    NOT NULL,
  started_at        TIMESTAMP NOT NULL,
  completed_at      TIMESTAMP,
  status            TEXT    NOT NULL CHECK (status IN ('running', 'success', 'failed', 'dead_letter')),
  retry_count       INTEGER NOT NULL DEFAULT 0,
  last_error        TEXT,
  PRIMARY KEY (household_id, id),
  FOREIGN KEY (household_id, scheduled_job_id)
    REFERENCES scheduled_jobs(household_id, id) ON DELETE CASCADE
);
CREATE INDEX ix_scheduled_job_runs_job
  ON scheduled_job_runs(household_id, scheduled_job_id);
```

### Module layout (`tulip-storage`)

```
tulip_storage/runner/
  __init__.py           — exports Runner, Clock, ScheduledJob (the model)
  clock.py              — Clock type alias + default
  rrule.py              — thin wrapper around dateutil.rrule.rrulestr
  runner.py             — Runner class (the four-method surface + the loop)
  handlers/             — first-party handlers (P4.3.b adds envelope_refill)
```

Repositories (`tulip_storage.repositories`):
- `ScheduledJobRepository` — `create`, `get`, `due`, `cancel`, `update_next_run_at`.
- `ScheduledJobRunRepository` — `record_start`, `record_completion`, `last_for_job`.

### FastAPI lifespan integration

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from tulip_storage.runner import Runner

def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runner = Runner(...)
        app.state.runner = runner
        await runner.start()
        try:
            yield
        finally:
            await runner.stop()
    return FastAPI(..., lifespan=lifespan)
```

### Architecture test

`tests/test_architecture_scheduled_job_writes.py` — AST scan rejects imports of `tulip_storage.models.ScheduledJob` outside `tulip_storage.runner.*` and the model module itself. Mirrors P4.0's `test_architecture_no_direct_shadow_writes.py`.

## References

- [ARCHITECTURE.md §5.7](../ARCHITECTURE.md) — scheduled-tx runner spec.
- [ARCHITECTURE.md §4.1](../ARCHITECTURE.md) — `scheduled_transactions` schema sketch (reconciled here).
- [ADR-0001](0001-envelope-shadow-ledger.md) — the shadow-ledger model that P4.3.b's first consumer materializes.
- [docs/THREAT_MODEL.md §5.1](../THREAT_MODEL.md) — Phase 4 constraints (no eval; tenant scoping).
- [Issue #7](https://github.com/rmwarriner/tulip-accounting/issues/7) — scheduler primitive ADR (closed by P4.3.a).
- [Issue #68](https://github.com/rmwarriner/tulip-accounting/issues/68) — P4.3.a runner primitive (this ADR).
- [Issue #69](https://github.com/rmwarriner/tulip-accounting/issues/69) — P4.3.b refill engine (first consumer).
- [Issue #70](https://github.com/rmwarriner/tulip-accounting/issues/70) — P4.3.c API + CLI surface.

## Decision log

| Date | Decision | By |
|---|---|---|
| 2026-05-02 | Proposed: simple async loop over apscheduler / rq / celery / cron. | P4.3 planning |
| 2026-05-02 | Decided: two tables (`scheduled_jobs` + `scheduled_job_runs`), distinct from `audit_log`. | P4.3 planning |
| 2026-05-02 | Decided: `scheduled_jobs` is the literal generic table; §4.1's `scheduled_transactions` becomes a documentation artifact. | P4.3 planning |
| 2026-05-02 | Decided: RRULE via `python-dateutil`; new dep accepted. | P4.3 planning |
| 2026-05-02 | Decided: `Clock` injection; no `freezegun`. | P4.3 planning |
| 2026-05-02 | Decided: 3 retries with 1m / 5m / 30m backoff, then `dead_letter` + recurring job paused. | P4.3 planning |
| 2026-05-02 | Decided: single-worker assumption for v1. Multi-worker safety = Phase 9. | P4.3 planning |
| 2026-05-02 | Accepted on P4.3.a merge (#68). Closes #7. | P4.3.a implementation |

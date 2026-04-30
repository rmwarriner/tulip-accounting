# Tulip Accounting — Phase Status

Single source of truth for what's shipped, what's in flight, and what's queued. The phase definitions live in [ARCHITECTURE.md §10](ARCHITECTURE.md); this file just tracks the state.

**Last updated:** 2026-04-29 · `main` @ `a89cc89`

---

## Current state

- **Phase 0:** ✅ complete
- **Phase 1:** ✅ complete
- **Phase 2 (core API surface):** ✅ complete
- **Phase 2.x (cleanup before Phase 3):** queued — three slices, ordered

**Tests:** 215 passing · **coverage:** 95% project, ≥95% on `tulip-core` and `tulip-storage` · **CI:** green on `main`

---

## Phase 0 — Project bootstrap ✅

Per [PHASE_0_CHECKLIST.md](PHASE_0_CHECKLIST.md). Completed 2026-04-29.

- [x] uv workspace skeleton; seven packages with stub `pyproject.toml`
- [x] CI pipeline (lint, type-check, test, secrets-scan)
- [x] Pre-commit hooks (ruff, gitleaks, hygiene)
- [x] Tooling configuration (ruff, mypy --strict, pytest, coverage)
- [x] `tulip-core`: `Money`, `Currency`, `Account` value objects with property-based tests for `Money` arithmetic invariants
- [x] Architecture-test scaffolding (placeholder, replaced in P1.7 with the real boundary check)

---

## Phase 1 — Storage + accounting engine ✅

| Slice | What landed |
|---|---|
| **P1.1** | `Money.quantize_to_currency` (banker's rounding to minor units, idempotent, sign-preserving for ≥1 unit) |
| **P1.2** | `Posting`, `Transaction` (balance invariant for POSTED/RECONCILED), `Period` (open / soft-closed) |
| **P1.3** | Accounting engine: `post_transaction` (typed errors, period validation), `balance_with_fx_postings` |
| **P1.4** | SQLAlchemy 2.0 models (`Household`, `User`, `Account`, `Period`, `Transaction`, `Posting`, `AuditLog`) with composite (`household_id`, `id`) PKs and FKs |
| **P1.5** | Alembic + initial migration with four SQLite triggers enforcing the balance invariant on every status transition / posting mutation |
| **P1.6** | Field-level AES-256-GCM encryption helpers (`encrypt_field`, `decrypt_field`, `derive_master_key`) — single-key for v1; per-field DEK wrapping deferred |
| **P1.7** | Real architecture-boundary test (AST scan of `tulip-core/src/`) + end-to-end multi-currency wiring test |

### Phase 1 deferred items

- **SQLCipher full-DB encryption** (Layer 1 in ARCHITECTURE.md §7.4) — field-level (Layer 2) is wired; SQLCipher requires a native sqlcipher driver and will land via a separate engine factory. Tracked separately; not blocking.
- **Per-field DEK wrapping** — current `encrypt_field` uses the master key directly. The API is stable across the future change.
- **Tenant-scoping query event listener** — Phase 1 enforces tenancy via composite FKs (cross-tenant writes impossible at the schema level) and via repositories that always require a `household_id`. The architecture also calls for a SQLAlchemy event listener that auto-filters reads with an `admin_scope()` escape hatch; not yet implemented.

---

## Phase 2 — API surface (auth + accounts + transactions) ✅

| Slice | What landed |
|---|---|
| **P2.0** | `tulip-api` deps + `create_app()` factory + `/health` + `/openapi.json` + `/v1` prefix discipline |
| **P2.1** | `structlog` JSON logging + `RequestIdMiddleware` + PII-redaction processor (whitelist-based) |
| **P2.2** | Repositories (`AccountRepository`, `PeriodRepository`, `TransactionRepository`) + `AuditLogWriter` |
| **P2.3** | Auth: argon2id passwords, JWT access (15m) + opaque refresh (30d hashed), `Session` table + migration #2, `/v1/auth/{register,login,refresh,logout}` |
| **P2.5** | `/v1/accounts` CRUD with role + visibility enforcement (admin / member / viewer; shared / private) |
| **P2.6** | `/v1/transactions` CRUD routing through `post_transaction` with audit log on every mutation |

### Phase 2 deferred to Phase 2.x (see below)

- **P2.4 — MFA (TOTP)**
- **P2.7 — schemathesis contract tests in CI**
- **RFC 9457 Problem Details migration** (added as a project principle; see [ARCHITECTURE.md §7.8](ARCHITECTURE.md))

### Phase 2 carry-over notes

- API endpoints currently emit FastAPI's default `HTTPException(detail=str)` shape rather than RFC 9457 `application/problem+json`. The migration is **P2.x.2**.
- Audit-log writes are per-route at the moment; a unit-of-work pattern that auto-emits audit rows on flush would tighten coverage and avoid "did the developer remember" failure modes. Tracked for Phase 3+.
- Rate limiting via `slowapi` is installed as a dependency but not yet wired.
- OpenTelemetry hooks installed, off by default.

---

## Phase 2.x — Queued (in this order)

These slices are between core Phase 2 and the start of Phase 3 (CLI). They're sequenced so each one builds on the last.

### P2.x.1 — MFA (TOTP) — *in flight*

TOTP enrollment endpoint, login challenge gate, hashed recovery codes. The `User.totp_secret_encrypted` and `Household.mfa_policy` fields are already in the schema. New endpoints are RFC 9457-compliant from day 1 (the `auth.mfa_required` problem-details code is documented in §7.8.7).

Sub-slices:

- **P2.x.1.a — Enrollment + verification — ✅** *(2026-04-30)*
  - `Settings.master_key` wired in (`TULIP_MASTER_KEY` env, base64-32-bytes; ephemeral fallback warns).
  - `users.totp_enrolled_at` column + migration; distinguishes "secret stored, awaiting verify" from "verified."
  - **Minimum Problem Details infrastructure** landed alongside (`tulip_api.errors.TulipProblem`, `install_problem_handlers`, `_problem_details.assert_problem`). MFA error paths use it; legacy endpoints still emit plain `HTTPException` until P2.x.2 migrates them onto the same registry.
  - `tulip_api.auth.mfa` service: `pyotp` wrappers + AES-256-GCM encrypt/decrypt of stored secrets.
  - `POST /v1/auth/mfa/enroll` (rotates if not yet verified; 409 `auth.mfa_already_enrolled` after).
  - `POST /v1/auth/mfa/verify` (400 `auth.mfa_not_pending`, 401 `auth.mfa_invalid_code`, 409 `auth.mfa_already_enrolled`; 204 on success).
  - Audit log written on every state-changing path.
- **P2.x.1.b — Login challenge gate** *(queued)* — `/v1/auth/login` returns `auth.mfa_required` Problem Details when caller is TOTP-enrolled; new `POST /v1/auth/login/mfa` completes the flow with the code. Enforces `Household.mfa_policy` for admins.
- **P2.x.1.c — Recovery codes** *(queued)* — generate-on-enroll, hashed at rest (argon2id), one-time use; `POST /v1/auth/mfa/recover`.

### P2.x.2 — RFC 9457 Problem Details migration — *blocked by P2.x.1*

Replaces `HTTPException(detail=str)` with `application/problem+json` per [ARCHITECTURE.md §7.8](ARCHITECTURE.md). Concrete tasks:

1. Typed-exception base + per-error registry mapping `code` → `title` + `detail` template.
2. Global FastAPI exception handler that renders Problem Details and stamps `request_id`.
3. `assert_problem(resp, code=..., status=...)` test helper.
4. Migrate every existing endpoint test to `assert_problem`.
5. `/.well-known/errors/{code}` stub pages with the canonical explanation.
6. Architecture test forbidding ad-hoc `HTTPException(detail=str)` outside the wrapper.

### P2.x.3 — schemathesis contract tests in CI — *blocked by P2.x.2*

Drives the OpenAPI spec against a running app and asserts every documented error path returns the declared Problem Details schema. Lands as part of Phase 2.x cleanup before Phase 3.

---

## Phase 3 — CLI (Typer) + first useful flows — not started

Per ARCHITECTURE.md §10. Picks up after Phase 2.x is complete.

- `tulip auth login`, `tulip add`, `tulip register`, `tulip balance`, `tulip accounts`
- Token storage via `keyring`
- End-to-end tests of CLI against a spawned API server
- Toner-friendly print stylesheet finalized

---

## Reference: full phase roadmap

See [ARCHITECTURE.md §10](ARCHITECTURE.md). Phases 4 through 9 (envelopes, importers, AI, reports, ops, pre-cloud) are not in flight.

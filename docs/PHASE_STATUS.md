# Tulip Accounting — Phase Status

Single source of truth for what's shipped, what's in flight, and what's queued. The phase definitions live in [ARCHITECTURE.md §10](ARCHITECTURE.md); this file just tracks the state.

**Last updated:** 2026-04-29 · `main` @ `a89cc89`

---

## Current state

- **Phase 0:** ✅ complete
- **Phase 1:** ✅ complete
- **Phase 2 (core API surface):** ✅ complete
- **Phase 2.x (cleanup before Phase 3):** queued — three slices, ordered

**Tests:** 268 passing · **coverage:** 95% project, ≥95% on `tulip-core` and `tulip-storage` · **CI:** green on `main`

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

### P2.x.1 — MFA (TOTP) — ✅ *(2026-04-30)*

TOTP enrollment endpoint, login challenge gate, hashed recovery codes. `User.totp_secret_encrypted` was in the initial schema; `User.totp_enrolled_at` landed in slice (a); `Household.mfa_policy` landed in slice (b). New endpoints are RFC 9457-compliant from day 1 (the `auth.mfa_required` problem-details code is documented in §7.8.7).

Sub-slices:

- **P2.x.1.a — Enrollment + verification — ✅** *(2026-04-30)*
  - `Settings.master_key` wired in (`TULIP_MASTER_KEY` env, base64-32-bytes; ephemeral fallback warns).
  - `users.totp_enrolled_at` column + migration; distinguishes "secret stored, awaiting verify" from "verified."
  - **Minimum Problem Details infrastructure** landed alongside (`tulip_api.errors.TulipProblem`, `install_problem_handlers`, `_problem_details.assert_problem`). MFA error paths use it; legacy endpoints still emit plain `HTTPException` until P2.x.2 migrates them onto the same registry.
  - `tulip_api.auth.mfa` service: `pyotp` wrappers + AES-256-GCM encrypt/decrypt of stored secrets.
  - `POST /v1/auth/mfa/enroll` (rotates if not yet verified; 409 `auth.mfa_already_enrolled` after).
  - `POST /v1/auth/mfa/verify` (400 `auth.mfa_not_pending`, 401 `auth.mfa_invalid_code`, 409 `auth.mfa_already_enrolled`; 200 + recovery codes on success — body shape changed in slice c).
  - Audit log written on every state-changing path.
- **P2.x.1.b — Login challenge gate — ✅** *(2026-04-30)*
  - `households.mfa_policy` column + migration (default `optional`); enum values `optional | required_for_admins | required_for_all`.
  - Stateless MFA-challenge JWT (`purpose: mfa_challenge`, 5-min TTL) via `create_mfa_challenge_token` / `verify_mfa_challenge_token` in `tulip_api.auth.tokens` — same `jwt_secret`, no new state.
  - `POST /v1/auth/login` outcomes: wrong creds → 401 plain (unchanged, deliberately doesn't leak enrollment); enrolled → 401 `auth.mfa_required` with flat top-level `mfa_token` + `mfa_token_expires_in`; unenrolled when policy forces it → 403 `auth.mfa_enrollment_required` with `enrollment_url` extension.
  - New `POST /v1/auth/login/mfa` accepts `{mfa_token, code}`, verifies both, issues access + refresh tokens; access tokens or wrong-purpose JWTs are rejected.
  - Audit row `login_mfa_success` written on success; failed step-2 attempts are app-log only (matches existing failed-login policy).
- **P2.x.1.c — Recovery codes — ✅** *(2026-04-30)*
  - `mfa_recovery_codes` table + migration; argon2id-hashed, one row per code, `used_at` marks consumption (rows preserved for audit).
  - 8 codes minted at `/v1/auth/mfa/verify` and returned **once** as plaintext in the body (response changed from 204 → 200 with `{recovery_codes: [...]}`); format `XXXX-XXXX` from RFC 4648 base32 (40 bits/code). Input normalization tolerates lowercase / missing-dash transcription.
  - `POST /v1/auth/login/recover` — step-2 alternative to `/login/mfa`. Verifies the same `mfa_token`, redeems an unused code (single-use), audit-logs `mfa.recovery_login`, issues tokens. MFA stays enrolled — using a recovery code only consumes that one code.
  - `POST /v1/auth/mfa/recovery-codes/regenerate` — invalidates all existing codes, mints 8 fresh ones. **MFA-fresh** gate: requires both an access token *and* a current TOTP code in the body, so a stale stolen access token cannot silently swap codes.
  - `GET /v1/auth/mfa/recovery-codes/status` — returns `{remaining, total}`. Never returns the codes themselves.
  - Audit actions: `mfa.recovery_codes_generated`, `mfa.recovery_login`, `mfa.recovery_codes_regenerated`. Failed redemptions are app-log only.
  - New error code: `auth.mfa_invalid_recovery_code` (401) — distinct from `auth.mfa_invalid_code` so clients can tell apart "wrong TOTP" from "wrong/used recovery code."

P2.x.1 now closes **P2.x.2 (Problem Details migration)** as the next slice.

### P2.x.2 — RFC 9457 Problem Details migration — ✅ *(2026-04-30)*

Replaces `HTTPException(detail=str)` with `application/problem+json` per [ARCHITECTURE.md §7.8](ARCHITECTURE.md). Items 1-3 of the original sub-task list landed early as part of P2.x.1.a (the minimum infrastructure needed by MFA's new endpoints). What's left:

Sub-slices:

- **P2.x.2.a — Auth-domain migration — ✅** *(2026-04-30)*
  - `TulipProblem` extended with optional `headers` field (RFC 7235 `WWW-Authenticate: Bearer` is the first user; framework now passes them through to the rendered response).
  - New error subclasses: `UnauthorizedError` (`auth.unauthorized`, 401), `ForbiddenError` (`auth.forbidden`, 403), `InvalidCredentialsError` (`auth.invalid_credentials`, 401), `DuplicateEmailError` (`auth.duplicate_email`, 409), `InvalidRefreshTokenError` (`auth.invalid_refresh_token`, 401), `InvalidMfaTokenError` (`auth.invalid_mfa_token`, 401), `MfaNotEnrolledError` (`auth.mfa_not_enrolled`, 401).
  - All ~15 legacy `HTTPException` sites in `routers/auth.py` and `auth/deps.py` migrated. `get_current_claims` now emits `auth.unauthorized` Problem Details with `WWW-Authenticate: Bearer`. Existing auth tests upgraded to `assert_problem`.
  - Login wrong-password path emits `auth.invalid_credentials` for both unknown-email and wrong-password — body identical, no oracle.
- **P2.x.2.b + P2.x.2.c — Domain endpoints + polish — ✅** *(2026-04-30, combined PR)*
  - All 9 remaining `HTTPException` sites in `routers/accounts.py` and `routers/transactions.py` migrated. New codes (per §7.8 spec where pre-specified): `account.not_found`, `account.unknown`, `transaction.invalid`, `transaction.unbalanced`, `transaction.not_found`, `period.closed`. Edit-forbidden case reuses `auth.forbidden` rather than adding a new code (same client behavior, specifics in `detail`).
  - **Architecture test** (`tests/test_architecture_no_http_exception.py`): AST scan over `tulip_api/src/` asserts zero `HTTPException` references — import, raise, attribute access, all caught. No allowlist. Re-introducing the legacy pattern is now a CI failure.
  - **`/.well-known/errors/`** index + **`/.well-known/errors/{code}`** per-code HTML pages, rendered dynamically from `TulipProblem.__subclasses__()`. Adding a new error class auto-publishes a docs page (or, for classes needing constructor args, a one-line entry in `_PLACEHOLDER_ARGS`).
  - Existing `accounts` / `transactions` endpoint tests upgraded to `assert_problem`.

P2.x.2 closes; P2.x.3 (schemathesis contract tests) is unblocked next.

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

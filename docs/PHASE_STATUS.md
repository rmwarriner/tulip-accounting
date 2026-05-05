# Tulip Accounting — Phase Status

Single source of truth for what's shipped, what's in flight, and what's queued. The phase definitions live in [ARCHITECTURE.md §10](ARCHITECTURE.md); this file just tracks the state.

**Last updated:** 2026-05-05 · `main` @ **P5.2.b in flight** (QIF importer; reuses the P5.2.a API + CLI surface)

---

## Current state

- **Phase 0:** ✅ complete
- **Phase 1:** ✅ complete
- **Phase 2 (core API surface):** ✅ complete
- **Phase 2.x (cleanup before Phase 3):** ✅ complete (P2.x.1 – P2.x.4)
- **Phase 3 (CLI):** ✅ complete — P3.1 through P3.4 + P3.6 shipped; P3.5 (toner-friendly print stylesheet) deferred to Phase 8 alongside the actual reports (#22)
- **Post-Phase-3 enhancements:** balance + trial-balance endpoints (#31), account nesting end-to-end (#42), interactive `tulip add --edit` (#43)
- **Pre-Phase-4 docs:** threat-model checkpoint shipped (#56, [docs/THREAT_MODEL.md](THREAT_MODEL.md)). Transaction void / PENDING-only edit (#55) deliberately deferred to Phase 5 alongside reconciliation. Deep security/privacy audits deliberately deferred — see [ARCHITECTURE.md §10 audit cadence](ARCHITECTURE.md) (privacy: pre-Phase 6; deep security: Phase 8; pre-cloud re-audit: Phase 9).
- **Phase 4 (envelopes + sinking funds):** ✅ **complete** — all seven slices merged 2026-05-02. P4.0 (#60), P4.1.a (#62), P4.1.b (#63), P4.2 (#66), P4.3.a (#68 — closes #7 via [ADR-0002](adrs/0002-scheduler-primitive.md)), P4.3.b (#69), P4.3.c (#70).
- **Phase 5 (importers + reconciliation):** in flight — P5.0 (#55), P5.1 (storage layer), P5.2.a (OFX), and P5.2.b (QIF) implemented per [ADR-0004](adrs/0004-reconciliation.md). Next: P5.2.c (CSV importer + per-household profiles).

**Tests:** 917 passing · **CI:** green on `main`

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

### P2.x.3 — schemathesis contract tests in CI — ✅ *(2026-04-30)*

- Schemathesis relocated from a never-installed `tulip-api`-local `dependency-groups.test` to the root `dependency-groups.dev` so `uv sync --all-packages` actually installs it.
- New `ProblemDetailsResponse` Pydantic model in `errors.py` provides the OpenAPI schema for every error body. `problem_response("code1", "code2")` helper builds operation `responses=` entries.
- Every operation in `routers/{auth,accounts,transactions}.py` now declares its full set of error responses (401/403/404/409 + 400/422 framework paths).
- `RequestValidationError` (FastAPI 422) and Starlette `HTTPException` (framework-level 400 malformed body, 404 no route, 405 wrong method) are now wrapped to RFC 9457 — every non-2xx response is `application/problem+json`. New codes: `validation.failed`, `request.body_invalid`, `request.not_found`, `request.method_not_allowed`, `request.unsupported_media_type`.
- `tests/test_openapi_contract.py` uses `schemathesis.openapi.from_asgi()` and `@schema.parametrize()` to fuzz every documented operation with 25 examples each (override via `HYPOTHESIS_PROFILE=thorough` for 200). Asserts status codes are in declared sets and bodies conform to declared schemas.
- **Real bug caught and fixed**: `/v1/auth/login` used `scalar_one_or_none()` against email, but the schema allows the same email across multiple households. Schemathesis fuzzed two registers with the same email and tripped `MultipleResultsFound`. Login now authenticates against all candidates with the email and picks the one whose password verifies.
- Architecture test (`test_architecture_no_http_exception`) exempts `errors.py` since that's where the legitimate `HTTPException` wrapper handler lives.

P2.x is now fully complete; the next slice is **Phase 3 — CLI (Typer) + first useful flows**.

### P2.x.4 — catch-all unhandled-exception handler — ✅ *(2026-05-01)*

Closes #26. Surfaced during P3.2.a smoke testing when a SQLAlchemy URL parse error escaped the Problem Details middleware and emitted Starlette's default `text/plain` 500.

- New `InternalServerError` (`server.internal_error`, 500) `TulipProblem` subclass — generic detail, no exception text or traceback in the body (per ARCHITECTURE.md §1.1.7 / §7.8.6).
- `install_problem_handlers` registers a fourth handler for the `Exception` base class. Starlette dispatches by MRO so the existing `TulipProblem` / `RequestValidationError` / `StarletteHTTPException` handlers still win for their specific types; the catch-all only fires for genuinely-unhandled exceptions.
- Logs the full exception (with traceback, via `structlog.exception(exc_info=...)`) under the request's structlog context — operators have the detail in logs, clients don't.
- 5 new tests against a deliberate-panic FastAPI app: 500 problem+json shape, no leak of exception text / class name / traceback in the body, all exception types caught (RuntimeError, ValueError, KeyError), client-supplied `X-Request-Id` echoed, typed `TulipProblem` handler still wins over the catch-all.

---

## Phase 3 — CLI (Typer) + first useful flows — in flight

Per ARCHITECTURE.md §10. Phase 2.x cleared; this phase ships the CLI in five sequenced slices tracked as GitHub issues.

### P3.1 — CLI skeleton — ✅ *(2026-05-01)*

Issue #18. Foundation slice — no domain commands yet, just plumbing every later slice depends on.

- `tulip-cli` package wired with `typer`, `httpx`, `rich` deps and a `tulip` console script (`[project.scripts]` → `tulip_cli.main:app`).
- `tulip_cli.config`: TOML loader at `~/.config/tulip/config.toml` (XDG-aware), with precedence CLI flag > `TULIP_API_URL` > config file > default. `Config` dataclass strips trailing slashes.
- `tulip_cli.errors`: RFC 9457 Problem Details renderer. Bold-red title + indented detail to stderr; `--json` emits the raw body to stdout. Exit-code map per ARCHITECTURE.md §7.8.5 (`0`/`1`/`2`/`3`/`4`/`5`). Synthesizes a Problem Details body for non-`application/problem+json` failures so output stays consistent.
- `tulip_cli.http`: `TulipClient` thin wrapper over `httpx.Client`; raises `CliError` on both 4xx/5xx and network failures. Bearer-token slot ready for P3.2.
- `tulip ping` exercises the full path against `/health`. Without a server: exit `4`, network problem rendered. With `--json`: raw Problem Details body.
- Architecture test (`test_architecture.py`): AST scan over `tulip_cli/src/` rejects imports of `tulip_api`, `tulip_storage`, `sqlalchemy`, FastAPI, etc. — keeps the CLI a pure network client.
- 17 new tests; project total now 304 passing.

### P3.2 — Auth (`register`, `login`, `logout`, `status`) — in flight (#19)

Split into two PRs against the same umbrella issue.

#### P3.2.a — `register` command + spawn-uvicorn E2E fixture — ✅ *(2026-05-01)*

- New `live_api` pytest fixture in `packages/tulip-cli/tests/conftest.py` migrates a fresh SQLite DB, spawns `uvicorn` against `tulip_api.main:create_app` on an ephemeral port, polls `/health`, and tears the subprocess down on test exit. Per-test scope; ~1s overhead is fine at the current test count.
- `tulip register` (in `tulip_cli.commands.register`) prompts for email / display name / household / password (with confirmation) and `POST`s `/v1/auth/register`. `--password-stdin` skips the prompt for scripts and CI.
- 5 new E2E tests cover happy path, short-password validation failure (`validation.failed` 422), `--json` success body, `--json` problem body on validation failure, and the documented per-household uniqueness contract (same email across two households both succeed).
- Note: `auth.duplicate_email` (409) is unreachable through `register` alone because each call mints a new household, so `(household_id, email)` is unique by construction. Rejection coverage for that code will land when there's an "invite user to existing household" endpoint.
- Project test count: 314 passing.

#### P3.2.b — `login` (with MFA + recovery), `logout`, `status`, transparent refresh — ✅ *(2026-05-01)*

Closes #19.

- `tulip_cli.auth.tokens` — `TokenSet` dataclass + `TokenStore` with two backends. Default writes to the OS keyring (`tulip-accounting` service); setting `TULIP_TOKEN_STORE` to a path switches to a JSON-file backend. The file backend is used in tests and CI; real users get keyring. Pluggable backends (1Password CLI, `pass`) tracked separately as #28.
- `tulip_cli.auth.jwt_decode` — stdlib-only base64 decode of the JWT payload, no signature verification. Used by `auth status`; the next real call validates against the API.
- `TulipClient` learns pre-emptive transparent refresh: authenticated requests check the access-token expiry locally and call `POST /v1/auth/refresh` if within 30s of expiry. Refresh failure clears tokens and surfaces an `auth.session_expired` problem (exit `2`). Reactive (refresh on 401) was rejected because the API doesn't expose a distinct `auth.token_expired` code from a generic 401.
- `tulip auth login` — handles all three documented outcomes: 200 → tokens stored; 401 `auth.mfa_required` → prompt for TOTP code → `POST /v1/auth/login/mfa`; 403 `auth.mfa_enrollment_required` → render the `enrollment_url` and exit `2`. `--recovery` switches the step-2 prompt to a recovery code and POSTs to `/v1/auth/login/recover`. `--password-stdin` / `--code-stdin` for scripts.
- `tulip auth logout` — revokes the refresh token at the API and clears local tokens. Idempotent: already-logged-out is exit `0`.
- `tulip auth status` — reads tokens locally and decodes the access-token payload to display email, household_id, role, and access-token TTL. No network call; full server-side validation lands behind a `--check` flag once #24 (`GET /v1/auth/me`) ships.
- 35 new tests (token store round-trip with both backends, JWT decode, transparent refresh via `httpx.MockTransport`, plus 9 E2E covering happy login, MFA-TOTP, MFA-recovery, wrong-password exit code, status logged-in/out, JSON status, logout, idempotent logout). Project test count: 350 passing.

### P3.3 — Read flows (`accounts`, `balance`) — in flight (#20)

#### P3.3.a — `tulip accounts list` + `show` — ✅ *(2026-05-01)*

- New `tulip_cli.commands.accounts` registers an `accounts` Typer subcommand group.
- `tulip accounts list` consumes `GET /v1/accounts` (authenticated). Renders a Rich table for humans (`code`/`name`/`type`/`currency`/`visibility`); `--json` passes through the raw array. Empty households get a "no accounts yet" hint pointing at the (yet-to-land) `add` command.
- `tulip accounts show ACCOUNT` resolves the identifier as a UUID first, falling back to a `code` lookup over the listed accounts. `code` has no server-side uniqueness constraint, so multiple matches surface a CLI-side `account.ambiguous_code` problem (exit 1) rather than silently picking one.
- First slice that exercises the authenticated request path → `TulipClient.request(authenticated=True)` → transparent refresh from P3.2.b. Logged-out invocations cleanly surface `auth.not_logged_in` with exit 2.
- 8 new E2E tests: empty list, multi-account table, `--json` array, `show` by code, `show` by UUID, unknown code → user error, ambiguous code → user error, unauthenticated → exit 2. Project test count: 363 passing.

#### P3.3.b — `tulip balance` — ✅ *(2026-05-01)*

Closes P3.3 (#20). Consumes the balance endpoints landed by #31.

- New `tulip_cli.commands.balance` registers a top-level `tulip balance` command.
- **No argument** → `GET /v1/reports/trial-balance`. Renders a Rich table (`code`/`name`/`type`/`currency`/`balance`) plus per-currency totals (debits / credits / ✓ or ⚠ marker for the zero-sum check).
- **With `ACCOUNT`** (code or UUID) → `GET /v1/accounts/{id}/balance`. Reuses the UUID-or-code resolver from P3.3.a.
- `--as-of YYYY-MM-DD` flag passes through to the API for both shapes; client-side validation rejects malformed dates with `typer.BadParameter`.
- `--json` passes through the raw API body.
- 8 new E2E tests: empty trial balance, populated trial balance, JSON trial balance, single-account by code, single-account by UUID, as-of filtering, unknown code → user error, unauthenticated → exit 2. Project test count: 390 passing.

Also incidentally closes the `tulip-storage` `TrialBalanceRow` export gap surfaced during #31.

### P3.4 — Write flows (`accounts add`, `add`) — ✅ *(2026-05-01)*

Closes #21.

- `tulip accounts add` — `POST /v1/accounts`. Required: `--name`, `--type`, `--currency`. Optional: `--code`, `--subtype`, `--visibility` (default `shared`). Returns the created account body; `--json` passes through.
- `tulip add` (transactions) — `POST /v1/transactions`. Required: `--date YYYY-MM-DD`, `--description` (or `-m`), repeated `--post`. Optional: `--reference`.
- **Posting syntax: `--post account=amount[@CURRENCY]`** — picked over `account:amount` because account codes contain colons (e.g. `assets:checking`). The parser splits on the **last** `=` so codes-with-colons round-trip; `@CURRENCY` is optional and inherits from the account when omitted.
- New `tulip_cli.commands.transactions` exports `parse_posting()` + `ParsedPosting` so the parser is unit-testable independently of the API.
- 21 new tests: 10 parser unit tests (account+amount, negative, currency override, UUIDs, multiple-colon codes, malformed shapes); 5 E2E for `accounts add` (happy, minimal-no-code, --json, invalid type, unauthenticated); 6 E2E for `tulip add` (happy with balance round-trip, --json, unbalanced rejection, unknown-account-in-post, single-posting validation, unauthenticated).
- Project test count: 411 passing.

### P3.5 — Toner-friendly print stylesheet — deferred to Phase 8 (#22)

Phase 3 originally included a print-stylesheet skeleton, but with no reports to render against, the invariants are easy to drift out of sync. Recommended deferral until Phase 8 reports work; #22 stays open as the holding pen.

### P3.6 — CLI read+edit completeness — ✅ *(2026-05-01)*

Closes #54. Filled the "you can write but can't read your own data via CLI" gap before Phase 4 starts. Originally scoped as pure CLI work; the `GET /v1/transactions` endpoint had no filter params, so the slice grew a small repo + API extension as well.

- **Storage**: new `TransactionRepository.list_headers(account_id, from_date, to_date, status, limit)` replaces the inline SQL the API router used to do. Filters AND together; `account_id` matches any transaction with at least one posting on that account; date range is inclusive; ordered date desc, created_at desc.
- **API**: `GET /v1/transactions` learns `account_id` / `from` / `to` / `status` / `limit` query params. `status` is regex-validated to `pending|posted|reconciled`, `limit` is bounded to 1-1000 — both surface as `validation.failed` (422) on bad input. The router now delegates to `list_headers` rather than inlining its own query.
- **CLI**: new `tulip transactions list` (with `--account` code-or-UUID, `--from`, `--to`, `--status`, `--limit`, plus `--json` passthrough) and `tulip transactions show TXID` (header + posting table). New `tulip accounts edit ACCOUNT` — only sends explicitly-passed fields; supports `--name` / `--code` / `--subtype` / `--visibility` / `--parent` (the last one re-uses the parent-validation rules from #42.a). New `tulip accounts deactivate ACCOUNT` — confirmation prompt by default, `--yes` to skip.
- **HTTP**: `TulipClient` learned `patch()` and `delete()` for the new account flows.
- 36 new tests (5 storage, 7 API, 24 CLI E2E across all four commands incl. happy / filter combos / error paths / `--json` / unauthenticated). Project test count: 496 passing.

Transaction edit / delete / void deliberately not in scope — see #55, deferred to Phase 5 alongside reconciliation.

### Transaction void / PENDING-only edit — deferred to Phase 5 (#55)

The API has no PATCH or DELETE for transactions, and adding them naively is the wrong shape for double-entry accounting (POSTED transactions should void via reversal, not edit-in-place). The semantics are inseparable from the un-reconcile flow that lands in Phase 5, so this issue is the holding pen for the Phase 5 work — it captures the domain model decision (status enum vs `voided_by_transaction_id` link), the API surface (`PATCH` PENDING-only, `POST /v1/transactions/{id}/void`, `DELETE` PENDING-only), and the period / reconciled-source interaction rules.

---

## Post-Phase-3 enhancements

These weren't in the original Phase 3 plan but landed before Phase 4 work begins, and the CLI's full ergonomic loop (register → login → accounts add (with parent) → add (interactive or flag) → balance) needed all three to feel complete.

### Balance endpoints + `tulip balance` (#31, #20-b) — ✅ *(2026-05-01)*

- **API**: `TransactionRepository.balance_for_account` and `TransactionRepository.trial_balance` (POSTED + RECONCILED only; pending is workflow state). New schemas in `tulip_api.schemas.balance`. Two endpoints: `GET /v1/accounts/{id}/balance` and `GET /v1/reports/trial-balance` (new `routers/reports.py`). Both accept `?as_of=YYYY-MM-DD`. All Decimal balances are quantized to currency minor units via `Money.quantize_to_currency()`.
- **CLI**: `tulip balance` (no arg) renders trial balance with debit/credit zero-sum check; `tulip balance ACCOUNT` (code or UUID) shows a single account's balance.
- 23 new tests (8 storage, 10 API, 8 CLI). PRs #37 + #38.

### Account nesting end-to-end (#42) — ✅ *(2026-05-01)*

- **API** (#42.a, PR #45): On `POST` and `PATCH /v1/accounts`, parent_account_id is validated against four rules: `parent.type == child.type`, `parent.currency == child.currency`, no shared-child-under-private-parent, and no cycles (cycle walk on PATCH only). Five new error codes: `account.parent_not_found`, `account.parent_type_mismatch`, `account.parent_currency_mismatch`, `account.parent_visibility_violation`, `account.parent_cycle`. `AccountUpdate` learns `parent_account_id` so PATCH can reparent.
- **CLI** (#42.b, PR #47): `tulip accounts add --parent ACCOUNT` (code or UUID); `tulip accounts list` defaults to a Rich tree when nesting exists, falls back to flat table when it doesn't or under `--flat`; `tulip accounts show` resolves and displays the parent's code+name.
- Multi-currency parent hierarchies (USD-base household with EUR/GBP/JPY travel sub-accounts) are deliberately rejected; the design discussion lives in **#44** as a holding pen.
- 23 new tests (14 API, 9 CLI).

### Interactive `tulip add --edit` (#43) — ✅ *(2026-05-01)*

PR #48. Editor-driven transaction entry as an alternative to the flag mode.

- New `tulip_cli.commands._editor` spawns `$VISUAL` → `$EDITOR` → `vi`/`notepad` (with `shlex.split` so `EDITOR='code --wait'` works).
- New `tulip_cli.commands._ledger` parses a strict subset of hledger syntax: `YYYY-MM-DD <description>` header, indented `<account>  <amount> [<currency>]` postings, `#` and `;` comments. Errors carry line numbers.
- The editor loop reopens with a banner on parse / balance / unknown-account / period-closed errors so users fix their typing rather than re-typing from scratch (matches `git commit` ergonomics). Saving an empty/comments-only buffer aborts cleanly with exit `0`.
- 26 new tests: 15 parser unit, 5 editor-spawn unit, 6 E2E with a fake editor (happy + abort + 3 reopen-on-error + `--json`).

---

## Phase 4 — Envelopes + sinking funds — in flight

Per [ADR-0001](adrs/0001-envelope-shadow-ledger.md). Envelope and sinking-fund balances are tracked through a parallel double-entry **shadow ledger** whose accounts are `allocation_pools`. Spending against an envelope = a main-ledger posting carrying `pool_id`, which auto-pairs a shadow transaction at write time. Refills, transfers, rollovers, and ad-hoc allocations are user-initiated shadow transactions. Pool balances are derived from `sum(shadow_postings)` — never stored.

### P4.0 — Storage + domain layer — ✅ *(2026-05-02)*

Closes #60. Pure storage + domain slice; no API, no CLI, no refill execution. Migration + value objects + engine + repositories + system-pool auto-creation + architecture test.

- **Migration** (`a3f4d8e91b22`): new tables `allocation_pools`, `envelopes`, `sinking_funds`, `shadow_transactions`, `shadow_postings`. Four shadow-ledger balance triggers mirror the main-ledger triggers from migration 0001 — sum-to-zero per `(shadow_transaction_id, currency)` is enforced on transitions into `posted` and on `INSERT` / `UPDATE` / `DELETE` of postings while a shadow tx is `posted`. The long-deferred FK on `postings.pool_id → allocation_pools` lands here too (the column has existed since the initial schema as a nullable BLOB without referential integrity).
- **Domain types** (`tulip_core.allocation`): `Pool` (frozen value object, equality by id), `PoolType` (5 values: `envelope`, `sinking_fund`, `inflow`, `unallocated`, `spent`), `Envelope` (with `BudgetPeriod` and `RolloverPolicy`), `SinkingFund` (with `ContributionStrategy`), `ShadowPosting`, `ShadowTransaction` (sum-to-zero invariant on POSTED, multi-currency segregated), `ShadowTxReason` (6 values), `ShadowTxStatus` (`pending` / `posted` / `voided`), `RefillRule` value object (3 strategies, structured shape, JSON round-trip — no expression eval).
- **Engine** (`tulip_core.allocation.engine.post_shadow_transaction`): validates pool existence + tenant scope + active flag + currency match + balance, then promotes to POSTED. Idempotent on already-POSTED; rejects re-post of VOIDED. Period validation deliberately deferred to P4.1 — shadow tx record intent and the period gate is enforced where the main tx that triggered them lives.
- **Repositories** (`tulip-storage`): `AllocationPoolRepository` (CRUD + `get_or_create_system_pools(currency)` resolver, idempotent, system pools rejected from `deactivate`); `ShadowTransactionRepository.save_balanced` (PENDING-then-UPDATE-to-POSTED save flow, trigger fires on the UPDATE) + `balance_for_pool(pool_id, *, currency=None, as_of=None)` returning `{currency: net amount}`; pending and voided shadow txs excluded from balance sums.
- **System-pool auto-creation**: `Inflow` / `Unallocated` / `Spent` per `(household, currency)`, eagerly created on `POST /v1/auth/register` for the household's `base_currency`. The resolver is idempotent so lazy creation in P4.1's API layer (on first use of a new currency) Just Works.
- **Architecture test** (`test_architecture_no_direct_shadow_writes.py`): AST scan rejecting imports of the storage-layer `ShadowTransaction` / `ShadowPosting` model classes outside `repositories/shadow_transaction.py`. Domain-layer value objects of the same name are deliberately not banned — that's the type the rest of the codebase uses to *describe* a shadow tx before handing it to the repo.
- **Doc updates**: ARCHITECTURE.md §5.3 refill_rule JSON shape brought in line with the structured `RefillRule` value object (`fixed_amount` / `fill_to_amount` / `percentage_of_income`). ADR-0001 status flipped to Accepted.
- **Tests**: 84 new tests (49 core, 23 storage, 1 API). Project total: 580 passing.

### P4.1.a — Writer chokepoint (auto-pair shadow tx on pool_id postings) — ✅ *(2026-05-02)*

Closes #62. Extends `POST /v1/transactions`: when any posting carries `pool_id`, the handler atomically writes a paired shadow-ledger transaction in the same `session.commit()` per ADR-0001's pairing rule.

- **Schema**: `PostingCreate` learns optional `pool_id: UUID | None`; `PostingRead` and `TransactionRead` surface it (plus `paired_shadow_tx_id` on the response).
- **Pre-flight validation** (in this order, before any DB write): pool exists in household → pool active → account type permits pool-tagging (**EXPENSE only in v1**) → pool currency matches posting currency. Each maps to a typed Problem Details code: `pool.not_found`, `pool.inactive`, `pool.invalid_account_type_pairing`, `pool.currency_mismatch` (all 400). Cross-tenant pool refs surface as `pool.not_found`.
- **Lazy system-pool creation**: for each distinct currency among pool-tagged postings, the handler calls `AllocationPoolRepository.get_or_create_system_pools(currency=...)` before either ledger writes. Idempotent. No separate audit row — system pools are plumbing.
- **Auto-pairing engine**: new `tulip_core.allocation.engine.derive_paired_shadow_tx(main_tx, *, account_types_by_id, spent_pool_by_currency)` returning `ShadowTransaction | None`. Sign rule for v1: `EXPENSE → +1`. One absorbing leg in the household's `Spent` system pool of the appropriate currency. Multi-currency pool-tags within one main tx → `MultiCurrencyPoolTaggingError` (rejected in v1). Refund-shaped (positive net pool effect) → `UnsupportedRefundShapedShadowTxError` (rejected in v1; needs an ADR amendment to add a `REFUND` reason).
- **Atomic rollback**: existing handler boundary handles it — both `save_balanced` calls + the audit write share one session, one commit. If anything raises, FastAPI's request lifecycle rolls back the session via `get_session` and neither ledger persists.
- **Audit log**: extended the main tx's `after_snapshot` with `paired_shadow_tx_id` when present. One user action = one audit row; the shadow row is queryable via `paired_main_tx_id`.
- **GET / list endpoints** also surface `paired_shadow_tx_id` via a new `ShadowTransactionRepository.get_paired_id_for_main_tx`. Architecture test (P4.0) still bans direct shadow-table writes outside the repo.
- **New error codes**: `pool.not_found`, `pool.inactive`, `pool.currency_mismatch`, `pool.invalid_account_type_pairing` (400) and `pool.shadow_unbalanced` (500, defense in depth — only fires on a Tulip bug).
- **Tests**: 25 new (8 engine unit + 17 API integration). Project total: **605 passing** (up from 580).

### P4.1.b — Envelope / sinking-fund / refill / transfer / budget-inflow endpoints — ✅ *(2026-05-02)*

Closes #63. Three new routers (`/v1/envelopes`, `/v1/sinking-funds`, `/v1/pools`) sitting on top of P4.0's storage layer and P4.1.a's writer chokepoint.

- **Envelopes** (`/v1/envelopes`): CRUD (list / create / get / patch / delete) + `GET /{id}/balance` + `POST /{id}/refill`. Refill posts a 2-leg shadow transaction (`Unallocated -X` / envelope `+X`) with reason `REFILL`; lazy-creates the household's `Unallocated` system pool for the envelope's currency if missing. Permissive on Unallocated going negative — that's intent, not money. Visibility / role rules mirror accounts: shared visible to all; private visible only to creator + admins; member can't edit / refill private pools they didn't create.
- **Sinking funds** (`/v1/sinking-funds`): CRUD + `GET /{id}/balance`, mirror of envelopes. Field set: `target_amount`, `target_date`, `contribution_strategy` (`manual` / `even_split` / `percentage_of_income`), optional `contribution_amount`. Currency immutable.
- **Pools** (`/v1/pools`): `POST /{src}/transfer` and `POST /budget-inflow`. Transfer requires both pools active, same household, same currency, both **user pools** (system-pool source/dest rejected with `pool.transfer_system_pool_forbidden` carrying `extensions.role`). Budget-inflow declares "I have $X to budget", lazy-creates the household's three system pools for the currency if any are missing. Pre-flight rejects same-pool transfers (`pool.transfer_same_pool`), currency mismatches (`pool.transfer_currency_mismatch`), and unknown ISO codes (`pool.inflow_currency_unknown`).
- **Schemas**: shared `PoolBalanceRead` (envelopes + sinking-funds use it), structured `RefillRuleSchema` matching `RefillRule.to_dict()`. The router round-trips through `RefillRule.from_dict()` at the boundary so the no-eval guarantee holds — `refill_rule_json` storage is always written via `RefillRule.to_dict()`.
- **Repositories**: new `EnvelopeRepository` and `SinkingFundRepository` wrap the two-table inserts (`allocation_pools` + the joined detail row) behind a single `create / get / list_active / update_fields` interface. Soft-delete continues to go through `AllocationPoolRepository.deactivate`.
- **Helper module** `routers/_pool_helpers.py`: `filter_for_role`, `require_visibility_or_forbid`, `resolve_or_lazy_create_system_pool`, and `post_user_initiated_shadow_tx` — the last is the load-bearing one used by refill, transfer, and budget-inflow. Builds the domain `ShadowTransaction`, validates via the engine, persists via `ShadowTransactionRepository.save_balanced`, and writes one audit row with `entity_type="shadow_transaction"`.
- **Audit log**: every CRUD action (envelope / sinking_fund) writes `entity_type="envelope" | "sinking_fund"` rows; every refill / transfer / budget-inflow writes `entity_type="shadow_transaction"` with `reason` and `description` in `after_snapshot`. One user action = one audit row.
- **New error codes**: `envelope.not_found`, `sinking_fund.not_found`, `pool.transfer_same_pool`, `pool.transfer_currency_mismatch`, `pool.transfer_system_pool_forbidden`, `pool.inflow_currency_unknown`. Reuses `pool.not_found` / `pool.inactive` from P4.1.a.
- **No period gate** on user-initiated shadow tx (refill, transfer, budget-inflow) in v1 — these record intent, not money movement. Period enforcement remains exclusively on main-ledger writes.
- **Decimal-safe validation rendering**: fixed a latent bug where `RequestValidationError` with `Decimal` constraint contexts (`ge=0`, `gt=0`) couldn't serialize to JSON. Added `_sanitize_for_json` recursive coercion in the validation handler.
- **Tests**: 63 new (23 envelope endpoint tests, 13 sinking-fund, 27 pool). Project total: **668 passing** (up from 605).

### P4.2 — CLI commands for envelopes / sinking-funds / refill / transfer / budget-inflow — ✅ *(2026-05-02)*

Closes #66. Surfaces P4.1.b's API endpoints through the `tulip` CLI. Mirrors the patterns from `tulip accounts` (CRUD subgroups) and `tulip add` (top-level action commands).

- **`tulip envelopes`** subgroup: `list`, `show`, `add`, `edit`, `deactivate`. Resolver falls back to `name` (envelopes have no code field), with `envelope.ambiguous_name` raised on duplicates rather than silent first-match.
- **`tulip sinking-funds`** subgroup: same shape, mirror of envelopes minus refill. Field set: `--target-amount`, `--target-date`, `--contribution-strategy`, `--contribution-amount`.
- **Top-level action commands**: `tulip refill ENVELOPE`, `tulip transfer --from POOL --to POOL`, `tulip budget-inflow`. The transfer resolver looks across both envelope and sinking-fund lists; cross-type name collisions raise `pool.ambiguous_name`.
- **Output formats**: Rich tables for list (no per-row balance fetch — avoids N+1); key:value plus a separate `balance:` line for `show`; "Refilled X by AMT; new balance: BAL" / "Transferred AMT from SRC to DEST; new destination balance: BAL" / "Declared inflow of AMT CCY; new Unallocated balance: BAL" for actions. `--json` passes through the raw API response on every command.
- **Helper module** `commands/_pools.py`: `_resolve_envelope`, `_resolve_sinking_fund`, `_resolve_pool` plus typed Problem Details builders (`*.not_found`, `*.ambiguous_name`).
- **Refill-rule editing** intentionally not in P4.2; the structured-only constraint from ADR-0001 needs an editor flow that lands as a follow-up.
- **Tests**: 34 new E2E (15 envelope, 8 sinking-fund, 11 pool-action). Project total: **702 passing** (up from 668).

### P4.3.a — Scheduler runner primitive + ADR-0002 — ✅ *(2026-05-02)*

Closes #68 + #7. Implements the in-process scheduler that the rest of P4.3 (refill rules execution) sits on top of. Per [ADR-0002](adrs/0002-scheduler-primitive.md).

- **ADR-0002**: records 8 design decisions — simple async loop in FastAPI lifespan (rejected apscheduler / rq / celery / cron); 4-method runner surface (`register_handler`, `schedule_one`, `schedule_recurring`, `cancel`) with idempotency keys from day one; two tables (`scheduled_jobs` + `scheduled_job_runs`, distinct from `audit_log`); single generic `scheduled_jobs` reconciles the architecture's `scheduled_transactions` sketch with #7's proposal; RRULE via `python-dateutil`; `Clock` injection (no `freezegun`); 1m / 5m / 30m retry then dead-letter; **single-worker assumption** documented loudly for v1.
- **Schema**: new migration `b8a91c2f3d44` adding `scheduled_jobs` + `scheduled_job_runs`. `scheduled_jobs` has `dtstart` (RRULE anchor — needed so COUNT/UNTIL stays stable as `next_run_at` advances) + `next_run_at` (indexed) + `idempotency_key` (unique partial index per `(household_id, kind)`).
- **Code** (new `tulip_storage/runner/` module): `Runner` class with the 4-method surface + the async poll loop; `Clock` type alias + default; `compute_next_fire` wrapper around `dateutil.rrule.rrulestr`. ~250 LOC core.
- **FastAPI lifespan hook** in `create_app(enable_runner=True)` boots the runner on startup, drains it on shutdown. Tests pass `enable_runner=False` to skip the runner (their overridden `get_session` doesn't reach the runner's session factory).
- **Architecture test** (`test_architecture_no_direct_scheduled_job_writes`): only `tulip_storage.runner.runner` may import the storage-layer `ScheduledJob` model. Mirrors P4.0's shadow-table guard.
- **Tests**: 11 new in `tulip-storage` (TDD entry — schedule_one/run, schedule_recurring + advance, idempotency happy + cross-kind, cancel happy + unknown, retry + dead-letter, no-handler, RRULE-with-COUNT exhaustion, full async start/stop lifecycle). Project total: **713 passing** (up from 702).
- **New deps**: `python-dateutil>=2.9` + `types-python-dateutil>=2.9` (added to `tulip-storage`).

### P4.3.b — Refill-rule evaluation engine + envelope_refill handler — ✅ *(2026-05-02)*

Closes #69. Sits on top of P4.3.a's runner primitive and P4.1.b's API surface; consumes the storage layer P4.0 shipped.

- **Pure engine** (`tulip_core.allocation.evaluate_refill_rule`): given a `RefillRule`, the envelope's `current_balance`, and (for `PERCENTAGE_OF_INCOME`) the household's `recent_inflow`, returns the `Money` amount to contribute. Always non-negative; returns `Money.zero(...)` when the rule produces no contribution (e.g., `FILL_TO_AMOUNT` already at target, no recent inflow). Raises `CurrencyMismatchError` on cross-currency inputs.
- **`envelope_refill` runner handler** (`tulip_storage.runner.handlers.envelope_refill`): factory pattern — `make_envelope_refill_handler(session_maker)` returns the `(job, clock) -> None` callback that the runner registers. Handler loads envelope → lazy-creates `Unallocated` system pool for envelope's currency → computes `current_balance` via `ShadowTransactionRepository.balance_for_pool` → for `PERCENTAGE_OF_INCOME`, sums `BUDGET_INFLOW` shadow tx since `last_run_at` (or 30 days for first fire) → calls the engine → posts a 2-leg `REFILL` shadow tx if amount > 0 → writes audit row with `actor_kind="system"`.
- **Inactive envelope / no-rule envelope** are silent no-ops (don't error → don't retry; the schedule remains active in case the envelope reactivates).
- **Unknown envelope** raises `EnvelopeRefillError` → runner marks the run failed and retries per the backoff policy.
- **New repo method** `ShadowTransactionRepository.inflow_since(currency, since)` — sums positive `Unallocated` postings where `reason=BUDGET_INFLOW` and date ≥ since. Used by the handler; refactored out of the handler to keep the architecture-test boundary clean (handlers don't directly query shadow_transactions).
- **Architecture test** (P4.0's no-direct-shadow-writes) still passes — the handler routes all shadow access through the repo. The P4.3.a no-direct-scheduled-job-writes allowlist gets one new entry for the handler module (it has a TYPE_CHECKING-only `ScheduledJob` import for typing).
- **Tests**: 26 new (14 engine pure-function tests + 12 handler integration tests covering FIXED_AMOUNT happy path, FILL_TO_AMOUNT with gap and at-target, PERCENTAGE_OF_INCOME with and without inflow, no-rule no-op, inactive-envelope no-op, unknown-envelope error, payload-missing-key error, audit row shape with `actor_kind="system"`, recurring monthly schedule with two fires, full async start/stop pipeline). Project total: **739 passing** (up from 713).

### P4.3.c — API + CLI surface for refill schedules — ✅ *(2026-05-02)*

Closes #70. **Final Phase 4 slice.** Wires the user-facing surface for managing recurring refills on top of the runner primitive (P4.3.a) and the refill handler (P4.3.b).

- **API** — five endpoints in `routers/refill_schedules.py`:
  - `POST /v1/envelopes/{id}/refill-schedule` — register a recurring auto-refill. Body: `{rrule, start_at}`. Idempotency key = `str(envelope_id)`, so duplicates surface as `refill_schedule.already_exists` (409).
  - `GET /v1/envelopes/{id}/refill-schedule` — fetch the active schedule, 404 if none.
  - `DELETE /v1/envelopes/{id}/refill-schedule` — cancel (flips `is_active=false`).
  - `GET /v1/scheduled-jobs` — admin / ops view, cross-kind list of all active schedules in the household.
  - `POST /v1/scheduled-jobs/run-due` — admin: force a poll tick for testing + manual catch-up.
- **CLI** — new `tulip refills` subgroup: `schedule ENVELOPE --rrule … --start …`, `list` (Rich table + `--json`), `show ENVELOPE`, `cancel ENVELOPE [--yes]`, `run-due`. Mirrors the `tulip envelopes` resolver pattern (UUID-or-name, ambiguous-name detection).
- **New `ScheduledJobRepository`** (read-only) — household-scoped queries: `get`, `get_by_idempotency_key`, `list_active`, `list_runs`. Architecture-test allowlist updated to permit it (writes still route through the runner).
- **Runner dependency** — `get_runner(request)` resolves `app.state.runner`. The FastAPI lifespan hook attaches it in production; tests' conftest attaches a runner bound to the per-test session factory.
- **New error codes**: `refill_schedule.not_found` (404), `refill_schedule.envelope_has_no_refill_rule` (400), `refill_schedule.invalid_rrule` (400), `refill_schedule.already_exists` (409). RRULE strings are validated server-side via `python-dateutil`.
- **Pre-flight checks** at schedule creation: envelope exists + visible, envelope has a `refill_rule`, RRULE parses and yields at least one occurrence at-or-after `start_at`. All three before the runner-side write.
- **Tests**: 28 new (16 API + 12 CLI E2E). Coverage includes happy paths for every endpoint/verb, every error code, idempotency rejection, cancel-then-show round-trip, JSON passthrough, unauthenticated rejection, and the full `run-due` end-to-end pipeline (envelope → schedule → run-due → balance grew).
- **Project total**: **772 passing** (up from 739).

### Phase 4 follow-ups

- **P4 follow-up — `--edit` flow for `tulip envelopes add` / `edit`** so users can author `RefillRule` structures interactively. (No issue filed; deliberate deferral from P4.2.)

---

## Phase 5 — Importers + reconciliation — in flight (design)

Umbrella issue: #74. Per [ADR-0004](adrs/0004-reconciliation.md). Phase 5 adds statement importers (OFX, QIF, CSV) and reconciliation (manual `cleared` flag + statement-driven matcher). Reconciliation is modeled as a separate aggregate (`reconciliations` + `reconciliation_matches` tables) with denormalized join shortcuts on `transactions`. The matcher is a pure `tulip-core` function consuming a `StatementLine` common-denominator schema; the categorization seam is a `Categorizer` Protocol so Phase 6 plugs in without touching importer code. Carry-forward is explicit; CSV profiles live only in the DB (YAML is export / import format).

### P5 design — ADR-0004 — ✅ *(2026-05-04)*

Closes #101. Pre-code design doc — no implementation lands until reviewed. Settles the nine open questions: match candidates (account + exact amount + ±3 day window + not-already-reconciled); confidence buckets (`high` / `medium` / `low`, rule-based, `rapidfuzz`); partial / split matches via M:N `reconciliation_matches`; manual override on the same row with NULL confidence; unmatched inbox with three actions per side; idempotency on raw-file SHA-256; hybrid state model (separate `reconciliations` table is truth, denorm columns on `transactions`); `StatementLine` common-denominator dataclass in `tulip-core`; three-layer audit (audit_log + match provenance + encrypted file attachment).

### P5.0 — Transaction void / PENDING-only edit — ✅ *(2026-05-04)*

Closes #55. First Phase 5 implementation slice; prerequisite for every later slice's revert / un-reconcile / cleanup paths.

- **Migration** (`e7d2a4f8c1b9`): adds `transactions.voided_by_transaction_id` (composite self-FK with `use_alter=True`) + `voided_at` timestamp. Reuses the trigger drop-and-recreate dance from P4.0's `a3f4d8e91b22` because the main-ledger balance triggers reference `transactions` by name.
- **Domain layer**: `Transaction` value object learns optional `voided_by_transaction_id`; `__post_init__` rejects the impossible `PENDING + voided_by` state. New `tulip_core.accounting.build_reversal()` helper produces a sign-flipped PENDING reversal sibling; the API handler runs `post_transaction(reversal, periods)` to gate against open periods on the **reversal date**, not the source's date (the void's *own* date is what's checked, per ADR-0004).
- **Storage repos**: `TransactionRepository` learns `persist_reversal`, `update_pending`, `delete_pending` with typed exceptions (`TransactionAlreadyVoidedError`, `TransactionNotVoidableError`, `TransactionNotEditableError`, `TransactionNotDeletableError`). `ShadowTransactionRepository` learns a `void` chokepoint that flips status `posted → voided` (idempotent on already-voided; rejects PENDING).
- **API**: three new verbs on `/v1/transactions/{id}`. `POST /void` builds the reversal, persists it, and atomically auto-voids the paired shadow tx if any (option (c) — main-side reversal sibling, shadow-side status flip; balance auto-corrects since `balance_for_pool` already excludes voided shadow txs). `PATCH` and `DELETE` are PENDING-only; POSTED / RECONCILED return 409.
- **New error codes**: `transaction.not_editable` (409), `transaction.not_deletable` (409), `transaction.already_voided` (409, with `voided_by_transaction_id` extension), `transaction.not_voidable` (409, with `status` extension).
- **CLI**: new `tulip transactions {void, delete, edit}`. `void` takes `--reason` + optional `--date` + `--yes`; renders "Voided X; reversal posted as Y" and a one-liner when a paired shadow tx was auto-voided. `edit` reuses the `--edit` editor flow from #43, rendering the existing tx into hledger format and PATCH'ing on save.
- **Audit log**: one row per user action (`transaction_void`, `transaction_update`, `transaction_delete`); `paired_shadow_tx_id_voided` extends the void row's `after_snapshot` when applicable, mirroring P4.1.a's pattern.
- **Architecture test**: `test_architecture_no_direct_void_link_writes.py` AST scan rejects writes to `transactions.voided_by_transaction_id` outside `TransactionRepository.persist_reversal`, mirroring P4.0's shadow-write guard.
- **Tests**: 57 new (4 core, 16 storage, 15 API, 8 CLI E2E + arch tests). Project total: **829 passing** (up from 772).

### P5.1 — Schema + storage layer for imports + reconciliations — ✅ *(2026-05-05)*

Pure storage slice; no API verbs, no CLI. Migration `f4a6b9c2e7d3` adds 7 new tables and 5 nullable columns on `transactions`.

- **New tables**: `attachments`, `attachment_links`, `import_batches`, `statement_lines`, `reconciliations`, `reconciliation_matches`, `csv_profiles`. All use composite `(household_id, id)` PKs and composite FKs (the established P4.0 pattern).
- **New transactions columns**: `cleared_at`, `reconciled_at`, `reconciliation_id`, `imported_from_id`, `carried_forward_from_reconciliation_id`. Trigger drop-and-recreate dance reused from P5.0 because the main-ledger balance triggers reference `transactions` by name.
- **`reconciliation_matches → transactions` FK is `ON DELETE RESTRICT`** (not CASCADE) per ADR-0004 §Q3 — voiding a matched tx must fail loudly until the match is rejected.
- **AttachmentRepository is the first repo with filesystem I/O**: encrypts plaintext bytes via P1.6 `encrypt_field` and writes to `Settings.attachment_root` (defaults to `~/.local/share/tulip/attachments`; env override `TULIP_ATTACHMENT_ROOT`). New `Settings.attachment_root` field plumbed through the existing `Settings` dataclass.
- **`ReconciliationRepository.complete()` is the chokepoint** for `transactions.reconciled_at` + `transactions.reconciliation_id` per ADR-0004 §Q7. Architecture test `test_architecture_no_direct_reconciled_at_writes.py` enforces this; analogous to P5.0's void-link guard.
- **Three new architecture tests**: no direct P5.1 model writes outside repos, no direct reconciled_at writes outside chokepoint, no `tulip_ai` imports in (yet-to-exist) `tulip_importers` package.
- **`csv_profiles` stored DB-only** with YAML as export/import format (per the P5 design decision).
- **Tests**: 31 new (10 migration + 18 repository + 3 architecture). Project total: **860 passing** (up from 829).

### P5.2.a — OFX importer + first reconciliation domain types — ✅ *(2026-05-05)*

First Phase 5 slice that touches all four layers (core domain + new `tulip-importers` package + storage write-through + API + CLI). Closes the all-four-layers cohesion gap; P5.2.b (QIF) and P5.2.c (CSV) reuse the chokepoints landed here.

- **Core domain** (first `tulip_core.reconciliation` module): new `ParsedStatementLine` (parser output, no persistence ids) + `StatementLine` (persisted form, equality by id). Split keeps each value object with one writer and one reader; placeholder UUIDs in parser output become a type error rather than a runtime surprise. `raw` is `MappingProxyType[str, str]` — read-only after construction so the matcher can't mutate format-specific noise.
- **`tulip-importers` package** wired (was empty in P5.1). Depends on `tulip-core` (for `ParsedStatementLine`) + `ofxtools>=0.9.5`. **`ofxtools` chosen over the ADR's default `ofxparse`** at slice kickoff: ofxparse last released July 2023 (~2 years stale); ofxtools last released Jan 2026, actively maintained, ships type stubs, parses both OFX 1.x SGML and OFX 2.x XML through one API.
- **`tulip_importers.ofx.parse(file_bytes) -> list[ParsedStatementLine]`** with typed `OfxParseError`. Maps `STMTTRN.{DTPOSTED, TRNAMT, NAME, MEMO, FITID, TRNTYPE}` per ADR §Q8. Empty OFX returns `[]`; not-OFX raises. Three test fixtures: hand-crafted OFX 2.x XML + OFX 1.x SGML + empty-OFX. **No real bank statements committed** (per the fixture-strategy decision).
- **Architecture test** `test_architecture_importers_pure.py` bans `tulip_storage`, `tulip_api`, `sqlalchemy`, `fastapi`, `httpx`, `typer` imports from `tulip_importers/src/`. Sibling to the existing AI guard from P5.1; together they pin the importer's purity.
- **API**: new `routers/imports.py`. `POST /v1/imports` accepts multipart upload (file + `account_id` form field + `source_format`). 25 MB cap (`MAX_OFX_BYTES`) defends against OOM uploads; `Settings.attachment_root` already plumbed in P5.1. Idempotency goes through `ImportBatchRepository.find_for_attachment` (new method) so the API never imports the storage model directly. Five new error codes: `import.duplicate_file` (409, with `existing_batch_id` extension), `import.ofx_parse_failed` (400), `import_batch.not_found` (404), `request.payload_too_large` (413), `request.unsupported_media_type` (415). `python-multipart` added as a `tulip-api` dep.
- **CLI**: new `tulip import ofx FILE --account ACCOUNT [--json]`. Reads file from disk; resolves `--account` via the shared `_resolve_account` helper; multipart POST through new `TulipClient.post_multipart`.
- **Settings**: new `attachment_root` field already lived in `Settings` from P5.1; conftest now passes `tmp_path / "attachments"` to the test settings so the encrypted-bytes write doesn't pollute the dev's home directory.
- **`force=true` deferred** (#114): ADR-0004 §Q6 specifies a `?force=true` override but P5.1's unique index `ix_import_batches_idempotency` forbids it. The 409 happy path works; the override is xfailed and tracked as a P5.1 fix-up migration in #114.
- **Tests**: 40 new — 14 core, 8 importer, 1 storage E2E, 10 API endpoint (+1 xfailed for #114), 5 CLI E2E, 2 architecture. Project total: **900 passing** (up from 860).

### P5.2.b — QIF importer — ✅ *(2026-05-05)*

Smaller cousin of P5.2.a. Reuses the API endpoint, multipart upload machinery, `TulipClient.post_multipart`, and `ParsedStatementLine` shape; only the parser is new.

- **Custom parser** in `tulip_importers.qif.parser` per ADR-0004 §Q8 ("custom parser, small format, public domain"). ~150 LOC. No external library — QIF is line-oriented and small enough to handle in-house.
- **Date parsing** handles three dialects: ISO (`2026-05-12`), US 4-digit (`5/12/2026`), US 2-digit (`5/12/26` → 2026). Per-record line numbers in error messages so operators can locate bad rows.
- **Currency**: QIF carries no currency code, so `parse(bytes, *, currency="USD")` takes the account's currency as a kwarg. The API handler reads it from the resolved `Account` row.
- **API endpoint generalized**: `POST /v1/imports` now accepts `source_format=qif` alongside `ofx`. Per-format content-type allowlists (`application/qif`, `text/plain`, etc. for QIF). Per-format error codes: `import.qif_parse_failed` (400). New code `import.unsupported_format` (400, with `format` + `supported` extensions) for future format names landing through the same endpoint.
- **CLI**: new `tulip import qif FILE --account ACCOUNT` subcommand. Refactored `commands/imports.py` to share a `_do_import(...)` helper between OFX and QIF — no duplicated upload plumbing.
- **Tests**: 17 new (12 parser, 3 API, 2 CLI E2E). Project total: **917 passing** (up from 900).

### P5.2.c — CSV importer + per-household profile CRUD — queued

Per-household column-mapping profiles via the `csv_profiles` table from P5.1. CLI: `tulip imports profiles {add,edit,list,show,delete,export,import}` + `tulip import csv FILE --account A --profile P`. YAML round-trip for sharing profiles.

### P5.3 — Reconciliation matcher — queued

`tulip_core.reconciliation.matcher.find_candidates` per ADR-0004 §Q1-Q3 + `MatchConfidence` enum + `Categorizer` Protocol DI hook for Phase 6.

### P5.4 — API + CLI surface (closes Phase 5) — queued

Reconciliation CRUD endpoints, manual matching, carry-forward, `tulip reconcile` interactive flow, import apply / revert.

---

## Other shipped fixes

### P2.x.4 — catch-all unhandled-exception handler — ✅ *(2026-05-01)*

PR #30. Closed #26. Surfaced during P3.2.a smoke testing when a SQLAlchemy URL parse error escaped the Problem Details middleware and emitted Starlette's default `text/plain` 500. New `InternalServerError` (`server.internal_error`, 500) `TulipProblem` subclass; `install_problem_handlers` registers a fourth handler for the `Exception` base. Exception text and tracebacks stay in logs; clients get a generic detail with a `request_id` for support correlation.

---

## Reference: full phase roadmap

See [ARCHITECTURE.md §10](ARCHITECTURE.md). Phases 5 through 9 (importers, AI, reports, ops, pre-cloud) are not in flight.

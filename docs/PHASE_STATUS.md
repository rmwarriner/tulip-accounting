# Tulip Accounting — Phase Status

Single source of truth for what's shipped, what's in flight, and what's queued. The phase definitions live in [ARCHITECTURE.md §10](ARCHITECTURE.md); this file just tracks the state.

**Last updated:** 2026-05-15 · `main` @ **Phase 8 deep security audit complete** — security + privacy Wave-1 follow-ups landing (#239 per-user AI policy + keys, #242 GDPR Art. 16 rectification merged), plus a CLI/importers usability bundle

---

## Current state

- **Phase 0:** ✅ complete
- **Phase 1:** ✅ complete
- **Phase 2 (core API surface):** ✅ complete
- **Phase 2.x (cleanup before Phase 3):** ✅ complete (P2.x.1 – P2.x.4)
- **Phase 3 (CLI):** ✅ complete — P3.1 through P3.4 + P3.6 shipped; P3.5 (toner-friendly print stylesheet) deferred to Phase 8 alongside the actual reports (#22)
- **Post-Phase-3 enhancements:** balance + trial-balance endpoints (#31), account nesting end-to-end (#42), interactive `tulip add --edit` (#43)
- **Pre-Phase-4 docs:** threat-model checkpoint shipped (#56, [docs/THREAT_MODEL.md](THREAT_MODEL.md)). Transaction void / PENDING-only edit (#55) deliberately deferred to Phase 5 alongside reconciliation. Deep security/privacy audits deliberately deferred — see [ARCHITECTURE.md §10 audit cadence](ARCHITECTURE.md) (privacy: pre-Phase 6; deep security: Phase 8; pre-cloud re-audit: Phase 10).
- **Phase 4 (envelopes + sinking funds):** ✅ **complete** — all seven slices merged 2026-05-02. P4.0 (#60), P4.1.a (#62), P4.1.b (#63), P4.2 (#66), P4.3.a (#68 — closes #7 via [ADR-0002](adrs/0002-scheduler-primitive.md)), P4.3.b (#69), P4.3.c (#70).
- **Phase 5 (importers + reconciliation):** ✅ **complete** — P5.0 (#55), P5.1 (storage layer), P5.2.a/b/c (OFX / QIF / CSV importers), P5.3 (matcher + categorizer DI seam), P5.4.a (apply / promote endpoints + CLI), P5.4.b (reconciliation envelope + auto-match), P5.4.c (manual match + carry-forward), and P5.4.d (`tulip reconcile` CLI) all merged. Phase 5 closes per [ADR-0004](adrs/0004-reconciliation.md).

- **Phase 5 cleanup (post-merge):** three follow-ups closed — #127 (reconciliation inbox surfacing prior-completed-recon lines, fix in PR #129), #114 (relax `import_batch` idempotency index + wire `?force=true`, PR #130), #118 (CLI `--household` vs API `household_name` asymmetry — closed wontfix; rationale in `feedback_pr_body_no_backtick_escapes.md` and the issue thread).

- **Pre-internal-beta hardening (#121):** ✅ **complete** — all eight checkboxes merged across PRs #140 / #142 / #143 / #146 / #147 / #148 / #149 / #150 / #151 / #152 (master-key file gate, backup/restore CLI, docker compose, password-stdin TTY hint, UTC balance fix, `tulip doctor`, `tulip periods`, inline balances, QUICKSTART, README rewrite for users). Umbrella closed 2026-05-10.

- **Phase 6 (AI integration):** ✅ shipped — P6.0–P6.5.c complete (ADR + categorize + NL query + daily-insights/anomaly + envelope AI forecast + sinking-fund AI forecast + agentic proposals + AI-driven suggestions + cost-cap/rate-limit chokepoint + `tulip ai config` editor + `log_prompts` toggle + status polish). Capability inventory: `AICategorizer`, `AINLQueryCapability`, `AIForecastCapability` (envelopes + sinking funds), `AIProposalCapability`, the proposal executor registry, the shared `enforce_pre_call` gate, and the `GET|PUT /v1/ai/config` admin surface. The daily-insights handler now forecasts both envelopes and sinking funds via a single `ForecastRequest` dataclass; production wiring of the forecaster into the runner is the only remaining no-op slot, intentionally deferred to a deploy-time toggle. Phase 6 closes.

- **Phase 7 (reports + journal export/import):** ✅ **complete** — P7.1–P7.5 shipped (9 reports in HTML, PDF via weasyprint, CSV output, hledger journal export + import) plus P7.1.b (`tulip reports` + `tulip journal` CLI, #189/#190). Phase 7 closes.

- **Phase 8 (deep security audit + hardening):** ✅ **audit + Wave-1 complete** — the [deep security audit](audits/2026-05-12-deep-security-audit.md) (0 Critical / 8 High / 25 Medium / 24 Low) and the [deep privacy audit](audits/2026-05-13-deep-privacy-audit.md) (1 Critical / 17 High / …) are both merged document-only. **Security Wave-1** (15 issues, #217–#231) is fully landed: MFA defense-in-depth (80-bit recovery codes + single-use challenge `jti` + `slowapi` rate limiting on `/v1/auth/*`, #219), login timing-oracle defense (#221), `actor_kind` spoof fix (#218), gitleaks pin (#228), keyring-unavailable typed error (#227), `?force=true` admin-only (#230), `needs_rehash` wired into login (#224), structlog email/IP redaction (#220), backup-restore path-traversal defence (#217), prod ephemeral-key boot refusal (#223), composite FK on `ai_invocation_id` (#231), audit-coverage gaps (#222), report/journal visibility filter (#229). **Privacy Wave-1** Critical + first three High landed: `local_only` AI profile can no longer route to cloud (#233), AI error-path `response_text` gated on `log_prompts` (#234), household + user right-to-erasure infrastructure — `DELETE /v1/users/{id}`, two-step `DELETE /v1/households/me`, `AttachmentRepository.delete()` + GC handler (#235). Remaining privacy Wave-1 issues (#236–#243) are queued.

- **CLI + importers usability bundle (post-audit):** ✅ shipped — transaction id-prefix display + prefix resolution (#207/#211), interactive reconciliation wizard (#205), `tulip imports show`/`list` (#203/#272), QIF split-posting fidelity (#270) + non-transaction-section skipping (#198) + multi-account import with transfer pairing (#195a/#195b), paper-statement reconciliation (#275), `tulip imports apply --posted` (#210), currency-natural amount precision (#213), account names in `transactions list`/`show` (#214), transaction-level notes (#271), account resolution by name / hierarchical path (#197), interactive UUID picker (#273), `--pending` balance toggle (#274), Rich `Console` honouring `COLUMNS` (#285), right-aligned numeric columns (#289), `/reject` OpenAPI 400 (#194).

- **Phase 9 (terminal UI):** ⏳ scoped, not started — per [ADR-0007](adrs/0007-terminal-ui.md), a Textual TUI as an additive client (CLI stays the scriptable surface). v1 scope is read/browse only. Sits after Phase 8 wraps; pre-cloud preparation renumbers to **Phase 10**.

**Tests:** 1828 passing · **CI:** green on `main`

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

### P5.2.c — CSV importer + per-household profile CRUD — ✅ *(2026-05-05)*

Largest of the P5.2.x slices: CSV parser + Pydantic-validated profiles + 7-endpoint CRUD surface + YAML round-trip + new CLI subgroup. End-to-end: `tulip imports profiles add --name chase ...` → `tulip import csv FILE --account A --profile chase`.

- **`CsvProfile` Pydantic model** in `tulip_importers.csv.profile`. Required fields: `name`, `date_column`, `date_format`, `amount_column`, `description_column`. Defaults: `amount_negative_means="debit"`, `delimiter=","`, `encoding="utf-8"`, `skip_header_rows=1`. **Pydantic chosen over pure-stdlib dataclass** (deviation from `tulip-core`'s precedent) so the API's `CsvProfileCreate`/`CsvProfileRead` schemas reuse the same model directly — no dual definitions to keep in sync.
- **`CsvProfile.to_yaml()` / `from_yaml()`** uses `yaml.safe_load` exclusively. New architecture test `test_architecture_no_unsafe_yaml.py` AST-scans for `yaml.load` / `yaml.full_load` / `yaml.unsafe_load` across all `packages/*/src/` and rejects them.
- **CSV parser** (`tulip_importers.csv.parse(file_bytes, *, profile, currency)`). Handles UTF-8 BOM transparently (`utf-8-sig` upgrade), embedded-comma quoted fields, multi-line quoted fields, blank-row skipping, configurable `skip_header_rows`. Date parsing is strict strftime — the user picked the bank's format on purpose, no presets. Per-row error messages with row numbers.
- **`amount_negative_means="credit"`** flips signs for credit-card-style CSVs where positive = charge.
- **API**: 7 endpoints under `/v1/imports/profiles` — list, create (JSON), get (UUID-or-name), patch (partial), delete (hard), export (`application/x-yaml`), import (raw YAML body). `POST /v1/imports` extended with a `csv` branch + `profile_id` form field.
- **CLI**: new `tulip imports profiles {add, list, show, delete, export, import}` subgroup. `add` accepts both individual `--*-column` flags and `--from-yaml FILE`. New `tulip import csv FILE --account A --profile NAME` resolves the profile client-side via the existing UUID-or-name pattern. Refactored `_do_import` shared helper to pass `extra_form` for format-specific fields.
- **New error codes**: `import.csv_parse_failed` (400), `import.csv_profile_missing` (400), `csv_profile.not_found` (404), `csv_profile.duplicate_name` (409), `csv_profile.invalid_yaml` (400). The OFX/QIF errors stay distinct.
- **Router registration order matters**: `csv_profiles.router` must register *before* `imports.router` because both prefix on `/v1/imports`; the more specific `/v1/imports/profiles` must win FastAPI's first-match dispatch.
- **CLI alias**: `tulip import` (singular) kept alongside the new `tulip imports` (plural) for back-compat with PR-body smoke tests using the old name.
- **New deps on `tulip-importers`**: `pydantic>=2.7`, `pyyaml>=6.0`, `types-pyyaml`. Pydantic was previously transitive via tulip-api; now direct on tulip-importers as well.
- **Tests**: 57 new (14 profile model + 14 parser + 16 profile-CRUD API + 4 imports-endpoint CSV cases + 5 CLI E2E + 4 architecture/schemathesis adjustments). Project total: **974 passing** (up from 917). One schemathesis case skipped (path-collision between `POST /import` and `/{id_or_name}` — harmless, documented inline).

### P5.3 — Reconciliation matcher + categorizer DI seam — ✅ *(2026-05-06)*

Pure-`tulip-core` slice. No API, no CLI, no storage. Adds the bucketed-confidence matcher per ADR-0004 §Q1-Q2 and the `Categorizer` Protocol seam that Phase 6's `AICategorizer` will plug into.

- **`MatchConfidence` enum** (`HIGH > MEDIUM > LOW`). Plain `Enum`, not `str` mixin — the str mixin would let `MatchConfidence.HIGH < "low"` silently return True via alphabetic comparison ("high" < "low"). String values match the `reconciliation_matches.confidence` CHECK constraint from P5.1 so JSON round-trip needs no converter.
- **`CandidateMatch` value object** — frozen dataclass, equality by `(statement_line_id, ledger_transaction_id)` pair (so re-running the matcher with different fuzzy thresholds collapses duplicate proposals). `match_amount` stored explicitly so P5.4's split-match work doesn't change the shape.
- **`find_candidates(statement_lines, ledger_transactions, *, account_id, reconciled_transaction_ids=frozenset())`** — pure function. Walks each statement line × each eligible ledger tx; eligibility = ledger status (POSTED or RECONCILED) + correct account posting + not already reconciled. Money equality enforces currency match. Output sorted by `(statement_line_id, ledger_transaction_id)` for deterministic test diffs.
- **Confidence bucketing** per ADR §Q2: `HIGH` = same date + fuzzy ≥ 0.9; `MEDIUM` = same date with lower fuzzy OR ±3 days with fuzzy ≥ 0.6; `LOW` = ±1-3 days with fuzzy < 0.6. **Same-date is never LOW** — even no-fuzzy matches surface for user confirmation.
- **`rapidfuzz>=3.0,<4`** added as the first runtime dep on `tulip-core` (deviation from "no deps in core" but called out in the dependency comment). `token_set_ratio` with `default_process` (lowercase + punctuation strip) is the heuristic. Pinned `<4` because tokenization changes across major versions could flip bucket-boundary tests.
- **"Already reconciled" via `reconciled_transaction_ids: frozenset[UUID]` keyword arg** — the matcher has no DB access; caller queries `transactions.reconciliation_id IS NOT NULL` and passes the set. Documented as a caller obligation; P5.4 wires it.
- **`Categorizer` Protocol** + **`NullCategorizer`** + module-global registry (`register_categorizer` / `get_categorizer` / `_reset_categorizer_for_testing`). Async-by-design: `categorize` is `async def` from day one because Phase 6's `AICategorizer` will issue an LLM call. v1's `NullCategorizer` returns `Imbalance:Unknown` — a real rule-based categorizer + per-household rule storage land in P5.4 alongside the API surface.
- **Hypothesis property tests** for the bucket-classification function: `_classify_confidence(delta_days, fuzzy)` is exhaustively probed across the window, asserting boundary invariants (same-date is never LOW; date drift never returns HIGH; outside-window always emits no candidate).
- **Tests**: 54 new (6 `MatchConfidence` + 12 `CandidateMatch` + 18 matcher integration + 5 hypothesis properties + 15 categorizer + registry; the matcher's hypothesis suite expands to multiple parametrized cases under the `--hypothesis-show-statistics` runner). Project total: **1028 passing** (up from 974).

### P5.4.a — Apply / promote endpoints + CLI — ✅ *(2026-05-06)*

First sub-slice of P5.4: closes the importer loop. Statement lines now turn into PENDING ledger transactions via the registered `Categorizer` (currently `NullCategorizer` from P5.3). The reconciliation envelope (P5.4.b), manual match + carry-forward (P5.4.c), and the `tulip reconcile` CLI (P5.4.d) follow.

- **`POST /v1/imports/{batch_id}/apply`** — promote every non-excluded, non-already-promoted line in the batch to a PENDING `transaction` with two `posting`s (bank-side + categorizer-side). Flips `import_batches.status = 'applied'`. Idempotent at the batch level: re-applying returns `409 import.already_applied`.
- **`POST /v1/imports/{batch_id}/lines/{line_id}/promote`** — single-line promotion. Returns `201` with `{statement_line_id, transaction_id}`. Idempotent at the line level (`409 import.line.already_promoted` carries the existing tx id in the response). An excluded line returns `422 import.line.excluded` — un-exclude it first.
- **`tulip imports apply BATCH_ID`** — CLI wrapper. Renders a one-line summary in human mode; passes the JSON body through in `--json` mode.
- **Storage prerequisites** rolled into the slice:
  - New migration `d5c8e7a91f2b` adds `statement_lines.promoted_transaction_id` (composite FK to `transactions(household_id, id)`) for O(1) idempotency lookup. Picked over a back-query through `transactions.imported_from_id` because back-query is O(N) per line.
  - `AccountRepository.get_by_code` for resolving the categorizer's account-code suggestion to an `account_id`.
  - `TransactionRepository.save_balanced(..., imported_from_id=...)` propagates the FK through `_save` (the column existed in P5.1 but no path set it).
  - Registration now seeds `Imbalance:Unknown` (EQUITY, base currency) per household — chosen over `EXPENSE` because suspense accounts conventionally sit on the balance sheet rather than shifting the P&L until categorized.
- **`Categorizer` registry wiring** — `tulip_api.main` now calls `register_categorizer(NullCategorizer())` at module-import time. Phase 6's swap-in lands at this exact line; explicit registration makes the production wiring grep-able.
- **Service-level orchestration** lives in `tulip_api.services.import_apply` (`promote_statement_line`, `apply_batch`, `ApplyResult`). The router awaits the service; the service flushes but never commits, so the caller can wrap a single `commit()` around the audit-log row + the promoted-tx rows for atomicity.
- **Atomicity in `apply_batch`** is the caller's responsibility — service flushes only. Mid-batch failure (e.g., categorizer suggests an unknown account on line 3 of 5) raises `CategorizeUnknownAccountError`; the router returns `409 import.categorize.unknown_account` and FastAPI's exception path triggers `session.rollback()`. No partial state.
- **`409 import.categorize.unknown_account`** carries the offending `account_code` as an extension field, so the client can prompt the user to create the missing account or re-train the categorizer.
- **Audit-log entries**: `import_apply` (one row per batch, with `created_count` / `skipped_count` / `transaction_ids`) and `statement_line_promote` (one row per line, with `transaction_id` / `batch_id`).
- **Tests**: 11 service tests + 9 router tests + 4 storage-layer tests + 2 CLI integration tests + 1 register-seed test, plus a small architecture-test enhancement (the `if TYPE_CHECKING:` walker filter so type-only model imports don't trip the chokepoint guard). Project total: **1061 passing** (up from 1028).

### P5.4.b — Reconciliation envelope + auto-match — ✅ *(2026-05-06)*

Second sub-slice of P5.4: server-side reconciliation envelope CRUD + the auto-match endpoint that wires P5.3's matcher + persistence. Manual match (P5.4.c) and CLI (P5.4.d) follow.

- **`POST /v1/reconciliations`** — open an IN_PROGRESS envelope tied to one `import_batch` for one `account`. Validates: account exists, batch exists + belongs to the account, currency match between envelope and account, no other IN_PROGRESS reconciliation for the account (one-at-a-time invariant — locked decision; simplifies the matcher's already-reconciled detection).
- **`GET /v1/reconciliations/{id}`** — envelope **+ inline review pane**: matches, unmatched statement lines, unmatched ledger transactions in the period window. One round-trip for the entire reconciliation UI per the locked decision; the inbox is what the CLI/UI in P5.4.d will hit on every refresh.
- **`POST /v1/reconciliations/{id}/auto-match`** — wires `tulip_core.reconciliation.matcher.find_candidates` (P5.3) over the batch's non-excluded statement lines + the period's POSTED ledger transactions, persists each emitted `CandidateMatch` as a `reconciliation_matches` row with `matcher_version="v1"`, `created_by_user_id=NULL`, and the bucketed `confidence` (HIGH/MEDIUM/LOW). Re-running on a reconciliation that already has matches returns `409 reconciliation.matches_exist` per the locked decision — user rejects individual matches or DELETEs and recreates for a fresh pass.
- **`POST /v1/reconciliations/{id}/matches/{match_id}/reject`** — delete a match row, return the line + transaction to the unmatched pool. Mirrors `ReconciliationMatchRepository.reject`.
- **`POST /v1/reconciliations/{id}/complete`** — strict balance check: `sum(match.match_amount) == ending_balance - starting_balance`. On pass, hands off to `ReconciliationRepository.complete()` (the **only** writer of `transactions.reconciliation_id` + `reconciled_at` per ADR §Q7 — architecture-test enforced). On fail, `409 reconciliation.unbalanced` carries `expected_net`, `matched_net`, and `residual` as extension fields so the client can show the user exactly how much is unaccounted-for.
- **`DELETE /v1/reconciliations/{id}?cascade=true`** — un-reconcile. Requires explicit `?cascade=true` (omitting it returns `400 reconciliation.cascade_required` to gate the destructive intent). New `ReconciliationRepository.revert()` method nulls `transactions.reconciliation_id` + `reconciled_at`, clears `statement_lines.reconciliation_match_id` (no FK to cascade), then deletes the reconciliation row (cascade-deletes matches via the P5.1 FK).
- **Service module** `tulip_api.services.reconciliation_match` with `auto_match()` and `complete()` async + sync entry points. Mirrors P5.4.a's pattern: services flush only; the router wraps a single `commit()` around the audit-log row + the persisted matches so any mid-flight failure rolls back atomically.
- **Storage adapter** `_tx_to_domain(storage_tx, postings)` materialises a domain `Transaction` (with `Posting` tuple) from a storage row + its posting list — the matcher operates on domain types only, but `TransactionRepository.list_headers()` returns storage rows. The adapter is small and lives in the service module rather than core (no domain → storage cycle).
- **Domain ↔ storage `MatchConfidence` translation** — both layers have a `MatchConfidence` enum with the same string values; the service module owns the explicit mapping table so the boundary is grep-able.
- **No `transactions.reconciliation_id` write outside `ReconciliationRepository`** — verified by `test_architecture_no_direct_reconciled_at_writes`. The architecture test was extended to allowlist the new router + service files (which pass `reconciliation_id` as the FK column on `reconciliation_matches`, never to write the denorm on `transactions` — same kwarg-name collision the existing `repositories/reconciliation_match.py` carve-out already documents).
- **Audit-log entries** — `reconciliation_create`, `reconciliation_revert`, `reconciliation_auto_match`, `reconciliation_match_reject`, `reconciliation_complete`. Each carries enough state to reconstruct the action without consulting the live row (the row may be gone post-revert).
- **Tests**: 7 service tests + 15 router tests + 2 storage-layer tests = 24 new (1061 → 1090 passing).

### P5.4.c — Manual match + carry-forward — ✅ *(2026-05-07)*

Third sub-slice of P5.4. Lets the user resolve residuals auto-match can't pick up (manual match) and exclude in-flight ledger transactions from the current period's balance check (carry-forward — e.g., a check the bank hasn't cashed yet). The `tulip reconcile` CLI (P5.4.d) closes Phase 5.

- **`POST /v1/reconciliations/{id}/matches`** — body `{statement_line_id, ledger_transaction_id, match_amount, currency}`. Persists a manual match: `created_by_user_id` set, `matcher_version=NULL`, `confidence=NULL` per ADR §Q9. Validates four invariants before insert (locked decision: cheap insurance against typo'd UUIDs):
  - Statement line exists and belongs to the reconciliation's source batch (`400 reconciliation.line_not_in_batch` otherwise).
  - Statement line not already matched (`409 reconciliation.line_already_matched` — locked decision: user must explicitly reject the prior match first).
  - `match_amount == line.amount` exactly (`400 reconciliation.line_amount_mismatch` — partial-of-one matching deferred past v1 per ADR §Q3).
  - Ledger transaction exists and has at least one posting on the reconciliation's account (`400 reconciliation.tx_account_mismatch`).
- **`POST /v1/reconciliations/{id}/carry-forward`** — body `{transaction_ids: [UUID]}`. Marks ledger transactions as carry-forward via `transactions.carried_forward_from_reconciliation_id`. Validates each tx exists and falls within `[period_start, period_end]` — locked decision: out-of-period rejected (`400 reconciliation.tx_not_in_period`) so the user can't paper over real reconciliation problems.
- **`DELETE /v1/reconciliations/{id}/carry-forward/{transaction_id}`** — un-mark.
- **`/complete` balance equation updated** to `sum(matched) + sum(carry_forward) == ending - starting`. Carry-forward txs deduct from the expected net for *this* reconciliation: the bank counts them in `ending_balance`, but the user has explicitly said "this isn't mine yet" — so their bank-side posting amounts are subtracted. Carry-forward sums use a session-scoped query (no new repo method); the math is per-reconciliation and doesn't merit caching.
- **`ReconciliationRepository.set_carry_forward(tx_id, recon_id)`** + **`clear_carry_forward(tx_id)`** — the single chokepoint for `carried_forward_from_reconciliation_id` writes (architecture-test-enforced — same module that owns `complete()` and `revert()`).
- **`revert()` extension**: now also nulls carry-forward links pointing at the deleted reconciliation. Carry-forward is "this tx was counted in reconciliation X"; when X is reverted, that audit-trail link breaks and gets cleared. Mirrors how `transactions.reconciliation_id` is nulled in the same method.
- **Audit-log entries**: `reconciliation_match_create_manual`, `reconciliation_carry_forward_add`, `reconciliation_carry_forward_remove`. The `_manual` suffix on the match-create action distinguishes user-driven matches from `reconciliation_auto_match` (which writes one row covering the whole batch).
- **Tests**: 8 service tests + 7 router tests + 4 storage-layer tests + 1 inbox-shape test = ~20 new (1090 → **1113 passing**).

### P5.4.d — `tulip reconcile` CLI — ✅ *(2026-05-07)*

Closes Phase 5. Imperative CLI subcommand group with 10 commands wrapping the /v1/reconciliations endpoints. Per the locked decisions: no Textual TUI for v1 (a follow-up if/when end-to-end review fatigue surfaces); UUIDs only for `--line` / `--tx` / `--batch`; `--account` reuses the UUID-or-code resolver from `commands.accounts`.

- **`tulip reconcile create --account ACCT --batch BATCH --period START..END --starting AMT --ending AMT [--currency USD]`** — opens a reconciliation envelope. `--period` parses as `YYYY-MM-DD..YYYY-MM-DD` via a Typer parameter callback. `--starting` and `--ending` are `str` at the Typer layer (Typer doesn't natively render `Decimal`) and parsed to `Decimal` inside the function body — keeps `mypy --strict` clean while preserving accurate decimal arithmetic.
- **`tulip reconcile list [--account ACCT] [--status STATUS]`** — lists reconciliations newest-first. Calls the new `GET /v1/reconciliations` endpoint added in this slice (~15 LOC server-side: extends `ReconciliationRepository.list_for_household` to accept optional `account_id` + `status` filters; returns `{items: list[ReconciliationRead]}`). Renders a rich table in human mode; `--json` passes through the full body.
- **`tulip reconcile show RECON_ID`** — renders the four-section review pane: envelope summary + matches + unmatched statement lines + unmatched ledger transactions. Empty sections render `(none)` rather than being omitted (predictable structure beats minimalism for a top-to-bottom scan).
- **`tulip reconcile auto-match RECON_ID`** — runs the matcher; renders `Auto-matched: N matches (high=H, medium=M, low=L)`.
- **`tulip reconcile match RECON_ID --line UUID --tx UUID --amount AMT [--currency USD]`** — manual match.
- **`tulip reconcile reject RECON_ID MATCH_ID`** — delete a match; line + transaction return to unmatched.
- **`tulip reconcile carry-forward RECON_ID --tx UUID [--tx UUID ...]`** — repeatable `--tx` flag (Typer infers repetition from `list[UUID]` annotation; no `multiple=True` needed).
- **`tulip reconcile carry-forward-remove RECON_ID TX_ID`** — un-mark.
- **`tulip reconcile complete RECON_ID`** — finalise.
- **`tulip reconcile delete RECON_ID --cascade`** — `--cascade` is required client-side (CLI exits 2 if omitted with a "Pass --cascade to confirm" message — gates the destructive intent without round-tripping to the server's 400). The server's own `?cascade=true` requirement is the second line of defense.
- **New server-side endpoint** `GET /v1/reconciliations` with optional `account_id` + `status` query params. Tenant-scoped via the existing `claims.household_id` dependency. Unknown-account-id returns an empty list (silent scoping; 404 reserved for malformed paths). Invalid status values get FastAPI's 422.
- **Tests**: 8 CLI integration tests (subprocess against a live API per the existing `live_api` + `authed_session` fixture pattern; exercises every command + the negative paths for unbalanced complete and missing `--cascade`) + 8 router tests for the new GET endpoint (filters, tenant scoping, 422 on bad status, empty-on-unknown-account). Project total: **1129 passing** (up from 1113).
- **Phase 5 closes** with this slice. Importers + reconciliation are fully wired end-to-end: upload → apply → match → complete, with carry-forward and manual override available, all via API + CLI.

---

## Phase 8 — Operations + hardening — 🔄 in progress (audit + Wave-1 done)

Per [ARCHITECTURE.md §10](ARCHITECTURE.md), Phase 8 is the deep
security audit + hardening pass. The audits are done and the first
remediation wave is fully landed; the slice-per-PR rhythm continued
through it (one issue → one branch → one PR).

### Audits — ✅ *(2026-05-12 / 2026-05-13)*

Two document-only audits merged:

- [`docs/audits/2026-05-12-deep-security-audit.md`](audits/2026-05-12-deep-security-audit.md)
  — 0 Critical / 8 High / 25 Medium / 24 Low / 41 Info, plus a §11
  Wave-1/2/3 remediation roadmap. Filed 15 Wave-1 follow-up issues
  (#217–#231).
- [`docs/audits/2026-05-13-deep-privacy-audit.md`](audits/2026-05-13-deep-privacy-audit.md)
  — 1 Critical / 17 High / 28 Medium / 22 Low / 38 Info. Filed 17
  Wave-1 follow-up issues (#233–#249).

### Security Wave-1 — ✅ *(15 issues, #217–#231)*

Every Wave-1 security follow-up shipped, one PR per issue:

- **#219 — MFA defense-in-depth.** Recovery codes bumped 8→16 base32
  chars (40→80 bits, `XXXX-XXXX-XXXX-XXXX`); MFA-challenge JWT carries a
  single-use `jti` persisted in a new `used_mfa_challenges` table;
  `slowapi` rate-limiting wired on `/v1/auth/login`, `/login/mfa`,
  `/login/recover`, `/refresh`; audit rows for `login_failed` /
  `mfa.code_rejected` / `mfa.recovery_rejected`.
- **#221** login timing-oracle defense (dummy verify + no short-circuit);
  **#218** drop `ai_invocation_id` from `ProposalCreate` (actor-kind
  spoof); **#228** gitleaks Docker pin; **#227** typed error when the
  keyring backend is unavailable; **#230** `?force=true` import-dedup
  override is admin-only; **#224** `needs_rehash()` wired into login;
  **#220** email/IP added to the structlog redaction whitelist + a
  stdlib `LogRecord` redactor; **#217** backup-restore rejects
  attachment-member path traversal; **#223** boot refuses ephemeral
  master-key / JWT-secret under `TULIP_ENV=prod`; **#231** composite FK
  on `pending_proposals.ai_invocation_id` + `notifications.ai_invocation_id`;
  **#222** audit-log rows for logout / refresh / proposal / refill-schedule;
  **#229** role-visibility filter threaded through the reports + journal
  export.

### Privacy Wave-1 — 🔄 Critical + Highs landing (#233–#235, #242)

- **#233 (C-1)** — `resolve_policy` no longer lets a `local_only` AI
  profile resolve to a cloud provider via `fallback_provider`; pinned to
  a `_LOCAL_PROVIDERS` allowlist (ADR-0005 §Q4 lock).
- **#234 (H-1)** — AI error-path `response_text` is gated on
  `policy.log_prompts`, matching the success path; the structured
  `outcome` enum is the load-bearing field.
- **#235 (H-2+H-3)** — right-to-erasure infrastructure:
  `DELETE /v1/users/{user_id}` (admin-only, last-admin guard, audit-PII
  redaction), two-step `DELETE /v1/households/me` (token-confirmed),
  `AttachmentRepository.delete()` with refcount, and an `attachment_gc`
  scheduler handler. New `pending_household_erasures` table.
- **#239 (H-11 + M-16)** — per-user AI policy + per-user AI keys:
  `users.ai_policy` JSON column (nullable = inherit household);
  `HouseholdContext.acting_user_id` plumbed through all five
  `resolve_policy` callsites (categorize / nl_query / proposals /
  forecast / `routers/ai.py` × 3) so members can ratchet up severity
  from the household floor; `PUT /v1/users/{me,user_id}/ai-policy`
  endpoints; per-user key precedence in `routers/ai.py`
  (`_resolve_provider_key` helper); six new key endpoints under
  `/v1/ai/keys/{me,users/{user_id}}/{provider}`. New audit actions:
  `user.ai_policy_set` / `user.ai_key_set` / `user.ai_key_forgotten`
  (no key bytes in audit rows). `users.ai_policy` exposed in
  `UserRecordExport` for #241 Art. 15 completeness.
- **#242 (H-14)** — GDPR Art. 16 rectification:
  `PATCH /v1/transactions/{id}/description` rewrites a POSTED /
  RECONCILED transaction's `description` / `reference` / `notes` in
  place, and rewrites the paired reversal sibling's
  `f"Reversal of {old}: {reason}"` quote to `[redacted]` so the
  pre-rectification PII doesn't survive at rest;
  `PATCH /v1/users/me` mutates `display_name` (no re-auth) and `email`
  (re-auth via `current_password` in body);
  `POST /v1/auth/password/change` rotates the Argon2id hash and revokes
  every outstanding refresh token. New audit actions:
  `description_rectified` / `profile_updated` / `password_changed`.

Remaining privacy Wave-1: #237 (cross-user-within-household visibility
filter) — dormant, gated on multi-user invite landing first. All other
issues in the #236–#243 range have closed.

### Post-audit CLI + importers usability bundle — ✅

A batch of usability issues surfaced while testing, run as a
dependency-ordered queue of one-PR-per-issue slices:

- **Transactions / accounts:** id-prefix column + prefix resolution
  (#207/#211), account names in `list`/`show` (#214), transaction-level
  notes wired through repo/API/CLI (#271), account resolution by unique
  name or hierarchical colon-path (#197), currency-natural amount
  precision (#213), right-aligned numeric columns (#289).
- **Imports:** `tulip imports show` (#203) + `tulip imports list` (#272),
  QIF split-posting fidelity (#270), QIF non-transaction-section
  skipping (#198), multi-account QIF import via `--account-map` with
  cross-account transfer pairing (#195a/#195b), `tulip imports apply
  --posted` (#210).
- **Reconciliation:** interactive auto-match wizard (#205),
  paper-statement (no-OFX) reconciliation flow (#275).
- **CLI ergonomics:** interactive UUID picker when a required id is
  omitted (#273), `tulip balance --pending` toggle (#274), Rich
  `Console` honouring `COLUMNS` for stable non-TTY rendering (#285).
- **API contract:** documented 400 on `/v1/ai/proposals/{id}/reject`
  (#194).

---

## Phase 7 — Reports + journal export/import — ✅ shipped

Per ARCHITECTURE.md §8 + §10. v1 ships 9 reports in HTML+PDF+CSV, plus
hledger-compatible journal export + basic journal import. "Workstream
slicing": P7.1 HTML, P7.2 PDF, P7.3 CSV, P7.4 journal export, P7.5
journal import. P7.1.b adds the CLI surface over both.

### P7.1.b — `tulip reports` + `tulip journal` CLI — ✅ *(2026-05-13; closes #189)*

Closes the deferred CLI surface from P7.1. Both report and journal
endpoints were API-only after Phase 7 closed; this slice wires them
into the imperative CLI per the existing client pattern (architecture
test in `tulip-cli/tests/test_architecture.py` keeps the CLI a pure
network client).

**`tulip reports`** — Typer group with 9 subcommands, one per
`/v1/reports/*` endpoint:
- `trial-balance`, `balance-sheet`, `envelope-status`,
  `sinking-fund-progress` — share `--as-of`.
- `income-statement`, `cash-flow` — `--start` / `--end`
  (income-statement also takes optional `--prior-start` / `--prior-end`).
- `reconciliation-summary` — `--status` filter.
- `audit-log` — `--start` / `--end` / `--actor` / `--entity-type` /
  `--limit` / `--offset`.
- `custom-query` — `--sql` (subject to the same SQL-safety gate as
  `tulip ai query`).

Each subcommand accepts `--format json|html|pdf|csv` (default `json`)
and `--output PATH`. JSON/HTML default to stdout; PDF/CSV require
`--output` since binary-to-terminal isn't useful. Date options are
validated client-side with a `typer.BadParameter` for fast feedback.

**`tulip journal`** — two subcommands:
- `journal export` wraps `GET /v1/journal/export`. Accepts `--start`,
  `--end`, `--output`. Writes to stdout by default.
- `journal import FILE` wraps `POST /v1/journal/import`. Reads the
  file's bytes, posts as `text/plain`, surfaces `{created, transaction_ids}`
  or the typed Problem Details errors verbatim.

**Tests** — +17 integration tests across two files:
- `test_reports_command.py` (9 tests): trial-balance JSON/HTML/CSV-to-file/
  PDF-requires-output paths; `--as-of` and invalid-date forwarding;
  audit-log pagination; custom-query unsafe-SQL surfacing; auth gate.
- `test_journal_command.py` (8 tests): export to stdout / file / date
  range / invalid date; **export→import round-trip** (primary
  acceptance); import parse-error surfacing; `--json` passthrough on
  import; auth gate.

Full suite: 1528 passed, 1 skipped in 4:01.

### P7.5 — hledger journal import — ✅ *(2026-05-12)*

Final Phase-7 slice. Closes the round-trip with P7.4: users can pull
their ledger out as hledger journal text and push it back in. Imported
transactions land in **PENDING status** for user review — same
convention as the existing OFX / QIF / CSV importers (#74).

**Parsing** (`tulip_reports.journal.parse`):
- `parse_journal(text) -> ParsedJournal` — pure function, no DB
  access. Accepts the subset of hledger that `export_journal` emits
  plus forgiveness for hand-edits (extra blank lines, comments).
- Errors don't abort parsing; they're collected with line numbers
  so the user can fix and retry.

**Resolution** (`tulip_reports.journal.import_`):
- `resolve_journal(session, *, household_id, parsed) -> ImportResult`
  maps each posting's `<Type>:<code>:<name>` path back to a tulip
  account. By code first (most reliable), then by exact `(type, name)`.
- Validates: every account resolves, posting currency matches
  account currency, postings balance per currency.

**HTTP** (`tulip_api.routers.journal`):
- `POST /v1/journal/import` accepts plain-text body (5 MB cap),
  parses + resolves, inserts as PENDING transactions via
  `TransactionRepository.save_balanced`. Response carries
  `created` count + the `transaction_ids` array.
- Parse errors → `journal.parse_failed` (400) with `errors`
  extension array.
- Resolve errors → `journal.import_failed` (400) with the same shape.

**Tests** — +8 in `test_journal_import.py`:
- Happy path: minimal two-posting tx creates one PENDING transaction.
- **Export → import round-trip** through the existing transactions
  endpoint — the primary acceptance criterion.
- Parse error: non-date header surfaces with line number.
- Resolve error: unknown account path.
- Resolve error: unbalanced postings.
- Resolve error: currency mismatch.
- Empty body returns 201 with `created: 0`.
- Auth gate.

Full suite: 1511 passed locally.

**Phase 7 closes** with this slice. CLI subcommands tracked as P7.1.b
follow-up (see below for the in-flight CLI slice).

### P7.4 — hledger-compatible journal export — ✅ *(2026-05-12)*

Fourth Phase-7 slice. Adds a plain-text export of the household
ledger in hledger journal format — the de-facto standard for the
plain-text-accounting community. Output round-trips cleanly through
hledger / ledger-cli for users who want analysis in those tools.

**Storage-side**:
- `tulip_reports.journal.export.export_journal(session, *,
  household_id, start=None, end=None) -> bytes` — pure function
  rendering all POSTED + RECONCILED transactions (excluding voided)
  as a hledger journal. Account paths use the colon hierarchy
  `<Type>:<code>:<name>` (or `<Type>:<name>` when no code is set).

**HTTP**:
- New router `tulip_api.routers.journal` with one endpoint:
  `GET /v1/journal/export[?start=...&end=...]`. Returns
  `text/plain; charset=utf-8` with `Content-Disposition:
  attachment; filename=tulip-journal-<date>.journal` so browsers
  download to a sensible name.

**Format details**:
- One `YYYY-MM-DD description` header per transaction.
- Two-space indent before each posting; account / amount separated
  by at least two spaces (hledger's minimum).
- Amounts use `.` decimal, no thousand separators (canonical
  hledger), banker's-rounded to two decimals.
- One blank line between transactions.
- `tx.reference` is prefixed to the description as `(REF) desc`
  when present.
- Pending + voided transactions excluded — same convention as the
  trial balance / income statement.

**Tests** — +6: empty household, single tx renders correctly,
date-range filter, sensible filename in Content-Disposition,
auth gate, code-less accounts fall back to `<Type>:<name>`.

Full suite: 1502 passed locally.

### P7.3 — CSV output for all 9 reports — ✅ *(2026-05-12)*

Third Phase-7 slice. Layers CSV output on top of HTML (P7.1) and PDF
(P7.2). No new dependencies — stdlib `csv` + `io.StringIO` handle
RFC 4180 quoting/escaping per row.

**Engine**:
- `ReportRenderer.csv_bytes(headers, rows)` static helper — encodes
  a list-of-lists table as UTF-8 CSV bytes with `\r\n` line
  terminators.

**Per-report**:
- Each module gains `render_csv(data) -> bytes`. Shape varies per
  report:
  - Trial balance, envelope status, sinking-fund progress,
    reconciliation summary, audit log → one row per data record.
  - Balance sheet, income statement, cash flow → one row per
    (section, account) with a `Section` column.
  - Custom query → raw column headers + result rows.

**HTTP**:
- Each endpoint's `format` widened to
  `Literal["json", "html", "pdf", "csv"]`.
- CSV responses return `text/csv` with
  `Content-Disposition: attachment; filename=<report>-<date>.csv`
  (attachment, not inline — CSVs typically download).

**Tests** — +8 (engine has no new tests since `csv_bytes` is a
trivial wrapper; coverage comes from the parametrized integration
tests + the trial-balance integration test).

Full suite: 1495 passed locally.

### P7.2 — PDF rendering via weasyprint — ✅ *(2026-05-12)*

Second Phase-7 slice. P7.1 shipped the 9 reports as HTML; this
slice layers PDF output on top via `weasyprint>=63`. Same templates,
same toner-friendly CSS — the `@media print` block in `base.html`
already exists from P7.1, so PDF rendering is "HTML through
weasyprint" with zero per-report template changes.

**Engine**:
- `ReportRenderer.render_pdf(template, **context)` lazy-imports
  weasyprint, renders the same template to HTML, pipes through
  `weasyprint.HTML(string=...).write_pdf()`. Returns `bytes`.

**Per-report**:
- Each report module gains `render_pdf(data) -> bytes` alongside
  `render_html`. One-line delegate.

**HTTP**:
- Each endpoint's `format` query param widened to
  `Literal["json", "html", "pdf"]`. PDF responses return
  `application/pdf` with a `Content-Disposition: inline;
  filename=<report>-<date>.pdf` header for sensible default
  filenames in browsers.

**System dependencies**:
- weasyprint 60+ uses pure-Python PDF generation (pydyf) but still
  needs Pango for text shaping + HarfBuzz + fontconfig.
- `.github/workflows/ci.yml` apt install extended:
  `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfontconfig1`.
- `deploy/docker/Dockerfile` runtime stage apt install extended
  with the same libs. The Dockerfile's `uv sync` line also gains
  `--package tulip-reports` since the API now imports it at
  module-load time.

**Tests** — +9:
- Engine: 1 unit test confirming `render_pdf` returns valid PDF
  bytes (`%PDF-` magic).
- Trial balance: 1 integration test for `?format=pdf`.
- 7 parametrized integration tests across the 8 new reports'
  `?format=pdf` paths (custom-query excluded from the parametrized
  matrix because its SQL parameter is per-test, but it has its own
  PDF-capable code path).

Full suite: 1487 passed locally.

### P7.1 — `tulip-reports` skeleton + 9 reports in HTML — ✅ *(2026-05-12)*

First Phase-7 slice. Stands up the `tulip-reports` package + all 9 v1
reports rendered to HTML. Toner-friendly per ARCHITECTURE.md §8 (white
background, thin black rules, sans-serif, color reserved for emphasis,
print stylesheet @media block).

**Rendering pipeline**:
- `tulip_reports.engine.ReportRenderer` — Jinja2 environment with
  custom filters (`money` for Decimal formatting + currency suffix,
  `isodate` for date/datetime → ISO 8601, `negative` test for the
  toner-friendly emphasis class). `StrictUndefined` catches template
  typos at render time; `autoescape` is on for HTML safety.
- `templates/base.html` — shared chrome + the §8 CSS contract.
- Per-report modules under `tulip_reports.reports.*` each expose a
  `build(session, **filters) -> ReportData` dataclass and
  `render_html(data) -> str`.

**Reports** (all 9):
1. **Trial balance** — extended the existing JSON endpoint with
   `?format=html`. Existing contract unchanged.
2. **Balance sheet** — regroups trial-balance rows by account type
   (assets / liabilities / equity) + retained earnings as cumulative
   income − expenses through `as_of`.
3. **Income statement** — revenue + expenses over a date range, with
   optional prior-period comparison. Income signs flipped server-side
   so "money in" is positive in both columns.
4. **Cash flow** — net change per asset account over a date range,
   split into inflows (positive delta) vs outflows.
5. **Envelope status** — active envelopes with current balance vs
   budget + utilization percentage.
6. **Sinking-fund progress** — active sinking funds with balance vs
   target_amount + target_date + days remaining.
7. **Reconciliation summary** — aggregated reconciliations (status
   filter optional) with match + carry-forward counts.
8. **Audit log** — paginated audit-log query with date / actor /
   entity_type filters; bounded at 500 rows per page.
9. **Custom query** — read-only SQL against AI views (P6.2),
   validated via `tulip_ai.sql_safety.validate_and_rewrite`. Same
   safety gate as the NL-query capability; failures surface as a
   `report.unsafe_query` 400.

**HTTP**: `GET /v1/reports/<name>?format=json|html` for each report.
Default `json` preserves backward compat for the existing trial
balance endpoint; HTML is the new path.

**Architecture-test exemptions**: the reconciliation-summary report
reads the `Reconciliation` model directly for display; same read-only
allowlist entry as `csv_profiles.py` (P5.0 precedent).

**Tests** — +52: 16 engine + filter unit tests, 1 trial-balance HTML
integration test (the foundational pattern), +35 across the new
report endpoints (JSON shape, HTML response Content-Type, auth gate,
date filters, custom-query SQL safety gate).

**Out of scope** (deferred — all subsequently delivered):
- PDF rendering via weasyprint — P7.2.
- CSV output — P7.3.
- CLI subcommands (`tulip reports <name>`, `tulip journal {export,import}`) — P7.1.b.
- Journal export/import — P7.4 / P7.5.

---

## Phase 6 — AI integration — in flight (design)

Phase 6 entry criterion per [ARCHITECTURE.md §10](ARCHITECTURE.md) was a privacy audit shaping the design before any code lands. The audit is now shipped as an ADR; implementation begins with P6.1.

### P6.5.c — Sinking-fund forecast extension — ✅ *(2026-05-11)*

Final Phase 6 slice. Closes ADR-0005's slice plan and the Phase 6
umbrella. P6.3.b shipped `AIForecastCapability` with `target_amount` /
`target_date` parameters but the `daily_insights` handler only looped
envelopes; sinking funds — which actually *want* a target-relative
"on-track / off-track / ahead" framing — were unwired.

**Storage**:
- New `ShadowTransactionRepository.daily_contribution_series_for_pool(...)`,
  mirror of `daily_spend_series_for_pool` filtering on `amount > 0`
  (inflows). Voided shadow transactions excluded by status filter;
  returns sparse map keyed by date.

**Handler refactor** (`tulip_storage.runner.handlers.daily_insights`):
- New `ForecastRequest` dataclass carries the full forecast context:
  `pool_kind ∈ {"envelope", "sinking_fund"}`, `series`,
  optional `target_amount` / `target_date` / `current_balance`,
  plus the existing pool fields. `ForecasterCallback` is now
  `Callable[[ForecastRequest], Awaitable[str | None]]` — single arg.
- Envelope path: builds `daily_spend_series_for_pool`, calls forecaster
  with `pool_kind="envelope"` and target fields `None`. Same output
  notification shape as before (`kind=forecast`, `entity_type=envelope`).
- **Sinking-fund path** (new): loops `allocation_pools` joined to
  `sinking_funds` where `pool_type=sinking_fund`. Builds
  `daily_contribution_series_for_pool` over 60 days, fetches
  `balance_for_pool` for the current balance, calls forecaster with
  `pool_kind="sinking_fund"` and `target_amount` / `target_date` /
  `current_balance` populated from the row. Writes
  `kind=forecast` notifications with `entity_type=sinking_fund`.
- The `AIForecastCapability` itself doesn't change — its prompt
  already branches on `target_amount/target_date` presence.

**Tests** — +8:
- 5 repo unit tests for `daily_contribution_series_for_pool`: positive
  postings only, sparse return, voided txs excluded, currency filter,
  date range filter.
- 3 handler integration tests: sinking-fund forecaster receives full
  target context, forecaster returning None writes no row,
  no-forecaster baseline means sinking funds produce zero notifications.
- All 4 prior P6.3 / P6.3.b handler tests updated to the new
  callback signature and still pass.

**Out of scope** (deploy-time concern, not a code slice):
- Wiring `AIForecastCapability` into the runner's handler registration
  as the production `forecaster` callback. The slot is in place; what
  remains is one line of glue at deployment to construct the
  capability + adapter + session factory and pass it to
  `make_daily_insights_handler(..., forecaster=...)`.

### P6.5.b — `tulip ai config` editor + `log_prompts` toggle + status polish — ✅ *(2026-05-11)*

Second Phase-6 wind-down slice. Ships the operator surface deferred from
P6.1 + the fallback-semantics callout ADR-0005 §"Negative" #3 calls
out. No migration — everything rides on the existing
`households.ai_policy` JSON column.

**HTTP** (admin-only):
- `GET /v1/ai/config` returns the raw household-level fields
  (`default_provider`, `default_model`, `profile`, `monthly_cost_cap_usd`,
  `cost_cap_behaviour`, `rate_limit_per_hour`, `fallback_provider`,
  `fallback_model`, `log_prompts`) plus a per-capability overrides view.
  For the fully-resolved view, `GET /v1/ai/status` still answers — and
  now includes the same six P6.5.a-related fields plus a `month_to_date_spend_usd`
  field surfaced from `tulip_ai.cost.check_cost_cap` when a cap is configured.
- `PUT /v1/ai/config` accepts a partial patch with the sentinel
  `"__CLEAR__"` (or empty string for the Decimal-typed
  `monthly_cost_cap_usd`) to remove a key. Pydantic `extra="forbid"`
  rejects unknown keys with 422.
- `PUT /v1/ai/config/capabilities/{capability}` patches per-capability
  overrides under `ai_policy.capabilities[capability]`. Unknown
  capability/path values, unknown fields, and out-of-space values
  (`policy`, `profile`) return typed 422s.

**CLI** — new `tulip ai config` sub-typer:
- `tulip ai config show` — table view of household-level + per-capability overrides.
- `tulip ai config set <key> <value>` — whitelisted set with type
  coercion (`log_prompts` accepts true/false/on/off/yes/no,
  `rate_limit_per_hour` parses to int, empty value clears).
- `tulip ai config clear <key>` — convenience wrapper for `set <key> ""`.
- `tulip ai config set-capability <capability> <field> <value>` —
  per-capability override (`policy / provider / model / profile`).
- `tulip ai config log-prompts {on|off}` — convenience wrapper that
  also emits the ADR-0005 §Q6 privacy warning to stderr when toggled on.

**`tulip ai status` polish**:
- Surfaces `cost_cap_behaviour`, `rate_limit_per_hour`,
  `fallback_provider` (+ model), and the month-to-date spend when a cap
  is configured.
- When `fallback_provider` is set, prints the locked
  ARCHITECTURE/ADR §Q8 callout: "applies on cost-cap degrade ONLY.
  Provider 5xx errors do NOT silently fall back."
- When `log_prompts=true`, prints the privacy-vs-forensic-value warning
  inline.

**Schema additions**:
- `AIConfigRead`, `AIConfigCapability`, `AIConfigPatch`,
  `AIConfigCapabilityPatch` in `tulip_api.schemas.ai`.
- `CLEAR_SENTINEL = "__CLEAR__"` (shared between schemas and router).
- `AIStatusRead` extended with the new fields + `month_to_date_spend_usd`
  (backwards-compatible; default `None`).

**Tests** — +20 across two layers:
- API endpoint integration (14): config show defaults, set, clear via
  sentinel, full cost-cap round trip, empty-string clears the cap,
  unknown household-level key 422, invalid behaviour 422, unknown
  capability 422, unknown field 422, set + clear per-capability
  override round-trip, status reflects all P6.5.a fields with
  defaults, status reflects the cap round-trip.
- CLI integration (3): full `set/show/clear/log-prompts` round-trip,
  `status` includes the fallback-cost-cap-only callout, unknown-key
  CLI rejection.

### P6.5.a — Pre-call cost-cap + rate-limit chokepoint — ✅ *(2026-05-11)*

First wind-down slice of Phase 6. Closes the gap from ADR-0005 §Q7:
every capability now consults a shared pre-call gate before issuing the
provider call. The gate enforces the household's monthly cost cap and
each user's sliding-window rate limit, and applies the locked
`cost_cap_behaviour` policy when the cap trips. No migration —
behaviour rides on existing `households.ai_policy` JSON.

**`tulip_ai.cost`** (new module):
- `check_cost_cap(...)` sums the current month's `ai_invocations.cost_estimate_usd`
  for the household (only `success` + `provider_error` rows count —
  capped/disabled rows never reached the wire). Returns
  `CostDecision(kind=allow|cap_exceeded, spent_so_far_usd, cap_usd)`.
- `check_rate_limit(...)` counts `ai_invocations` for `(household_id, user_id)`
  in the last 60 minutes. Default 60/hour. `actor_user_id=NULL` is its own
  bucket so importer-driven calls don't pollute a user's quota and vice
  versa.
- `enforce_pre_call(...)` is the combined gate the capabilities call: rate
  first (no degrade — rate-limit always hard-fails), then cost. On
  `cap_exceeded` with `cost_cap_behaviour=degrade` and a configured
  `fallback_provider`, returns a `PreCallApproval` that swaps
  provider/model. `hard_fail` (or `degrade` without a fallback) returns
  `PreCallBlock(outcome=cost_capped)`.

**Policy plumbing** (`tulip_ai.policy`):
- `ResolvedPolicy.cost_cap_behaviour: Literal["degrade", "hard_fail"]`
  (default `degrade` per ADR §Q7).
- `ResolvedPolicy.rate_limit_per_hour: int` (default `60`,
  positive-int-coerced from `households.ai_policy.rate_limit_per_hour`).

**Capability wiring**: `AICategorizer`, `AINLQueryCapability`,
`AIForecastCapability`, `AIProposalCapability` all call the gate
between policy resolution and `adapter.chat()`. On `PreCallBlock` they
write an `ai_invocations` row stamped with `outcome=rate_limited` or
`outcome=cost_capped` (no provider call) and surface the structured
error to the caller in the way each capability already handles
failures (importer falls back silently, NL query / forecast / suggest
returns an error-shaped result). On a degraded `PreCallApproval`, the
capability calls the swapped provider (typically `ollama`) without the
cloud key and audits `provider=ollama` + `outcome=success`, satisfying
ADR §Q7's "explicit, audited, no silent fallback" rule.

**Tests** — +20 across three layers:
- `cost.py` unit tests (16): under cap allows, at cap blocks, only
  billable outcomes count, previous-month exclusion, sliding window,
  per-user buckets, default 60/hour, system bucket independence,
  `enforce_pre_call` clean approval, rate-first ordering, degrade swap,
  hard-fail block, degrade-without-fallback falls back to block.
- `policy.py` extension tests (5): degrade default, explicit hard_fail,
  garbage falls back to degrade, rate-limit default 60, garbage / zero
  / negative falls back to 60.
- `AICategorizer` integration matrix (3): degrade swap shows
  `provider=ollama` on the adapter call + audit row, hard_fail writes
  `outcome=cost_capped` with no adapter call, rate-limit writes
  `outcome=rate_limited` with no adapter call.

**Out of scope** (P6.5.b/c follow-ups):
- `tulip ai config` JSON editor.
- `log_prompts` toggle.
- `tulip ai status` polish (fallback-semantics callout).
- Sinking-fund forecast extension.

### P6.4.b — AI-driven proposal generation (`AIProposalCapability` + `suggest-budget`) — ✅ *(2026-05-11)*

The "AI as proposal generator" half of P6.4: an asynchronous capability that
takes an envelope + its recent spend series, calls the LLM via the existing
`LitellmAdapter`, parses the structured suggestion, audits the call, and
returns a `ProposedChange` ready for the queue. The HTTP/CLI surface persists
it as a `created_by_kind=ai_agent` proposal linked to the
capability's audit row via `ai_invocation_id` — so when the user approves it
through the standard inbox flow, the locked
[ARCHITECTURE.md §6.2](ARCHITECTURE.md) +
[THREAT_MODEL.md §5.3](THREAT_MODEL.md) chain
(`actor_kind=ai_agent` audit row, `metadata.proposal_id`,
`metadata.proposal_kind`, originating `ai_invocations.id`) all line up.

**Capability** (`tulip_ai.proposals.AIProposalCapability`):
- One method: `suggest_envelope_budget(*, household_id, actor_user_id, api_key,
  envelope_id, envelope_name, currency, current_budget, recent_spend_series)`.
- Prompt asks for a JSON object with `new_budget_amount` (decimal string) +
  `rationale` (string). Tolerates code-fenced JSON; rejects garbage with a
  structured `error` and a `provider_error` audit row.
- Audit row always written (success, parse-fail, provider error, policy
  disabled, no key) — `capability="agentic"`, prompt SHA-256, no prompt
  text per ADR-0005 §Q5.
- Returns `SuggestionResult(proposal: ProposedChange | None, error: str | None)`.
  `ProposedChange` mirrors the `POST /v1/ai/proposals` body shape so the
  router can hand it straight to `PendingProposalRepository.create`.

**HTTP**:
- New `POST /v1/ai/proposals/suggest/budget` (admin / member). Body:
  `{"envelope_id": "<uuid>"}`. Pulls the envelope's last 60 days of spend
  via a new `ShadowTransactionRepository.daily_spend_series_for_pool()`
  writer-side query, decrypts the household's provider key, calls the
  capability, and on success creates a `created_by_kind=ai_agent` proposal
  with the `ai_invocation_id` link. On any capability failure (no key,
  policy disabled, unparseable response), returns
  `{"proposal": null, "error": "<reason>"}` with 200 — the audit row still
  landed, the user just learns why no proposal was queued.

**CLI**: `tulip ai suggest-budget --envelope <UUID>`. On success prints the
new proposal id, rationale, and a hint to approve/reject via the existing
`tulip ai approve` / `tulip ai reject` commands. On structured error, writes
to stderr and exits 1.

**Tests** — +5 capability unit tests + 3 endpoint integration tests + 1 CLI
integration test:
- Capability: happy path returns `ProposedChange` with parsed payload,
  no-key path returns error + `provider_error` audit, unparseable response
  records error + `provider_error` audit, disabled-policy path returns
  error + `policy_disabled` audit, code-fenced JSON tolerated.
- Endpoint: unauthenticated returns 401, unknown envelope returns 404,
  no-key returns 200 with structured error.
- CLI: no-key path exits 1 with the structured error on stderr.

Architecture-test allowlist: `daily_insights.py` already imports
`ScheduledJob` + `ShadowPosting`; the new `daily_spend_series_for_pool`
lives on `ShadowTransactionRepository` so the suggest-budget endpoint
itself stays within the existing repository boundary.

### P6.4 — Agentic proposals + `actor_kind=ai_agent` audit signal — ✅ *(2026-05-11)*

Fourth Phase 6 slice — the architecturally novel one. Establishes the proposal queue, the approve/reject workflow, the `actor_kind=ai_agent` audit pattern locked by [ARCHITECTURE.md §6.2](ARCHITECTURE.md) + [THREAT_MODEL.md §5.3](THREAT_MODEL.md), and one end-to-end executable proposal kind: `envelope_budget_update`. AI-driven proposal *generation* lands as P6.4.b — this slice ships the infrastructure both AI- and human-created proposals share.

**Storage**:
- New `pending_proposals` table per ADR-0005 §Q9. Columns: `kind / title / rationale / payload(JSON) / status / created_by_kind / created_by_user_id / ai_invocation_id / decided_at / decided_by_user_id / decision_note`. Composite PK on `(household_id, id)`; index on `(household_id, status)` for the inbox listing.
- `PendingProposal` model + `ProposalStatus` / `ProposalCreatorKind` enums.
- `PendingProposalRepository`: `create / get / list_by_status / mark_decided` (idempotent on same-status, refuses to transition out of a terminal state).

**Executor pattern**:
- `tulip_api.services.proposal_executor` — a dispatch registry mapping proposal kinds to executor functions. `execute_approved_proposal(session, *, household_id, proposal, decided_by_user_id, request_id)` looks up the kind's executor, runs it, and writes the audit row with `actor_kind=ai_agent` (if proposal was AI-created) or `actor_kind=user` (if human-created). The audit row's `metadata` carries the originating `proposal_id` and `proposal_kind` per the locked ARCHITECTURE §6.2 rule.
- v1 ships one executor: `_execute_envelope_budget_update` updates `envelope.budget_amount` on the targeted envelope. Payload validation rejects malformed input with a typed Problem Details. New kinds: register one function.
- `supported_proposal_kinds()` exposes the allowlist for `GET /v1/ai/proposals/kinds` and the CLI's discoverability.

**HTTP**:
- `POST /v1/ai/proposals` — create. `ai_invocation_id` presence flips `created_by_kind` to `ai_agent` server-side.
- `GET /v1/ai/proposals[?status=...]` — list (default filter `pending`); empty string disables the filter.
- `GET /v1/ai/proposals/kinds` — supported-kinds allowlist for the executor registry.
- `POST /v1/ai/proposals/{id}/approve` — runs the executor, stamps status. Failures (unsupported kind, invalid payload, envelope vanished) leave the proposal PENDING with a typed error.
- `POST /v1/ai/proposals/{id}/reject` — stamps status; idempotent on already-rejected.

New errors: `proposal.not_found` (404), `proposal.already_decided` (409), `proposal.unsupported_kind` (400), `proposal.payload_invalid` (400).

**CLI** (`tulip ai`, four new subcommands): `propose --kind --title --payload <json>`, `proposals [--status]`, `approve UUID [--note]`, `reject UUID [--note]`.

**Tests** — +17 API endpoint tests covering:
- Creator-kind plumbing (user vs ai_agent based on `ai_invocation_id` presence).
- Listing filter behaviour (default pending, empty=all, kinds endpoint).
- Approve happy path (envelope's `budget_amount` actually updated).
- **The locked audit rule**: AI-created proposal → `actor_kind=ai_agent` audit row with `metadata.proposal_id` link. User-created → `actor_kind=user`. Both verified end-to-end through the real `AuditLog` table.
- Approve failure modes: unknown ID (404), already-decided (409), unsupported kind (400), invalid payload (400).
- Reject happy path + idempotency on already-rejected.
- Auth gates on both endpoints.

**Deferred to P6.4.b**:
- AI-driven proposal generation — a capability that takes a "goal" (e.g., "review last month's spending and suggest envelope budget adjustments"), emits structured proposals, and writes them as `created_by_kind=ai_agent`. The infrastructure is ready; just needs the prompt + capability class.
- More executor kinds (`categorize_lines`, `transfer_pools`, etc.) — same registry pattern.

### P6.3.b — AI forecast capability + handler integration — ✅ *(2026-05-11)*

Completes the daily-insights pipeline started in P6.3. The anomaly half stays as-is; this slice adds the AI forecast half per ADR-0005 §Q3.

**New `tulip_ai.forecast`**:

- `bucket_time_series(series, profile)` rounds each amount to the nearest 5% (default) / 25% (strict) of the series' maximum absolute value. `local_only` passes through. Per ADR §Q3 — trend matters, exact amounts don't.
- `ForecastPromptPayload` + `build_forecast_prompt` are the byte-faithful prompt assembler. Strict elides the envelope name (ID-only) per ADR §Q3.
- `AIForecastCapability` — single-turn flow taking `{envelope_name?, time_series, target_amount?, target_date?, recent_inflow_average?}` and returning a `ForecastResult(text, error)`. Same broad-exception guard pattern as `AICategorizer`; one `ai_invocations` row per call.

**Handler wiring**: `make_daily_insights_handler(session_maker, *, forecaster=None)` now accepts an optional `ForecasterCallback` (async callable returning text or `None`). When provided and returning text, the handler writes a `kind=forecast` notification per envelope alongside the existing anomaly rows. `None` preserves the P6.3 anomaly-only behaviour.

**Tests** — +13 new:
- 5 bucketing (default 5%, strict 25%, local_only pass-through, empty, all-zeros).
- 3 prompt build (strict-elides-name, default-includes-name, target fields threaded).
- 3 capability integration (happy path, no-API-key, disabled-policy).
- 2 handler integration (forecaster called writes forecast row, forecaster returning None writes nothing).

**Deferred**:
- The app-factory wiring that constructs the production `AIForecastCapability` and threads the callback into the handler — lands when the runner's scheduled-job seeding adds `daily_insights` alongside the existing `envelope_refill` auto-seed.
- Sinking-fund-on-track variant — same capability shape, different prompt context.

### P6.3 — Daily-insights scheduler + anomaly detector + notifications inbox — ✅ *(2026-05-11)*

Third Phase 6 slice. Establishes the notifications inbox and ships the anomaly half of the daily-insights pipeline. AI forecasting (the other half ADR-0005 §Q3 describes) is deferred to a P6.3 follow-up — the handler has a documented seam where the AI capability plugs in.

**New `tulip_core.insights` (pure-domain)**:

- `find_anomalies(series, window_size=30, threshold_sigma=2)` returns positive-tail anomalies (overspending only — under-spending isn't notification-worthy in v1). Severity is bucketed: `info` (>=2 sigma), `warning` (>=3 sigma), `critical` (>=4 sigma).
- 7 unit + property tests (flat-series-never-flags hypothesis property, spike-detection, severity ladder, negative-tail suppression, validation).

**Storage**:

- New `notifications` table with composite PK + indexes for inbox listing.
- `NotificationKind` / `NotificationSeverity` enums.
- `NotificationRepository` for `create / list_active / list_all / get / dismiss` (dismiss is idempotent).

**Scheduler handler `daily_insights`**:

- Registered via the same `register_handler` seam ADR-0002 ships.
- For each active envelope in the household, builds a 60-day daily-spend series from POSTED shadow postings (outflows only, abs-valued, zero-filled), feeds it to `find_anomalies`, and writes one `notifications` row per detected anomaly with `produced_by="daily_insights"` and `entity_type="envelope"`.
- The AI forecast extension point is documented inline; same loop will call the AI capability and write a `kind=forecast` notification when P6.3.b lands.

**HTTP**: `GET /v1/notifications[?include_dismissed=true]` and `POST /v1/notifications/{id}/dismiss`. Dismiss returns 404 (`notification.not_found`) on unknown ids; idempotent on already-dismissed.

**CLI**: `tulip notifications list [--include-dismissed]` (Rich table, severity colour-coded) and `tulip notifications dismiss UUID`.

**Tests** — +16 new:
- 7 anomaly detector unit + property tests.
- 2 daily-insights handler integration tests (flat-series-no-notifications, spike-produces-anomaly-with-severity).
- 7 API endpoint tests (empty inbox, list active only by default, include-dismissed, dismiss, dismiss 404, dismiss idempotency, auth gate).

**Deferred to P6.3.b** (separate slice):
- `AIForecastCapability` per ADR-0005 §Q3 with 5%/25% amount bucketing and the forecast prompt payload.
- Envelope-runout / sinking-fund-on-track notifications (driven by the AI capability).

### P6.2 — NL query: two-turn flow with model-emitted SQL — ✅ *(2026-05-11)*

Second Phase 6 capability per [ADR-0005 §Q3](adrs/0005-ai-integration.md). Lets a user ask `tulip ai ask "how much did I spend on groceries last month?"` and get a natural-language answer grounded in a real SQL query against their own ledger.

**New `tulip_ai.sql_safety`** — the security boundary for model-emitted SQL:

- `validate_and_rewrite(emitted_sql, household_id)` parses via `sqlglot` (new dep), rejects anything that isn't a single SELECT, requires every table reference to hit the AI-view allowlist (only `ai_view_transactions` in v1), and rewrites each `ai_view_X` reference to a tenant-scoped subquery against the canonical tables. Auto-`LIMIT 100` if the model didn't cap rows. Returns a `SafeSQL(sql, parameters)` ready for `session.execute(text(sql), parameters)`.
- 21 unit tests cover the negative cases (`UPDATE`/`DELETE`/`INSERT`/`DROP`/`ALTER`/`PRAGMA`/`VACUUM`/`ATTACH`/multi-statement scripts/parse errors/raw-table references) and the rewrite contract (alias preserved, tenant predicate present, LIMIT honored vs auto-added, WHERE clauses untouched).

**New `tulip_ai.nl_query`** — `AINLQueryCapability` orchestrates the two-turn flow:

1. **Turn 1** — `{question, schema_card}` → model returns SQL.
2. **Validate + rewrite** via `sql_safety`. Unsafe SQL stamps the audit row `provider_error / unsafe_sql:<reason>` and returns a structured error.
3. **Execute** the rewritten SQL against a fresh session bound to the same DB.
4. **Redact** result rows — descriptions get the same vendor-token redaction as the categorize path; amounts and dates pass through (summary needs real numbers).
5. **Turn 2** — `{question, redacted_rows}` → model summarises.
6. **Audit** — one `ai_invocations` row per turn (chained by `request_id`), always with `prompt_hash`; `prompt_json` opt-in via `households.ai_policy.log_prompts`.

**HTTP**: `POST /v1/ai/ask` (admin / member) takes `{question}` and returns `{summary, rows, sql, error}`. Failures everywhere fall through to a structured `error` field rather than a 5xx — the user is the caller and needs to know if their question couldn't be answered.

**CLI**: `tulip ai ask "..."` prints the summary plus the row count + raw rows on success, or the error on stderr with exit 1.

**Tests** — +24 new:

- 21 `sql_safety` unit tests.
- 3 `AINLQueryCapability` integration tests covering the happy path (real DB execution + audit rows for both turns), unsafe-SQL rejection (audit row stamped with `unsafe_sql:` note), and the description redaction on turn 2 (raw rows returned to user; redacted rows sent to model).
- API endpoint tests already covered the auth path; `TestAsk` exercises the no-key and disabled-policy structured-error responses.

**Deferred**: sample rows in turn 1 (the field is wired; pulling 5 rows from each view is a follow-up because it requires a per-view sampler and the redactor pass on the way out). Additional AI views (`ai_view_envelopes`, `ai_view_accounts`) lands when the categorize/forecast/agentic capabilities need them.

### P6.1 — tulip-ai skeleton + AICategorizer + BYOK CLI/API — ✅ *(2026-05-11)*

The first Phase 6 implementation slice — everything ADR-0005 §Q9 lists for P6.1 ships in one PR.

**New `tulip-ai` workspace package** with seven modules:

- `tulip_ai.adapters` — `ProviderAdapter` Protocol, `LitellmAdapter` (lazy-imports litellm; ~50 MB transitive dep), `RecordingAdapter` (test seam that captures messages without a network call).
- `tulip_ai.audit` — `AIInvocationWriter`, the sole INSERT path for `ai_invocations` rows (chokepoint pattern from ADR-0001). `hash_prompt_payload()` produces the stable SHA-256 hash stored on every row.
- `tulip_ai.categorize` — `AICategorizer` implementing `tulip_core.reconciliation.Categorizer`; opens its own session per call, resolves the household's policy, decrypts the API key, builds the prompt, calls the adapter, parses the response, writes the audit row. Failures (no key, provider error, malformed response, hallucinated account code) fall back to `Imbalance:Unknown` with confidence 0.0 — they never propagate into the importer flow. `build_categorize_prompt()` is the pure-function preview path the CLI calls into.
- `tulip_ai.errors` — `AIError`, `AICapDisabled`, `AIProviderError`, `AIRateLimited`, `AICostCapped`.
- `tulip_ai.policy` — `resolve_policy(household_policy, user_policy, capability) -> ResolvedPolicy`. Household is the floor; user ratchets up but never down (locked in ADR-0005 §Q5).
- `tulip_ai.redaction` — `CategorizePromptPayload`, `PromptRedactor` with `default` / `strict` / `local_only` profiles. Strict redacts vendor names (token-replacement keeping length-≥4 tokens + curated short keepers like `GAS`/`ATM`/`BAR`) and buckets amounts to orders of magnitude; chart of accounts always rides through full.
- `tulip_ai.__init__` re-exports the public surface.

**Storage layer**:

- New `ai_invocations` table (per ADR-0005 §Q6) with `household_id` + `id` composite PK, `actor_user_id`, `capability`, `policy_resolved`, `profile`, `provider`, `model`, `tokens_{in,out}`, `cost_estimate_usd` (Numeric(12,6)), `latency_ms`, `outcome`, `provider_response_id`, `request_id`, `prompt_hash` (SHA-256, always populated), and the opt-in `prompt_json` / `response_text` columns (NULL by default; only stored when `households.ai_policy.log_prompts == true`).
- `households.ai_policy` JSON column with default `{}` (resolver treats empty as "code defaults").
- `households.ai_keys_encrypted` + `users.ai_keys_encrypted` (LargeBinary) — encrypted JSON `{provider: api_key}`, mirrors `users.totp_secret_encrypted`'s encryption flow.

**Architecture test**: `test_architecture_no_api_in_ai.py` bans `tulip_ai` from importing `tulip_api` (mirrors the existing `tulip_importers` no-tulip-ai rule). Dependency direction: core ← storage ← ai ← api.

**HTTP surface** (`/v1/ai/...`, all admin-gated):

- `POST /v1/ai/keys/{provider}` — upload an API key (encrypted server-side).
- `DELETE /v1/ai/keys/{provider}` — remove a key (idempotent).
- `GET /v1/ai/keys` — list providers that have keys; never exposes key bytes.
- `GET /v1/ai/status` — resolved policy for the caller's household.
- `POST /v1/ai/preview` — the byte-faithful redacted prompt body for a synthetic statement line (ADR-0005 §Q4 surface).

**CLI** (`tulip ai`, six subcommands):

- `tulip ai set-key --provider X` (interactive `getpass` or `--key-stdin` for scripts).
- `tulip ai forget-key --provider X`.
- `tulip ai list-keys`.
- `tulip ai status`.
- `tulip ai preview --description ... --amount ...` — shows the exact payload the categorize call would emit.

`config` (the policy editor) deferred to P6.2 — JSON-shape manipulation that's not on the critical path for the e2e flow.

**App-factory wiring**: `_register_ai_categorizer()` runs once at lifespan start, binds an `AICategorizer` to the configured `session_maker` + `LitellmAdapter`, and calls `register_categorizer(...)` — the same DI seam P5.3 ships. Existing import-apply flow now goes through AI when an API key is set; falls back to "Imbalance:Unknown" silently otherwise.

**Tests** — 42 new total:

- `tulip-ai`: 12 redaction tests (3 profiles × ~4 amount/token cases), 12 policy tests (severity ratchet matrix, defaults, provider inheritance, local_only forcing), 4 audit-writer tests, 6 AICategorizer tests (happy path, policy-disabled, no-key, hallucinated code, code-fence parsing, prompt purity).
- `tulip-api`: 8 endpoint tests (key round-trip, status, preview, auth gates).
- `tulip-cli`: 3 subprocess integration tests (key round-trip via stdin, status output, preview JSON).
- `tulip-storage`: 1 new architecture test banning `tulip_api` imports from `tulip_ai`.

No live provider calls anywhere — `RecordingAdapter` captures the messages the categorizer would have sent; the byte-faithful preview tests assert the prompt body's shape.

### P6.0 — Privacy audit + data-flow contract (ADR-0005) — ✅ *(2026-05-11)*

Closed #102. [ADR-0005](adrs/0005-ai-integration.md) is the authoritative design for Phase 6, resolving nine open questions:

1. **Module structure** — new `tulip-ai` package with one-direction dependency on `tulip-core` + `tulip-storage`; never depends on `tulip-api`.
2. **Provider adapter + BYOK** — single `LitellmAdapter` in v1; per-household + per-user API keys field-encrypted via the master-key flow from #132; no fallback to a different household's key.
3. **Per-capability data-flow contract** — explicit tables for what each of the four capabilities (categorize / NL query / forecast / agentic) sends in the prompt body, under `default` vs `strict` redaction profiles.
4. **Redaction profiles + preview** — `PromptRedactor` pure function; `tulip ai preview` CLI surface that's **byte-faithful** to the live call (test-enforced).
5. **Policy resolution** — household policy is the floor; users ratchet up (more cautious) but never down. Severity ordering `disabled > requires_approval > permissive`.
6. **Audit-log shape** — new `ai_invocations` table with writer chokepoint; `prompt_json NULL` by default (operators opt in); `prompt_hash` always populated.
7. **Cost cap + rate limit** — cost cap is *pre-call* reservation; rate limit is per-user sliding window; cap-reached behaviour is `degrade` (to local provider) or `hard_fail`.
8. **Failure modes** — locked "no silent provider fallback" extends to "no silent retry"; the one explicit exception is the cost-cap `degrade` path which audits `provider=ollama`.
9. **Slice ordering** — P6.1 (skeleton + `AICategorizer`) → P6.2 (NL query) → P6.3 (forecast) → P6.4 (agentic) → P6.5 (polish).

Side effects of P6.0:

- [ARCHITECTURE.md §6](ARCHITECTURE.md) now points at the ADR as authoritative.
- [ARCHITECTURE.md §10 audit cadence](ARCHITECTURE.md) marks the privacy audit as ✅ shipped.
- [THREAT_MODEL.md §5.3](THREAT_MODEL.md) constraints are unchanged in spirit but now cross-reference the ADR for the concrete designs that resolve each one.
- The Phase 6 bullet list in ARCHITECTURE.md §10 is replaced with the P6.0–P6.5 slice plan.

No code changes; design-only slice. Implementation begins with P6.1.

---

## Other shipped fixes

### P2.x.4 — catch-all unhandled-exception handler — ✅ *(2026-05-01)*

PR #30. Closed #26. Surfaced during P3.2.a smoke testing when a SQLAlchemy URL parse error escaped the Problem Details middleware and emitted Starlette's default `text/plain` 500. New `InternalServerError` (`server.internal_error`, 500) `TulipProblem` subclass; `install_problem_handlers` registers a fourth handler for the `Exception` base. Exception text and tracebacks stay in logs; clients get a generic detail with a `request_id` for support correlation.

### Balance endpoints: resolve "today" via UTC, not server-local — ✅ *(2026-05-10)*

Closed #141. The runner's `Clock` returns UTC (per ADR-0002 §6, "every other path uses real `datetime.now(UTC)`"); the `envelope_refill` handler stored shadow tx dates as `clock().date()` accordingly. The envelope/sinking-fund balance endpoints, however, defaulted `as_of = date.today()` — server-local. For any negative-offset timezone, between UTC midnight and local midnight the local "today" lags the handler's UTC "today", so a freshly-posted refill was filtered out by the `tx.date <= as_of` predicate. Surfaced as a flake on `test_run_due_executes_handler` between 17:00 PT and 23:59 PT. Fix: three balance-endpoint callsites (`envelopes.py`, `sinking_funds.py`) now use `datetime.now(UTC).date()`; deterministic regression test in `test_refill_schedules_endpoints.py::TestRunDue::test_run_due_balance_uses_utc_today_not_local` pins the local helper to a far-past date so the bug, if reintroduced, fails the test at any wall-clock time.

### README rewrite for users — ✅ *(2026-05-10)*

Closed #139 (Hardening Tier 4 — last item in the pre-internal-beta umbrella). README is now split into a "For users" section (one-paragraph what-it-is, three-bullet what-it-does, 60-second install, what's deliberately deferred from v1) and a "For contributors" section (everything that was there before: workspace layout, uv workflow, just recipes, signed commits, API + CLI surfaces, security posture, license). QUICKSTART is linked from the top callout, the install section, the documentation list, the CLI walkthrough, the `just quickstart-smoke` recipe mention, and the footer. PHASE_STATUS.md and ARCHITECTURE.md are linked only from the contributor section per the issue's acceptance. Added CI / license / Python-version status badges above the fold. Backup / restore / doctor / periods commands added to the inline CLI surface list to match what actually ships.

### `docs/QUICKSTART.md` — ✅ *(2026-05-10)*

Closed #138 (Hardening Tier 4). End-to-end runnable walkthrough from empty machine through install → register → seed accounts → import → reconcile → close period → backup, plus a cookbook section for manual master-key rotation (per locked decision C: rotation is documented procedure, not a CLI command). New `docs/quickstart-fixtures/sample-statement.ofx` ships a realistic 6-line May 2026 statement (`$3611.88` net) that the QUICKSTART's reconcile step balances against; two unit tests pin both the parse and the math so a parser change can't silently break the walkthrough. README's "What is Tulip" section and the `tulip register` success output both point at the new doc. New `just quickstart-smoke` recipe replays the full flow against a fresh compose stack — not a CI gate today (docker-in-docker on the runners is unsupported) but cheap to run locally before merging anything that touches the imports / reconcile / periods / backup surface. The runtime Docker image now bundles `tulip-cli` alongside `tulip-api` so the backup step can run via `docker compose exec -T api tulip backup --out -`; without that, the backup CLI can't reach the in-volume SQLite file.

### Inline balances + refill summary on envelope / sinking-fund list — ✅ *(2026-05-10)*

Closed #137 (Hardening Tier 3). `tulip envelopes list` and `tulip sinking-funds list` previously omitted the live balance to avoid an N+1 fan-out (P4.2 trade-off). Now they batch-fetch via a new endpoint:

- **API**: `POST /v1/pools/balances` — body `{pool_ids: [UUID, ...]}` (max 500 ids); returns `[PoolBalanceRead]` with one row per requested pool that exists in the caller's household. Foreign-tenant ids are silently dropped (matches per-pool lookup semantics). Single SQL query under the hood (`ShadowTransactionRepository.balances_for_pools`).
- **CLI**: both list views now show inline `balance` (right-aligned), and envelopes show a one-line `refill` summary (e.g. `fixed: 100.00 USD`, `target: 500.00 USD`, `pct-inflow: 5%`). `--json` output gets a `balance` key per row. Empty-list short-circuits without an HTTP call.
- **Tests**: 5 API endpoint tests (happy path with non-zero balance, empty pool_ids, unknown ids dropped, foreign-tenant pools invisible, 401 unauth), 6 unit tests for the refill summariser (4 strategies + None + unknown), and 2 CLI integration tests (envelopes show populated balance after refill, sinking funds show 0 balance column).

### `tulip periods` CLI — ✅ *(2026-05-10)*

Closed #136 (Hardening Tier 3). Period soft-close was a storage-layer primitive only; now exposed via `GET /v1/periods`, `POST /v1/periods/{id}/close`, `POST /v1/periods/{id}/reopen` (admin-gated for the mutating routes; idempotent on already-closed/already-open). New `tulip periods {list, close, reopen}` CLI commands consume the endpoints; the existing `period.closed` 400 path on transaction writes is unchanged — this slice only ships the status-flip surface. New `period.not_found` (404) error class. Tests: 10 API endpoint tests (list, close, reopen, idempotency, 404, role-gated, close-blocks-writes, round-trip-unblocks-writes) + 4 CLI subprocess integration tests against `live_api`.

### `tulip doctor` smoke / first-run verification — ✅ *(2026-05-10)*

Closed #135 (Hardening Tier 2). New `tulip doctor` CLI command runs five checks against the configured API and exits 0 / 1 / 2 (locked design decision per the issue: 0 = all good, 1 = warning, 2 = hard failure — overrides the CLI's general-purpose `EXIT_*` constants for this command only):

1. **API reachability** — `GET /health` returns 200.
2. **Master-key loaded** — `master_key_source != "ephemeral"`; ephemeral fallback is a hard failure.
3. **Migration head** — DB's alembic revision matches the head bundled in the running API package; mismatch is a warning ("run `alembic upgrade head`").
4. **Attachment-root writable** — API probe creates and removes a zero-byte file at `attachment_root`.
5. **Token store** — CLI-side check that the token-store path is reachable; missing/empty is a warning, not a failure (user might just need to log in).

Supporting changes:

- New unauthenticated endpoint `GET /v1/system/diagnostics` aggregates probes 2–4 server-side and is consumed by the CLI. Booleans only — no paths or key bytes leak. Auth-free by design so the doctor runs *before* any user has registered.
- New `tulip_storage.migrations_meta.expected_alembic_head()` helper reads the bundled migrations directory via alembic's `ScriptDirectory` so the API can compare DB head to wheel head without invoking the alembic CLI.
- `Settings.master_key_source: Literal["env", "file", "ephemeral"]` tracks which env path produced the key.
- Brief mention added to README; full QUICKSTART integration deferred to [#138](https://github.com/rmwarriner/tulip-accounting/issues/138).

---

## Reference: full phase roadmap

See [ARCHITECTURE.md §10](ARCHITECTURE.md). Phases 0–7 are complete; Phase 8 (operations + hardening) is in progress — the deep security + privacy audits and security/privacy Wave-1 are done. Phase 9 (terminal UI — [ADR-0007](adrs/0007-terminal-ui.md)) is scoped but not started. Phase 10 (pre-cloud preparation + re-audit) follows.

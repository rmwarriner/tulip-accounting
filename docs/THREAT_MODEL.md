# Tulip Accounting — Threat Model Checkpoint

**Status:** lightweight checkpoint, not a deep audit. **Deep audits** are scheduled per the [§10 audit cadence in ARCHITECTURE.md](ARCHITECTURE.md): privacy audit before Phase 6 (AI), deep security audit at Phase 8 (operations + hardening), pre-cloud re-audit at Phase 9.

**Last updated:** 2026-05-07 · `main` @ Phase 5 complete

This document captures what the system protects, what it doesn't, and the constraints Phase 4–6 work must not violate. It exists because the cheap moment to lock in trust boundaries is *before* envelopes / importers / AI add surface area, not after. Tracked as #56.

---

## 1. Trust boundaries

Today's deployment is single-tenant, single-machine, no network exposure beyond localhost. Six trust boundaries exist, in rough order of concern:

1. **CLI process ↔ API process.** Both run as the same user on the same machine. The CLI authenticates with bearer JWTs; refresh tokens live in the OS keyring (or a JSON file when `TULIP_TOKEN_STORE` is set, used only by tests + CI). The API serves on `127.0.0.1:8000` by default; it is not bound to a public interface in any documented setup.
2. **API process ↔ SQLite database.** The DB file is a normal file on disk, owned by the API process's user. Layer 1 SQLCipher full-DB encryption is **deferred** (see [§4](#4-known-deferred-mitigations)); today the database is unencrypted at the filesystem layer.
3. **API process ↔ master key.** `TULIP_MASTER_KEY` (base64-encoded 32 bytes) is read from the environment at startup. If unset, an ephemeral key is generated and a warning is emitted. The key lives in process memory for the lifetime of the process and is on the structlog redaction whitelist (`tulip_api.logging_config:32-46`) so it can't accidentally appear in logs.
4. **API process ↔ user credentials.** Passwords are hashed with argon2id (`tulip_api.auth.passwords`) using OWASP-2024 minimum parameters; PHC-formatted hashes are self-describing, so re-tuning later is a no-op for old hashes. JWT access tokens are 15-minute; refresh tokens are 30-day, opaque, and stored hashed (argon2id) in `sessions`. MFA TOTP secrets (`users.totp_secret_encrypted`) are AES-256-GCM-encrypted with the master key at rest. Recovery codes are argon2id-hashed.
5. **API process ↔ HTTP clients.** Every error is RFC 9457 Problem Details, never raw exception text or tracebacks (enforced by an architecture test, `test_architecture_no_http_exception.py`). The catch-all 500 handler logs the full exception with traceback under structlog context but emits a generic `server.internal_error` body. Pydantic schemas validate every input boundary; schemathesis fuzzes every documented operation in CI.
6. **CLI process ↔ user terminal / editor.** `tulip add --edit` spawns `$VISUAL` / `$EDITOR` / `vi` via `shlex.split`, so shell metacharacters in `$EDITOR` aren't injected. Buffer contents are user-typed transactions, not credentials.

**What is not a trust boundary today** (will become one at the listed phase):

- Network attacker — N/A in single-tenant local deploy. Becomes in-scope at **Phase 9** (cloud + multi-tenant).
- Cross-household actor — composite FKs make cross-tenant FK references impossible, but a SQLAlchemy query event listener that filters reads with an `admin_scope()` escape hatch is **deferred from Phase 1**. Today, tenant isolation rests on (a) composite FKs at the schema level and (b) repositories that always require a `household_id` in the constructor. See [§4](#4-known-deferred-mitigations).
- Browser-based attacker — no web UI ships in v1. Reports + journal export are Phase 7+; until then, no DOM, no XSS surface.
- AI provider — no `tulip-ai` package wired yet. **Phase 6** is the inflection point; see [§5](#5-constraints-for-phase-46-work).

## 2. Data classification

Five tiers, highest to lowest confidentiality:

| Tier | Data | Where it lives today | Notes |
|---|---|---|---|
| **Critical** | Master key, password hashes, TOTP secrets, recovery codes, JWT secret | env / process memory; `users.password_hash`; `users.totp_secret_encrypted`; `mfa_recovery_codes.code_hash`; `Settings.jwt_secret` | TOTP secrets are the only field-level-encrypted item today. Passwords + recovery codes are argon2id-hashed (one-way, not encrypted). Master key + JWT secret are in process memory only. |
| **High** | Account balances, transaction descriptions, transaction references, account codes/names, audit log entries | unencrypted SQLite columns | This is "the ledger" — the actual financial data. Nothing about it is encrypted at rest today (Layer 1 SQLCipher deferred). Whoever can read the DB file reads the ledger. |
| **High (integrity)** | `audit_log` rows | unencrypted SQLite | Append-only via `AuditLogWriter` (single chokepoint). Confidentiality is medium; integrity is high — a tampered audit log invalidates forensics. No DB-level immutability yet (deferred to the Postgres phase per ARCHITECTURE.md §1.1). |
| **Medium** | Email addresses, household / display names, periods, account hierarchy structure | unencrypted SQLite | PII-ish but largely user-chosen labels. Logging redaction list (`logging_config.py`) keeps emails out of logs by default. |
| **Highest (Phase 6)** | AI prompt bodies (proposed transactions, NL queries, household financial context fed to LLMs) | not yet — `tulip-ai` doesn't exist | This is why the privacy audit is pinned to **before Phase 6** rather than Phase 8 — Phase 6 is where data starts leaving the local boundary. |

Classification informs constraints in [§5](#5-constraints-for-phase-46-work).

## 3. Threat actors and attack surface (current)

Single-tenant local deployment scopes the actor list down hard:

### In-scope

- **Local attacker with filesystem read access** to the API process's user account. Wins immediately on the ledger (DB is unencrypted at rest); wins on TOTP secrets only if they also have the master key. Wins on past tokens if they read the keyring database.
- **Local attacker with process memory access** (e.g. via a debugger or `/proc/$pid/mem` on Linux). Has the master key, the JWT secret, and any decrypted TOTP secrets currently being verified. This is "you've already lost" territory; the only mitigation is OS-level (no untrusted users on the same machine).
- **Misbehaving CLI client** — well-formed authenticated requests with malformed payloads. Covered by Pydantic at the schema boundary, schemathesis fuzzing in CI, and the architecture test that bans raw `HTTPException` (so error responses can't leak internals).
- **Stolen refresh token** — 30-day window. The CLI keeps refresh tokens in the OS keyring by default; an attacker with keyring access has 30 days of mint-new-access-tokens until either the token expires or the user runs `tulip auth logout` (which calls `/v1/auth/logout` to revoke the refresh token at the API).

### Out of scope today (becomes in-scope at the listed phase)

- **Network attacker** — single-tenant local. **Phase 9.**
- **Cross-household attacker** — single-tenant local. **Phase 9** (cloud), but the design already has composite FKs to make this safe-by-construction.
- **Compromised AI provider / prompt injection / model exfiltration** — no AI in flight. **Phase 6.**
- **Compromised import source** — Phase 5 shipped 2026-05-07. Now **in scope**: see [§5.2](#52--phase-5-importers--reconciliation) for the constraints that landed (size cap, parser hardening, encrypted attachment storage).
- **Stolen attachment / external-document exposure** — Phase 5 wired the encrypted attachment store. Now **in scope**; field-level AES-256-GCM via the master key per ARCHITECTURE.md §7.4 Layer 3.

## 4. Known-deferred mitigations

These were considered and intentionally deferred. Documented here so future audits don't waste cycles rediscovering them, and so future phases don't accidentally rely on a mitigation that isn't actually implemented.

| Item | Status today | What compensates | Tracked at |
|---|---|---|---|
| **SQLCipher full-DB encryption** (Layer 1) | Not wired. DB file is plaintext on disk. | Filesystem permissions; the deployment story (Phase 8) is single-machine home server with locked-down user. | ARCHITECTURE.md §1.3 / §7.4. Lands behind a separate engine factory; not blocking. |
| **Per-field DEK wrapping** | `encrypt_field` uses the master key directly. One key compromise = all fields. | Single field encrypted today (TOTP secrets); blast radius is bounded. The `encrypt_field` API is stable across the future change. | ARCHITECTURE.md §7.4 (deferred from Phase 1). |
| **SQLAlchemy tenant-scoping query event listener** with `admin_scope()` escape hatch | Not implemented. | Composite FKs make cross-tenant *FK references* impossible at the schema level. Repositories require `household_id` at construction. Both of these are tested. | ARCHITECTURE.md §3.3 + §1.3. |
| **Rate limiting** (`slowapi`) | Installed as a dependency, not wired. | Single-tenant local deploy; no public surface. Auth endpoints have no aggressive-bruteforce mitigation yet. | ARCHITECTURE.md §7.6. |
| **WebAuthn / passkeys** as an MFA option | Not implemented. TOTP + recovery codes only. | TOTP is fully wired (`P2.x.1`). Adding WebAuthn later doesn't break TOTP. | ARCHITECTURE.md §12 (deferred). |
| **OS-level audit log immutability** | App-level append-only writer, no DB-level enforcement. | Single `AuditLogWriter` chokepoint; no other code path mutates `audit_log`. An architecture test could enforce this — currently it does not. | ARCHITECTURE.md §1.3. |
| **OpenTelemetry** | Hooks installed, off by default. | Structured JSON logs (structlog) carry the same context. | ARCHITECTURE.md §1.3 / §7.2. |
| **KMS integration for the master key** | `TULIP_MASTER_KEY` env var (or ephemeral fallback with warning). | Standard process-env practice. Phase 9 lifts this to KMS. | ARCHITECTURE.md §7.4. |
| **Pluggable token-store backends** (1Password CLI, `pass`) | Keyring + JSON file backends only. | Real users get keyring; tests get JSON. | #28. |

If you find a "missing" security control during a future audit, **check this table first** — if it's listed here, the audit's job is to *re-evaluate the deferral*, not to surprise-flag the absence.

## 5. Constraints for Phase 4–6 work

Rules that must hold for the work that's about to happen. Each one references where it bites if violated.

### 5.0 — All phases

- **Tenancy.** Every new model gets a composite `(household_id, id)` PK and composite FKs to its parents. Every new repository takes `household_id` in its constructor. No code path may construct a query without a `household_id` filter. Violating this wedges multi-tenant safety until the deferred event listener lands.
- **RFC 9457 errors.** No raw `HTTPException(detail=str)`. New error classes inherit `TulipProblem`. An architecture test (`test_architecture_no_http_exception.py`) fails CI on regression.
- **Audit log.** Every business mutation writes an `audit_log` row via `AuditLogWriter`. No other writer path. Append-only, in spirit if not yet enforced at the DB level.
- **Logging redaction.** Sensitive fields land on the structlog whitelist in `tulip_api.logging_config`. New sensitive fields (e.g. envelope linkages with personal categories, importer source URLs with credentials) get redacted on day 1.

### 5.1 — Phase 4 (envelopes + sinking funds)

- **Envelopes are tenant-scoped, not user-scoped.** A household member can see all shared envelopes; private envelopes follow the same `_filter_for_role` pattern as private accounts.
- **Refill rules don't execute arbitrary expressions.** The `refill_rule` JSON field (per ARCHITECTURE.md §5.3) is a structured shape, not a code-eval surface. Audit any future "expression-y" field for the same constraint.
- **No new uniqueness constraints on user-chosen labels** without considering "same label across households" — this bit us once already in P2.x.3 (login mismatched on email-not-unique-across-households).

### 5.2 — Phase 5 (importers + reconciliation) — ✅ shipped

Phase 5 closed 2026-05-07. The constraints that were locked at Phase 5 entry, and how they landed:

- **Importers handle untrusted input.** OFX / QIF / CSV parsers all run under explicit size + content-type guards in `tulip_api.routers.imports.upload_import`:
  - **25 MB upload cap** (`MAX_OFX_BYTES` constant) checked before slurping into memory; `413 request.payload_too_large` on overflow. Real bank statements are well under 1 MB; the cap is generous and bounds worst case.
  - **Content-type allow-list** per format (OFX accepts `application/x-ofx`, `application/octet-stream`, `text/xml`, `application/xml`; QIF / CSV similar) checked before parsing; `415 request.unsupported_media_type` otherwise.
  - **OFX**: `ofxtools` (chosen over `ofxparse` for active maintenance + XXE safety; uses `defusedxml` under the hood). XXE rejection covered by `tulip-importers/tests/test_ofx_security.py`.
  - **QIF**: hand-rolled line-oriented parser; no XML attack surface.
  - **CSV**: `csv.DictReader` with a per-household `CsvProfile` (Pydantic v2, YAML round-trip via `yaml.safe_load` only — `yaml.load` is banned by an architecture test). Profile uploads cap at 100 KB.
  - Empty-body inputs surface a typed problem (`import.ofx_parse_failed` / `qif_parse_failed` / `csv_parse_failed`) rather than crashing.
- **Statement attachments live in the encrypted attachment store** (ARCHITECTURE.md §7.4 Layer 3). `AttachmentRepository.create` writes the encrypted blob to `Settings.attachment_root`; the master key wraps the per-attachment DEK; the file on disk is never plaintext. Content-hash dedup (`ix_attachments_hash`) ensures one Attachment row per unique bytes per household — a duplicate upload re-uses the existing attachment row.
- **Filename safety.** `source_filename` is stored verbatim (display only); the actual on-disk path is content-hash-derived, so user-supplied filename never participates in path resolution. No path-traversal surface.
- **Transaction void / reversal** (#55) shipped as P5.0, ahead of the reconciliation flow that depends on it. The un-reconcile path (`DELETE /v1/reconciliations/{id}?cascade=true`) routes through `ReconciliationRepository.revert()`, the architecture-test-enforced single chokepoint for `transactions.reconciliation_id` + `reconciled_at` + `carried_forward_from_reconciliation_id` writes.
- **`?force=true` upload override** (#114, ADR §Q6, PR #130) intentionally bypasses the same-file/same-account duplicate check. The audit log records `"force": true` in the `after_snapshot` so the admin trail is honest about the duplicate. Idempotency lookup is application-level; the underlying index is a query accelerator, not a constraint.
- **Reconciliation is single-IN_PROGRESS-per-account** by the locked P5.4.b decision (`ReconciliationAccountAlreadyInProgressError`, 409). Simplifies the matcher's "already-reconciled" detection; closes a class of double-match bugs that would otherwise be possible if two reconciliations were open simultaneously.

What is **out of scope** of the Phase 5 threat model and explicitly deferred:

- **Attachment download endpoint** — there's no `GET /v1/imports/{id}/attachment` yet. Statement bytes can be re-uploaded but not retrieved through the API. If/when that endpoint lands, range-request denial-of-service + content-disposition smuggling become in-scope concerns.
- **Multi-currency reconciliation** — every Phase 5 endpoint asserts `account.currency == reconciliation.currency`; multi-currency is silently out of scope. Lifting this requires FX rate engine work first (per ARCHITECTURE.md §1.3 deferred items).
- **Partial-of-one matches** (a $100 statement line matching $60 of a $100 ledger tx with $40 residual) — explicitly rejected for v1 per ADR-0004 §Q3. Manual match enforces `match_amount == line.amount`.

### 5.3 — Phase 6 (AI integration)

This is the privacy inflection point and gets its own audit slot **before implementation begins**. Constraints to write into Phase 6's entry criteria:

- **Prompt bodies are not logged by default.** Only metadata (model, latency, cost, tenant, user, success/fail). Tenant-level opt-in via `households.ai_policy` (see ARCHITECTURE.md §6.5).
- **Redaction runs before the litellm call**, not after. PII-redaction policy is auditable as a separate function.
- **No silent provider fallback.** If the configured provider fails, the AI call fails — no implicit failover to another provider that might have a different data policy.
- **`actor_kind=ai_agent` audit rows** for every state-changing AI proposal that's approved, with a link to the originating proposal. ARCHITECTURE.md §6.4 specifies this; this constraint is the reminder.
- **Cost / rate caps are enforced server-side**, not in the prompt or in the model. Phase 6 doesn't trust the model to self-limit.

## 6. Out of scope (explicitly)

These are real concerns, but not for this checkpoint:

- **Penetration testing** — Phase 8.
- **Cryptographic review** of `encrypt_field` (key derivation, nonce reuse risk, AEAD usage details) — Phase 8.
- **Multi-tenant cloud threat model** — Phase 9.
- **Privacy audit of AI flows** — before Phase 6 (separate slice, not yet scheduled because Phase 6 isn't imminent).
- **Backup/restore threat model** — backup pipeline doesn't exist yet (Phase 8).
- **Supply chain / SBOM** — Phase 8 audit covers this.
- **Side-channel / timing attacks** — out of v1 scope; relevant only to multi-tenant cloud (Phase 9).

---

## References

- [ARCHITECTURE.md §3.3 Tenancy Model](ARCHITECTURE.md)
- [ARCHITECTURE.md §7.4 Encryption at Rest](ARCHITECTURE.md)
- [ARCHITECTURE.md §10 Audit Cadence](ARCHITECTURE.md)
- [PHASE_STATUS.md](PHASE_STATUS.md)
- Issue #56 (this checkpoint), #55 (transaction void → Phase 5), #28 (token-store backends), #44 (multi-currency parents).

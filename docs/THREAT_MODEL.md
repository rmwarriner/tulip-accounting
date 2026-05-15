# Tulip Accounting — Threat Model Checkpoint

**Status:** lightweight checkpoint, not a deep audit. **Deep audits** are scheduled per the [§10 audit cadence in ARCHITECTURE.md](ARCHITECTURE.md): privacy audit before Phase 6 (AI) ✅ shipped as [ADR-0005](adrs/0005-ai-integration.md); deep security audit at Phase 8 (operations + hardening) ✅ shipped as [`docs/audits/2026-05-12-deep-security-audit.md`](audits/2026-05-12-deep-security-audit.md), plus a full-system deep privacy audit ✅ shipped as [`docs/audits/2026-05-13-deep-privacy-audit.md`](audits/2026-05-13-deep-privacy-audit.md); pre-cloud re-audit at Phase 10. (Phase 9 is the terminal UI — [ADR-0007](adrs/0007-terminal-ui.md); the pre-cloud phase renumbered 9→10 when it was inserted.)

**Last updated:** 2026-05-14 · `main` @ Phase 8 in progress — deep security + deep privacy audits shipped; security + privacy Wave-1 hardening follow-ups landed

This document captures what the system protects, what it doesn't, and the constraints Phase 4–8 work must not violate. It exists because the cheap moment to lock in trust boundaries is *before* envelopes / importers / AI / reports add surface area, not after. Tracked as #56.

**Phase 7 surface check:** the new `/v1/reports/*` endpoints and `/v1/journal/{export,import}` reuse the existing tenant-scoped session + JWT chokepoint — they introduce no new trust boundary, no new external network dependency at runtime (weasyprint is a build-time system lib for PDF), and no new credential store. The `custom-query` report is gated by the same SQL-safety pass that backs the AI NL-query capability (ADR-0005); writes, joins outside the AI-view allowlist, and non-allowlisted table reads are all rejected with `report.unsafe_query`. Journal import lands transactions as PENDING through `TransactionRepository.save_balanced`, the same chokepoint the OFX / QIF / CSV importers use.

**Phase 8 status:** the deep security and deep privacy audits ran against `main` at Phase 7 complete (see the two audit docs linked above). Both are document-only — **0 Critical / 8 High** security findings, **1 Critical / 17 High** privacy findings. A first wave of hardening follow-ups has since landed: `slowapi` rate limiting on `/v1/auth/*`, single-use MFA-challenge JWTs (`used_mfa_challenges`), an 80-bit recovery-code entropy floor, a constant-time login path, structlog email/IP redaction on by default, and GDPR Art. 17 right-to-erasure endpoints for users and households. §4 and §6 below reflect the post-Wave-1 state; the audit docs remain the authoritative finding-by-finding record and track the still-open items.

---

## 1. Trust boundaries

Today's deployment is single-tenant, single-machine, no network exposure beyond localhost. Six trust boundaries exist, in rough order of concern:

1. **CLI process ↔ API process.** Both run as the same user on the same machine. The CLI authenticates with bearer JWTs; refresh tokens live in the OS keyring (or a JSON file when `TULIP_TOKEN_STORE` is set, used only by tests + CI). The API serves on `127.0.0.1:8000` by default; it is not bound to a public interface in any documented setup.
2. **API process ↔ SQLite database.** The DB file is a normal file on disk, owned by the API process's user. Layer 1 SQLCipher full-DB encryption is **deferred** (see [§4](#4-known-deferred-mitigations)); today the database is unencrypted at the filesystem layer.
3. **API process ↔ master key.** `TULIP_MASTER_KEY` (base64-encoded 32 bytes) is read from the environment at startup. If unset, an ephemeral key is generated and a warning is emitted. The key lives in process memory for the lifetime of the process and is on the structlog redaction whitelist (`tulip_api.logging_config:32-46`) so it can't accidentally appear in logs.
4. **API process ↔ user credentials.** Passwords are hashed with argon2id (`tulip_api.auth.passwords`) using OWASP-2024 minimum parameters; PHC-formatted hashes are self-describing, so re-tuning later is a no-op for old hashes (`needs_rehash` fires on next successful login per #224). JWT access tokens are 15-minute; refresh tokens are 30-day, opaque (256-bit `secrets.token_urlsafe`) and stored as **SHA-256** in `sessions` — full token entropy makes a slow KDF unnecessary, and the deterministic hash lets the revoke-on-logout lookup hit an index rather than scanning rows (see `tulip_api.auth.tokens:97-99`). MFA TOTP secrets (`users.totp_secret_encrypted`) are AES-256-GCM-encrypted with the master key at rest. Recovery codes are argon2id-hashed with an 80-bit entropy floor (Phase 8 Wave-1). The MFA-login flow issues a short-lived MFA-challenge JWT carrying a single-use `jti`; the `used_mfa_challenges` table burns the `jti` on redemption, so a captured challenge token can't be replayed (Phase 8 Wave-1).
5. **API process ↔ HTTP clients.** Every error is RFC 9457 Problem Details, never raw exception text or tracebacks (enforced by an architecture test, `test_architecture_no_http_exception.py`). The catch-all 500 handler logs the full exception with traceback under structlog context but emits a generic `server.internal_error` body. Pydantic schemas validate every input boundary; schemathesis fuzzes every documented operation in CI. The four most abuse-exposed `/v1/auth/*` endpoints (`login`, `login/mfa`, `login/recover`, `refresh`) sit behind per-IP `slowapi` quotas; exceedance is an RFC 9457 `auth.rate_limited` (429) with a `Retry-After` header (Phase 8 Wave-1).
6. **CLI process ↔ user terminal / editor.** `tulip add --edit` spawns `$VISUAL` / `$EDITOR` / `vi` via `shlex.split`, so shell metacharacters in `$EDITOR` aren't injected. Buffer contents are user-typed transactions, not credentials.

**What is not a trust boundary today** (will become one at the listed phase):

- Network attacker — N/A in single-tenant local deploy. Becomes in-scope at **Phase 10** (cloud + multi-tenant).
- Cross-household actor — composite FKs make cross-tenant FK references impossible, but a SQLAlchemy query event listener that filters reads with an `admin_scope()` escape hatch is **deferred from Phase 1**. Today, tenant isolation rests on (a) composite FKs at the schema level and (b) repositories that always require a `household_id` in the constructor. See [§4](#4-known-deferred-mitigations).
- Browser-based attacker — no web UI ships in v1. Reports + journal export are Phase 7+; until then, no DOM, no XSS surface.
- AI provider — no `tulip-ai` package wired yet. **Phase 6** is the inflection point; see [§5](#5-constraints-for-phase-46-work).

## 2. Data classification

Refreshed against the deep privacy audit (2026-05-13) findings H-17 +
M-3..M-5. Confidentiality tiers, highest to lowest:

| Tier | Data | Where it lives today | Multi-presence (count of surfaces) | Notes |
|---|---|---|---|---|
| **Critical** | Master key, password hashes, TOTP secrets, recovery codes, JWT secret | env / process memory; `users.password_hash`; `users.totp_secret_encrypted`; `mfa_recovery_codes.code_hash`; `Settings.jwt_secret` | 1 each | TOTP secrets are field-level-encrypted. Passwords + recovery codes are argon2id-hashed (one-way). Master key + JWT secret are in process memory only. |
| **Critical (free-text content)** | User-typed free text: `transactions.description`, `.reference`, `.notes_encrypted`; `postings.memo`; `accounts.notes_encrypted`; `allocation_pools.name`; `pending_proposals.decision_note` / `.rationale`; `notifications.body`; `shadow_transactions.description`; `shadow_postings.memo`; `statement_lines.description` / `.counterparty` | unencrypted SQLite columns (except `*_encrypted` fields under master-key AES-256-GCM) | 12 columns across 8 tables | **Inference risk under GDPR Art. 9(1).** A user typing `"Payment to Planned Parenthood — $40"` introduces special-category data (health) by inference; `"Tithe to <church>"` does the same for religion. No content classifier today; minimisation is the only mitigation. Free-text fields *cannot* be tier-classified ahead of time — they take whatever tier the user's input pushes them to. |
| **Critical (when populated)** | AI prompt bodies + response text | `ai_invocations.prompt_json` (NULL unless `households.ai_policy.log_prompts=true`); `ai_invocations.response_text` (same gate) | 1 column each | Per ADR-0005, opt-in retention only; default is NULL. Withdrawing consent atomically scrubs every existing row in the same commit as the policy flip (#243); consent-changed audit row (#247) records the toggle. |
| **High** | Account balances, account codes/names, period dates, account hierarchy structure, audit log entries (confidentiality) | unencrypted SQLite columns | derived from postings | This is "the ledger." Nothing encrypted at rest today (Layer 1 SQLCipher deferred). Whoever reads the DB file reads the ledger. |
| **High (pseudonymous)** | `ai_invocations.prompt_hash` (SHA-256 over redacted prompt); `audit_log.actor_user_id` + `entity_id` for deleted users (Art. 17(3)(e) carve-out) | unencrypted SQLite | always populated | Different retention curve from the prompt bodies — the hashes survive consent withdrawal so "was the same prompt sent twice" stays answerable. The 90-day `AI_INVOCATION_RETENTION_DAYS` TTL bounds long-term accumulation. |
| **High (PII identifiers)** | Email addresses | `users.email`; embedded in `audit_log.metadata_` rows (`login_failed`, `register`) | 2 surfaces | GDPR Art. 4(1) — "an identified or identifiable natural person." Redacted in structlog by default (#220). Scrubbed from `audit_log` on user erasure (#235). **Previously misclassified as Medium.** |
| **High (online identifiers)** | IP address, user agent | `sessions.ip_address`, `sessions.user_agent`; `audit_log.ip_address`, `audit_log.user_agent` | 2 tables × 2 columns | GDPR Recital 30 names IP as a personal identifier. Captured on every auth event (nine sites in `routers/auth.py`). Redacted in structlog (#246). At-rest in DB stays full precision; scrubbed from `audit_log` on user erasure (#235). **Previously missing from the table.** |
| **High (integrity, mixed confidentiality)** | `audit_log.before_snapshot` / `audit_log.after_snapshot` | unencrypted SQLite (JSON) | 2 columns | **Integrity is High** (single `AuditLogWriter` chokepoint, no other mutator). **Confidentiality inherits the highest field tier of the snapshot's content** — `description_rectified` rows embed Critical free-text, `profile_updated` rows embed High-PII emails, etc. Treat as Critical-when-populated. Nulled on user erasure via the redaction pass (#235). **Retention: policy-driven tiers** — see ARCHITECTURE §7.2; default 7y for ledger mutations, 90d for auth events, 30d for AI consent / capability rows (#245). |
| **Medium** | Household / display names | `households.name`, `users.display_name` | 1 each | User-chosen labels, low inference risk on their own, but flow through into `audit_log` snapshots which then inherit. |

### 2.1 Explicitly absent categories

Tulip does **not** structure-collect any of the following. A future audit
that flags their absence is wasting cycles — minimisation is the design.

- **Date of birth, age, age range.** Not collected at registration or
  during use. The user's `created_at` is platform-side metadata, not
  birth-related.
- **Phone numbers, postal addresses.** Not collected. Statement-line
  imports may contain merchant addresses but that's *merchant* data,
  not subject data.
- **Government IDs** (SSN, tax ID, passport, driver's licence). Not
  collected. Operators who want to record one are doing it in
  free-text fields and inherit the Critical (free-text) tier.
- **Gender, sexual orientation, racial or ethnic origin.** Not
  collected. GDPR Art. 9(1) special category; absent by design.
- **Biometric or genetic data.** Not collected. Art. 9(1) special
  category; absent by design.
- **Health data.** Not collected. Art. 9(1) special category; the
  Critical (free-text) tier acknowledges the *inference* risk a user
  could introduce by typing it, which is qualitatively different from
  structure-collecting it.
- **Children's data (under-13 / under-16 depending on jurisdiction).**
  Tulip has no minor-targeted features; the registration flow assumes
  an adult operator. There is no age-verification gate.
- **Geolocation beyond IP.** GPS, cell-tower triangulation, etc. not
  collected. IP is the only location-adjacent signal (see High (online
  identifiers) above).
- **Behavioural advertising profiles.** Not collected, not derived,
  not transmitted to advertising third parties. The AI provider call
  is the only outbound — see §5.3 + ADR-0005.

If any of these become relevant for a future capability, the
classification table is updated *before* the migration that introduces
the column lands.

### 2.2 Multi-presence and the deletion-cascade chain

The Multi-presence column is load-bearing for the right-to-erasure
implementation (Wave-1 #235): a field that lives in eight surfaces
needs eight cascade rules. Today the load-bearing footprints are:

- `transactions.description` and the rectified copies it produces:
  the source row, the void reversal's quoted copy (rewritten to
  `[redacted]` on rectification per #242), the user's exported JSON
  (#241), the audit-log before/after snapshots for both create and
  rectify, and the journal export. Eight surfaces total when all are
  populated; the rectification + erasure paths together drain the
  ones the controller controls.
- `users.email` flows into `audit_log.metadata_` for `register` and
  `login_failed`; the user-erasure path nulls those snapshots in the
  same commit as the row delete.
- `ai_invocations.prompt_json` is opt-in; the consent-withdrawal scrub
  (#243) is its erasure path.

The audit `2026-05-13-deep-privacy-audit.md §6` "Data-subject rights
gap analysis" is the canonical map; this section just calls out the
cascade dimension classification doesn't otherwise expose.

Classification informs constraints in [§5](#5-constraints-for-phase-46-work)
and the operator-facing commands map in [USER_RIGHTS.md](USER_RIGHTS.md).

## 3. Threat actors and attack surface (current)

Single-tenant local deployment scopes the actor list down hard:

### In-scope

- **Local attacker with filesystem read access** to the API process's user account. Wins immediately on the ledger (DB is unencrypted at rest); wins on TOTP secrets only if they also have the master key. Wins on past tokens if they read the keyring database.
- **Local attacker with process memory access** (e.g. via a debugger or `/proc/$pid/mem` on Linux). Has the master key, the JWT secret, and any decrypted TOTP secrets currently being verified. This is "you've already lost" territory; the only mitigation is OS-level (no untrusted users on the same machine).
- **Misbehaving CLI client** — well-formed authenticated requests with malformed payloads. Covered by Pydantic at the schema boundary, schemathesis fuzzing in CI, and the architecture test that bans raw `HTTPException` (so error responses can't leak internals).
- **Stolen refresh token** — 30-day window. The CLI keeps refresh tokens in the OS keyring by default; an attacker with keyring access has 30 days of mint-new-access-tokens until either the token expires or the user runs `tulip auth logout` (which calls `/v1/auth/logout` to revoke the refresh token at the API).
- **Online credential / MFA brute-force** — a local attacker who knows a valid email guessing the password, TOTP code, or recovery code (post-Phase-10, also a network attacker). The Phase 8 security audit flagged this as the one place the localhost assumption is thin — a multi-user household on a shared machine is a v1 use case. Wave-1 follow-ups bound it: per-IP `slowapi` quotas on `/v1/auth/*`, the single-use MFA-challenge `jti`, the 80-bit recovery-code entropy floor, a constant-time login path (closes the user-enumeration timing oracle), and failed-login audit rows for forensics.

### Out of scope today (becomes in-scope at the listed phase)

- **Network attacker** — single-tenant local. **Phase 10.**
- **Cross-household attacker** — single-tenant local. **Phase 10** (cloud), but the design already has composite FKs to make this safe-by-construction.
- **Compromised AI provider / prompt injection / model exfiltration** — Phase 6 shipped 2026-05-11. Now **in scope**: see §5.3 for the realised constraints (no-logging default, server-side redaction, no-silent-fallback, `actor_kind=ai_agent` audit chain, pre-call cost + rate gates).
- **Compromised import source** — Phase 5 shipped 2026-05-07. Now **in scope**: see [§5.2](#52--phase-5-importers--reconciliation) for the constraints that landed (size cap, parser hardening, encrypted attachment storage).
- **Stolen attachment / external-document exposure** — Phase 5 wired the encrypted attachment store. Now **in scope**; field-level AES-256-GCM via the master key per ARCHITECTURE.md §7.4 Layer 3.

## 4. Known-deferred mitigations

These were considered and intentionally deferred. Documented here so future audits don't waste cycles rediscovering them, and so future phases don't accidentally rely on a mitigation that isn't actually implemented.

| Item | Status today | What compensates | Tracked at |
|---|---|---|---|
| **SQLCipher full-DB encryption** (Layer 1) | Not wired. DB file is plaintext on disk. | Filesystem permissions; the deployment story (Phase 8) is single-machine home server with locked-down user. | ARCHITECTURE.md §1.3 / §7.4. Lands behind a separate engine factory; not blocking. |
| **Per-field DEK wrapping** | `encrypt_field` uses the master key directly. One key compromise = all fields. | Single field encrypted today (TOTP secrets); blast radius is bounded. The `encrypt_field` API is stable across the future change. | ARCHITECTURE.md §7.4 (deferred from Phase 1). |
| **SQLAlchemy tenant-scoping query event listener** with `admin_scope()` escape hatch | Not implemented. | Composite FKs make cross-tenant *FK references* impossible at the schema level. Repositories require `household_id` at construction. Both of these are tested. | ARCHITECTURE.md §3.3 + §1.3. |
| ~~**Rate limiting** (`slowapi`)~~ | **Shipped — Phase 8 Wave-1.** Per-IP quotas on the four `/v1/auth/*` abuse surfaces; `auth.rate_limited` (429). | — | Re-promoted from deferral by the deep security audit (H-4). |
| **WebAuthn / passkeys** as an MFA option | Not implemented. TOTP + recovery codes only. | TOTP is fully wired (`P2.x.1`). Adding WebAuthn later doesn't break TOTP. | ARCHITECTURE.md §12 (deferred). |
| **OS-level audit log immutability** | App-level append-only writer, no DB-level enforcement. | Single `AuditLogWriter` chokepoint; no other code path mutates `audit_log`. An architecture test could enforce this — currently it does not. | ARCHITECTURE.md §1.3. |
| **OpenTelemetry** | Hooks installed, off by default. | Structured JSON logs (structlog) carry the same context. | ARCHITECTURE.md §1.3 / §7.2. |
| **KMS integration for the master key** | `TULIP_MASTER_KEY` env var (or ephemeral fallback with warning). | Standard process-env practice. Phase 10 lifts this to KMS. | ARCHITECTURE.md §7.4. |
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
- **`?force=true` upload override** (#114, ADR §Q6, PR #130) intentionally bypasses the same-file/same-account duplicate check. **Admin-only** per #230 — a `member` caller is rejected with `403 auth.forbidden` so the audit row's `force=true` flag stays attributable to a deliberate admin action. The audit log records `"force": true` in the `after_snapshot` so the trail is honest about the duplicate. Idempotency lookup is application-level; the underlying index is a query accelerator, not a constraint.
- **Reconciliation is single-IN_PROGRESS-per-account** by the locked P5.4.b decision (`ReconciliationAccountAlreadyInProgressError`, 409). Simplifies the matcher's "already-reconciled" detection; closes a class of double-match bugs that would otherwise be possible if two reconciliations were open simultaneously.

What is **out of scope** of the Phase 5 threat model and explicitly deferred:

- **Attachment download endpoint** — there's no `GET /v1/imports/{id}/attachment` yet. Statement bytes can be re-uploaded but not retrieved through the API. If/when that endpoint lands, range-request denial-of-service + content-disposition smuggling become in-scope concerns.
- **Multi-currency reconciliation** — every Phase 5 endpoint asserts `account.currency == reconciliation.currency`; multi-currency is silently out of scope. Lifting this requires FX rate engine work first (per ARCHITECTURE.md §1.3 deferred items).
- **Partial-of-one matches** (a $100 statement line matching $60 of a $100 ledger tx with $40 residual) — explicitly rejected for v1 per ADR-0004 §Q3. Manual match enforces `match_amount == line.amount`.

### 5.3 — Phase 6 (AI integration) — ✅ shipped

The privacy inflection point. All five entry constraints landed in
Phase 6 implementation (P6.1–P6.5.c). The *authoritative* contract is
**[ADR-0005](adrs/0005-ai-integration.md)**, which closes
[#102](https://github.com/rmwarriner/tulip-accounting/issues/102).

- ✅ **Prompt bodies are not logged by default.** `ai_invocations.prompt_json` defaults to NULL; only metadata (model, latency, cost, tenant, user, success/fail) lands by default. Tenant-level opt-in via `households.ai_policy.log_prompts=true` (CLI: `tulip ai config log-prompts on`, which emits the privacy-cost warning to stderr). `prompt_hash` (SHA-256 over the redacted prompt) is always populated so "was the same prompt sent twice" is answerable without storing prompts. *Implemented:* P6.1 (PR #154) + P6.5.b (PR #163).
- ✅ **Redaction runs before the litellm call**, not after. `PromptRedactor` runs server-side per capability; `POST /v1/ai/preview` is byte-faithful so operators can see exactly what would be sent before any provider call fires. *Implemented:* P6.1 (PR #154).
- ✅ **No silent provider fallback.** Provider 5xx errors raise `AIProviderError` and stamp `outcome=provider_error` — no implicit failover. The one explicit exception is cost-cap `degrade` mode, which swaps to `fallback_provider` (typically Ollama) and **audits `provider=ollama` explicitly**. `tulip ai status` prints the locked callout: "applies on cost-cap degrade ONLY. Provider 5xx errors do NOT silently fall back." *Implemented:* P6.5.a (PR #161) + P6.5.b (PR #163).
- ✅ **`actor_kind=ai_agent` audit rows** for every state-changing AI proposal that's approved, with `metadata.proposal_id` linking back to the originating `pending_proposal` and through to `ai_invocations.id` via `ai_invocation_id`. The architecture-test enforces the single chokepoint (`AuditLogWriter`); the executor stamps the correct `actor_kind` based on `pending_proposal.created_by_kind`. *Implemented:* P6.4 (PR #158) + P6.4.b (PR #159).
- ✅ **Cost / rate caps are enforced server-side**, not in the prompt or in the model. `tulip_ai.cost.enforce_pre_call` runs *before* the adapter call: per-user sliding-window rate limit (default 60/hour) gates first, then the household-wide monthly cost cap. Both write `outcome=rate_limited` / `outcome=cost_capped` audit rows on the block path so capped capacity is observable in `ai_invocations`. *Implemented:* P6.5.a (PR #161).
- ✅ **litellm telemetry / callback surface pinned off at adapter init.** `LitellmAdapter.__init__` calls `_pin_litellm_safety_defaults()`, which sets `litellm.telemetry=False`, clears `success_callback` / `failure_callback` / `callbacks`, and sets `suppress_debug_info=True`. litellm's package-default for `telemetry` is `True` (a PyPI version check); leaving it that way would defeat "AI is the only egress, and only when you explicitly use it" without a Tulip code change. **Do not unwind this pinning during a litellm upgrade** — verify the surface against the new version and re-pin. *Implemented:* P8 (#248).

## 6. Out of scope (explicitly)

These are real concerns, but not for this checkpoint:

- **Penetration testing** — the Phase 8 deep security audit was static / document-only and explicitly *not* a pen test (see its §10). A dynamic pen-test engagement is still deferred — Phase 10 / pre-cloud.
- **Cryptographic review** of `encrypt_field` (key derivation, nonce reuse risk, AEAD usage details) — ✅ covered by the Phase 8 deep security audit (crypto stream): primitives confirmed correctly chosen and used; AEAD AAD binding is a tracked Medium follow-up, not a v1 blocker.
- **Multi-tenant cloud threat model** — Phase 10.
- **Privacy audit of AI flows** — ✅ shipped as [ADR-0005](adrs/0005-ai-integration.md) (P6.0, 2026-05-11), implemented through P6.1–P6.5.c; the full-system [deep privacy audit](audits/2026-05-13-deep-privacy-audit.md) (2026-05-13) re-verified the shipped state and broadened the review to the whole surface. See §5.3 above for the realised constraints.
- **Backup/restore threat model** — the backup/restore pipeline now exists (`tulip-cli/backup.py`) and was reviewed by the Phase 8 deep security audit (H-1: path traversal on restore). A standalone backup/restore threat-model section is still deferred.
- **Supply chain / SBOM** — the Phase 8 deep security audit reviewed the `pyproject.toml` / `uv.lock` dependency graph and ran `pip-audit` (clean); dependency-pinning gaps are tracked as Low findings. A formal SBOM artifact is still deferred.
- **Side-channel / timing attacks** — broadly out of v1 scope; relevant to multi-tenant cloud (Phase 10). One exception is already addressed: the login user-enumeration timing oracle the security audit flagged — Phase 8 Wave-1 landed a constant-time login path.

---

## References

- [Deep Security Audit — 2026-05-12](audits/2026-05-12-deep-security-audit.md) — Phase 8 deep security audit (0 Critical / 8 High); authoritative finding-by-finding record.
- [Deep Privacy Audit — 2026-05-13](audits/2026-05-13-deep-privacy-audit.md) — Phase 8 full-system privacy audit (1 Critical / 17 High); GDPR / CCPA framing.
- [ARCHITECTURE.md §3.3 Tenancy Model](ARCHITECTURE.md)
- [ARCHITECTURE.md §7.4 Encryption at Rest](ARCHITECTURE.md)
- [ARCHITECTURE.md §10 Audit Cadence](ARCHITECTURE.md)
- [PHASE_STATUS.md](PHASE_STATUS.md)
- Issue #56 (this checkpoint), #55 (transaction void → Phase 5), #28 (token-store backends), #44 (multi-currency parents).

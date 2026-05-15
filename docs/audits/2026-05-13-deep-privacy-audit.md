# Deep Privacy Audit — 2026-05-13

**Auditor:** Claude (Opus 4.7, multi-agent code review).
**Scope:** Full-system privacy audit against `main @ 93ed433` (Phase 7 complete). The Phase-6 privacy review shipped pre-implementation as [ADR-0005](../adrs/0005-ai-integration.md); this audit verifies the shipped state and broadens to the full system surface — personal-data inventory, data flows, retention/deletion, user-rights infrastructure, multi-user-within-household boundaries, operational PII.
**Stance:** Document only. No code changes were made; every recommendation is a candidate follow-up tracked separately. The companion [2026-05-12 deep security audit](2026-05-12-deep-security-audit.md) is referenced where findings overlap; this audit does not restate security findings — it layers privacy-regulatory framing on top.

---

## 1. Executive summary

Tulip's privacy posture is generally strong for a self-hosted v1: the architectural controls that matter for GDPR — explicit human-in-the-loop on every AI output (Art. 21/22 by construction); prompt bodies opt-in NULL by default (Art. 5(1)(c) minimisation); AES-256-GCM field-level encryption (Art. 32); no marketing/cookie/analytics surface; cross-tenant isolation by composite-FK chokepoint — are real and tested. The byte-faithful AI preview is the single best user-consent affordance in the codebase.

Three categories of finding warrant attention before any external rollout or multi-user-household scenario:

1. **One Critical ADR-0005 lock violation.** ADR-0005 §Q4 says `local_only` profile pins to Ollama "regardless of `default_provider`." The implementation reads `provider = household_policy.get("fallback_provider") or "ollama"`. A household that sets `profile=local_only` *and* configures any cloud `fallback_provider` (typically set up months earlier for cost-cap degrade) silently ships every AI prompt to that cloud. The user-facing label says "local-only"; the wire says otherwise. This is the highest-impact privacy finding in the codebase.

2. **Erasure infrastructure is structurally absent.** GDPR Art. 17 and CCPA §1798.105 right-to-erasure have no application path. The schema is fully CASCADE-ready (every household-scoped table declares `ondelete="CASCADE"` from `households.id`), but no `DELETE /v1/households/me` or `DELETE /v1/users/{id}` endpoint exists, attachment ciphertext on disk is never deleted (no `AttachmentRepository.delete()`, no GC), and four other endpoints (`DELETE` on accounts / pools / envelopes / sinking-funds) return 204 but actually flip `is_active=False` — soft-delete masquerading as deletion. The audit-log "forever" retention policy compounds this by accumulating denormalised user-typed PII in `before_snapshot` / `after_snapshot` JSON in perpetuity.

3. **Multi-user-within-household privacy bugs are dormant.** Five surfaces leak private-account / private-pool data within a household (`GET /v1/transactions`, reconciliation envelope, AI custom-query, AI NL-ask, `GET /v1/reports/audit-log`, `GET /v1/scheduled-jobs`). One enables a member to mutate another member's private envelope by approving an AI proposal that targets it. **These are dormant today because v1 has no multi-user invite surface — every household has exactly one admin (per the `register` flow).** The latent bugs surface the moment the Phase-9 invite endpoint lands. The companion security audit M-13 + M-14 captured two of these; this audit found four more.

**Counts:** 1 Critical · 17 High · 28 Medium · 22 Low · 38 Info / Confirms-control. Some findings are privacy framings of security-audit findings (cited via §); the cross-cut in §11 lists them.

---

## 2. Methodology

- **Audit scope:** Same surface as the 2026-05-12 security audit (~37 kLOC source / ~69 kLOC total Python across seven workspace packages) plus the regulatory framings: GDPR Articles 4, 5, 7, 9, 15–18, 20–22, 30, 32; CCPA §1798.105 / .110 / .140.
- **Seven parallel investigative streams**, each producing structured findings under uniform schema (severity, status, location, description, privacy implication, recommendation): personal-data inventory (A); data flows + egress (B); retention + deletion (C); user-rights infrastructure (D); ADR-0005 AI flow re-audit (E); multi-user-within-household privacy (F); operational PII (G).
- **Severity rubric** (privacy-flavoured):
  - **Critical:** Documented user-facing promise (ADR / threat model / UI label) is silently violated such that personal data is exposed to a destination the user explicitly opted out of.
  - **High:** Real regulatory-rights infrastructure gap that blocks fulfilment of a data-subject request, OR a real PII-exposure pathway, OR a doc/code drift on a load-bearing privacy claim.
  - **Medium:** Defense-in-depth gap, retention horizon unjustified, consent provenance incomplete, scope confusion (per-user vs per-household), or correctness issue with bounded privacy impact.
  - **Low:** Hygiene, future-proofing, single-tenant-localised exposure, or scenario-bounded risk.
  - **Info:** Observation, positive control, design-decision documentation.
- **What this audit is NOT:** a dynamic / pen-test review (covered by the companion security audit's §10); a legal opinion (recommendations frame regulatory exposure but a controller's actual obligations depend on jurisdiction + use); a redactor accuracy evaluation (Stream E documents the redactor's documented residual surface — see F-PE-005 below — but doesn't fuzz it).
- **Doc convention:** Findings here are numbered C-1 / H-1 / M-1 / L-1. The original stream-level finding IDs (F-PA-NNN, F-PB-NNN, etc.) are cited so the underlying analysis remains traceable.

---

## 3. Severity overview

| Severity | Count | Primary themes |
|---|---|---|
| Critical | 1 | `local_only` AI profile silently routes to cloud when `fallback_provider` is configured (ADR-0005 §Q4 lock violation) |
| High | 17 | No erasure path (household + user); attachment GC absent; soft-delete-as-deletion API verb; cross-user-within-household visibility leaks in 6 surfaces; AI `response_text` bypasses opt-in on error; per-user AI restriction half-wired; rectification incomplete; portability ledger-only; unencrypted parallel copy of an encrypted field |
| Medium | 28 | Audit-log "forever" retention denormalises PII; IP + UA forever in audit_log; consent provenance missing on `log_prompts`; AI invocation retention unbounded; rejected proposals retained verbatim; session + recovery-code accumulation; redactor per-capability drift; preview body-faithful but not message-faithful; cost-cap degrade audit incomplete; per-capability redaction inconsistent across `default` profile |
| Low | 22 | Diagnostics fingerprint disclosure; CLI emits email to stdout; 422 echoes rejected input; Problem-Details `instance` echoes UUIDs; journal export embeds household name; backup rotation aspirational; litellm telemetry not actively pinned-off |
| Info / Confirms-control | 38 | Schema CASCADE coverage; AES-256-GCM at rest; AIInvocationWriter chokepoint; byte-faithful preview path; no telemetry libs; no outbound HTTP from reports; AI capability allowlist closed; structlog whitelist + tests; absence of structured collection for special categories (Art. 9) |

---

## 4. Critical findings

### C-1 · `local_only` AI profile silently routes to cloud when `fallback_provider` is set
**Stream:** E (F-PE-001).
**Location:** `packages/tulip-ai/src/tulip_ai/policy.py:137-139`; tested only at `packages/tulip-ai/tests/test_policy.py:74-84`.

ADR-0005 §Q4 locks the behaviour: "`local_only` overrides everything and pins to Ollama regardless of `default_provider`." The implementation reads:

```
if profile == "local_only":
    provider = household_policy.get("fallback_provider") or "ollama"
```

If the household has a `fallback_provider` configured (typically for cost-cap degrade — see §M-N below), `local_only` resolves to that fallback. **A household admin who sets `profile=local_only` because they don't want their data leaving their machine, but who configured `fallback_provider=openai` six months ago for cost-cap reasons, will silently ship every AI prompt to OpenAI.** No runtime assertion catches this; `redaction.py:9-11` even *claims* such an assertion exists but no code implements it. The byte-faithful preview shows the cloud provider name in the `provider` field but the CLI status text continues to label the profile as `local_only` — the user is unlikely to scrutinise the mismatch.

**Privacy implication.** Highest-impact violation of a user-facing privacy promise found in the codebase. `local_only` is the only profile name in the UI that makes a binary claim about data egress; violating it silently is exactly the consent-decay pattern that erodes trust.

**Recommendation.** Pre-real-user (any rollout, even continued self-hosted). In `resolve_policy`, when `profile == "local_only"`, force `provider = "ollama"` (or an explicit `_LOCAL_PROVIDERS` allowlist) regardless of `fallback_provider`. Refuse to resolve if no local provider is configured. Property-based test: for any `ai_policy`, `profile=local_only` ⇒ resolved provider ∈ `_LOCAL_PROVIDERS`.

---

## 5. High-severity findings

### H-1 · `ai_invocations.response_text` on error paths persists exception strings, bypassing the `log_prompts` opt-in
**Stream:** G (F-PG-004). **Location:** `packages/tulip-ai/src/tulip_ai/forecast.py:263, 291, 328`; `nl_query.py:269, 304, 339, 364`; corresponding sites in `categorize.py` + `proposals.py`.

`THREAT_MODEL.md:129` promises "prompt bodies are not logged by default; `ai_invocations.prompt_json` defaults to NULL; only metadata lands by default." For the *success* path this is correct — `prompt_json` and `response_text` are both correctly gated on `policy.log_prompts`. The **error** path unconditionally writes `response_text=str(exc)[:500]`, `response_text=gate.reason[:500]`, or `response_text=f"unsafe_sql: {exc}"`. Exception strings from LiteLLM frequently echo prompt fragments; SQL-safety failures include the rewritten SQL that derived from the user's NL question. Net: a household with `log_prompts=false` (the default) still accumulates prompt-derived content in `response_text` on every error.

**Privacy implication.** Direct breach of the documented opt-in contract. GDPR Art. 5(1)(c) minimisation + Art. 7 consent: the user has refused prompt logging and the system logs anyway when things fail.

**Recommendation.** Pre-real-user. Gate error-path `response_text` on `policy.log_prompts` the same way success paths do — capture the structured `outcome` enum (already in scope) and drop the freeform text. Alternative: persist `f"{type(exc).__name__}"` only, never `str(exc)`.

### H-2 · No erasure path: household-delete and user-delete endpoints do not exist
**Stream:** C (F-PC-001) + D (F-PD-004 / F-PD-005). **Location:** no `DELETE /v1/households/...` or `DELETE /v1/users/...` route anywhere under `packages/tulip-api/src/tulip_api/routers/`.

The schema is fully erasure-ready: every household-scoped model declares `ondelete="CASCADE"` on `household_id`, verified across `accounts`, `allocation_pools`, `ai_invocations`, `attachments`, `audit_log`, `csv_profiles`, `notifications`, `pending_proposals`, `periods`, `scheduled_jobs`, `sessions`, `transactions`, `shadow_transactions`, `users`, `mfa_recovery_codes`, and (indirectly via `statement_lines`) `import_batches`. But no HTTP route, CLI command, or repository method exposes the action. A user wanting to exercise Art. 17 has no path; the operator can only run raw SQL — which still leaves attachment ciphertext blobs (H-3), prior journal exports, and backups.

**Privacy implication.** GDPR Art. 17 + CCPA §1798.105 have zero fulfilment surface. The single-tenant local-self-hosted stance softens urgency today but turns into a Phase-9 cloud blocker.

**Recommendation.** Pre-real-user. `DELETE /v1/users/{user_id}` (admin-only; prohibit last-admin delete; CASCADE handles sessions + MFA; rely on H-7 below for `actor_user_id` FK behaviour; write a terminal audit row before the user row vanishes). `DELETE /v1/households/me` two-step (soft tombstone + admin confirmation, hard cascade after). Pair with H-3 (attachment GC) and the doc-cross-cut M-11 (audit-log retention) for end-to-end erasure.

### H-3 · Attachment ciphertext on disk is never deleted
**Stream:** C (F-PC-007). **Location:** `packages/tulip-storage/src/tulip_storage/repositories/attachment.py:69-103` — no `delete()` method on the repository; no `Path.unlink()` against `attachment_root` anywhere in the codebase.

Encrypted attachment blobs are written to `attachment_root/<content_hash>`. There is no `AttachmentRepository.delete()`, no scheduled GC handler, no `tulip admin gc-attachments` recipe. Even when H-2's household-cascade lands, it removes the DB row but leaves the file. Within-household dedup means one blob may back multiple `attachments` rows, so naive per-row unlink would corrupt remaining references.

**Privacy implication.** A user who deletes their household reasonably expects uploaded bank statements gone. AES-256-GCM under the master key bounds the readability of orphaned files, but as long as the master key is retained (the default), the encryption is not erasure. Crypto-shredding (delete the master key) is plausible but isn't the documented operator workflow.

**Recommendation.** Pre-real-user. `AttachmentRepository.delete(attachment_id)` deletes row + unlinks blob with refcount check. Daily GC: walk `attachment_root`, unlink any file whose hash doesn't appear in `attachments.content_hash`. Plumb into H-2's household-delete cascade.

### H-4 · `DELETE` verb on accounts, pools, envelopes, sinking-funds is soft-delete masquerading as deletion
**Stream:** C (F-PC-003). **Location:** `routers/accounts.py:355-380`, `envelopes.py:292-322`, `sinking_funds.py:238-269`; repos: `account.py:87-94`, `allocation_pool.py:114-123`.

Four entity types expose `DELETE /v1/<resource>/{id}` returning 204, but implementation is `is_active=False`. The row, `name`, `notes_encrypted`, and (for accounts) `external_account_number_encrypted` all survive. The deactivation is correct for ledger integrity (posting FKs are `ON DELETE RESTRICT`); the *user-facing contract* is misleading.

**Privacy implication.** A user calling `DELETE /v1/accounts/123` reasonably expects the data erased. GDPR Art. 17(3)(b)/(e) covers retaining posting *history*, not account *names* or *notes*.

**Recommendation.** Pre-real-user. (a) Make the response body honest: `{"action": "deactivated", "data_retained": [...]}`. (b) Add `POST /v1/accounts/{id}/redact` (admin) that nulls `name` / `external_account_number_encrypted` / `notes_encrypted` on a deactivated account, replacing name with `redacted-account-<short-hash>`. Postings keep their FK; PII goes. Mirror for pools / envelopes / sinking funds.

### H-5 · `GET /v1/transactions` returns postings on private accounts the caller can't see
**Stream:** F (F-PF-002). **Location:** `packages/tulip-api/src/tulip_api/routers/transactions.py:566-636, 405-422, 425-517`.

Neither the list, show, nor PATCH endpoints filter postings by account visibility. A transaction posting against user A's private account is returned in full to any member who calls these endpoints. The `account_id` query filter doesn't gate either — a member can `GET /v1/transactions?account_id=<UUID-of-private-account>` and the API doesn't verify the caller can see that account. Companion security audit M-13/M-14 covered the reports + journal-export bypass; this is the third surface.

**Privacy implication.** Cross-user-within-household disclosure on the most-trafficked endpoint in the API. Dormant in v1 (no multi-user invite surface — see H-15) but the bug is shippable today.

**Recommendation.** Pre-real-user. Filter postings (or full transactions, if any posting touches an invisible account) through the same visibility helper used by `routers/accounts.py:55-60`. Land alongside the M-13/M-14 fix as a single "ledger reads share a visibility lens" slice.

### H-6 · Reconciliation envelope bypasses private-account visibility
**Stream:** F (F-PF-003). **Location:** `packages/tulip-api/src/tulip_api/routers/reconciliations.py:141-220, 243-334`.

`list_reconciliations`, `create_reconciliation`, and `get_reconciliation` gate only on `require_role("admin", "member")` and `claims.household_id`. A member can list all reconciliations including those against private accounts they can't see, open a fresh reconciliation against any household account (including another member's private one), and see matched / unmatched statement lines + ledger transactions for that account in the inbox — which include amounts and counterparty names from the bank statement.

**Privacy implication.** Reconciliation is where bank-statement counterparty names land in cleartext. This surface leaks "where the money went" in higher fidelity than the journal-export bypass.

**Recommendation.** Pre-real-user. Inject the visibility filter on account fetch in every reconciliation endpoint; for list, filter rows by visible-accounts set; for create, return `account.not_found` on invisible private accounts.

### H-7 · AI custom-query + NL-ask views ignore account visibility
**Stream:** F (F-PF-007). **Location:** `packages/tulip-ai/src/tulip_ai/sql_safety.py:51-78` (`_TRANSACTIONS_VIEW.select_fragment`); consumers `routers/reports.py:517-544` (custom-query) and `routers/ai.py:288-333` (NL ask).

The SQL-safety rewriter scopes by `t.household_id` only — not by `account.visibility` or `account.created_by_user_id`. A member's NL query "show all spending last month" returns rows for postings against another member's private account, including amount + account name. The custom-query report renders the same data into HTML / CSV / PDF. The companion security audit's F-B-011 captured the custom-query angle; this extends to NL ask.

**Privacy implication.** Same shape as H-5 / H-6 reached through a different surface that's harder to spot because the SQL-safety pass already makes the endpoint feel "locked down."

**Recommendation.** Pre-real-user. Add a second bind parameter (`actor_user_id`) to `validate_and_rewrite`; rewrite `WHERE t.household_id = :household_id AND (a.visibility = 'shared' OR a.created_by_user_id = :actor_user_id OR :is_admin)`.

### H-8 · Notifications and proposals are scoped per-household, not per-user
**Stream:** F (F-PF-004). **Location:** `packages/tulip-storage/src/tulip_storage/models/notification.py:40-58` (no `target_user_id`); `packages/tulip-api/src/tulip_api/routers/notifications.py:44-86`; `packages/tulip-api/src/tulip_api/routers/proposals.py:129-149`.

Notifications are written by the `daily_insights` scheduler against the household; any authenticated user sees the full inbox; any user (no `require_role` gate — companion security F-B-003) can dismiss anyone else's. Proposals are scoped only by household on read. User A's pending proposal — including its `payload` (envelope_id, new budget), `rationale`, and AI-generated `title` (e.g., "Adjust *Personal Hobbies* budget to 250 USD") — is visible to user B, and **user B can `POST /approve` it without consulting user A**. The executor doesn't re-check approver visibility against the affected resource.

**Privacy implication.** A member can mutate another member's private envelope budget by approving an AI proposal that targets it. The strongest within-household cross-user vulnerability found.

**Recommendation.** Pre-real-user. (a) Add `target_user_id` (nullable, NULL=household-broadcast) to `notifications`; filter list by `(target_user_id IS NULL OR target_user_id = caller)`. (b) For proposals: when the underlying entity is private (`pool.visibility=='private'`), restrict reads + decisions to creator + admin. (c) Gate `approve` / `reject` on the affected resource's visibility filter, not just `require_role`.

### H-9 · `GET /v1/reports/audit-log` exposes all members' actions in full
**Stream:** F (F-PF-005). **Location:** `packages/tulip-api/src/tulip_api/routers/reports.py:446-486`; `packages/tulip-reports/src/tulip_reports/reports/audit_log.py:58-77`.

The endpoint requires only authentication — no `require_role`, no `_filter_for_role`. Any household member can paginate the full audit history, filter by `actor_user_id=<other-user>`, and inspect `before_snapshot` / `after_snapshot` JSON on every entity, including private-account creations and private-envelope updates (which embed names + values verbatim).

**Privacy implication.** The audit log was designed for admin-grade incident investigation; in v1 it's effectively a household-wide activity feed where any member can study another member's private workflow.

**Recommendation.** Pre-real-user. Gate on `require_role("admin")` at minimum (matches the Phase-7 docs' implicit intent). Longer-term, filter rows whose `entity_type` is account / pool / transaction and whose target is invisible to the caller.

### H-10 · `import_batches.summary_json` carries bank-account identifiers unencrypted
**Stream:** A (F-PA-005). **Location:** `packages/tulip-storage/src/tulip_storage/models/import_batch.py:87`.

Per the model docstring, `summary_json` is "format-specific blob (e.g., OFX bank/account ids, CSV header layout) for audit-trail completeness." For OFX this typically includes BANKID and ACCTID (full or last-four account number) in plaintext JSON. The curated equivalent (`accounts.external_account_number_encrypted`) is field-encrypted. The asymmetry undermines the encrypted column's protection via an unencrypted parallel copy two tables over.

**Privacy implication.** CCPA §1798.140(v)(1)(C) financial-account-number. If OFX embedded a full account number in `BANKACCTFROM`, it persists unencrypted in `summary_json` *and* in the encrypted attachment file. A controller treating `external_account_number_encrypted` as the high-water mark for account-number protection has a hidden bypass.

**Recommendation.** Pre-real-user. Inventory the per-format keys actually written. Either redact ACCTID to last-four before writing, or encrypt `summary_json` via `encrypt_field` (likely cleaner — `summary_json` is bounded-size structured data).

### H-11 · Per-user "restriction of AI processing" is half-wired
**Stream:** D (F-PD-008) + E (F-PE-003). **Location:** `packages/tulip-ai/src/tulip_ai/policy.py:97-167` (`resolve_policy` accepts `user_policy` with documented "max-severity wins" merge); five call sites pass `user_policy=None`: `categorize.py:153`, `nl_query.py:234`, `proposals.py:133`, `forecast.py:236`, `routers/ai.py:180, 187, 266`. `users` has no `ai_policy` column.

ADR-0005 §Q5 specifies a household-floor + per-user-ratchet-up shape. The merge logic is real and tested. Storage + endpoint + invocation are not. A single member cannot dial down AI processing for their own data. Per-user API keys (`users.ai_keys_encrypted`) are similarly dead-storage — the column exists but no read path consumes it.

**Privacy implication.** GDPR Art. 18(1)(a)/(d) restriction-of-processing has no surface. The admin's policy is dispositive over every member's data. Dormant today (no member invite), surfaces immediately at multi-user.

**Recommendation.** Pre-real-user (or alongside multi-user invite). Add `users.ai_policy` JSON column mirroring `households.ai_policy`; `PUT /v1/users/me/ai-policy`; thread the loaded dict through all five `resolve_policy` callsites. Pure wiring — the design is in place.

### H-12 · `tulip ai propose / approve / reject` flow has no audit row of its own
**Stream:** referenced from security audit H-8 + reaffirmed by privacy stream C (F-PC-006). **Location:** `packages/tulip-api/src/tulip_api/routers/proposals.py:93-246`.

Proposal create / approve / reject mutate `pending_proposals.status` but write no `audit_log` row of their own. Only the proposal's *executor* writes a row, and only for approved-and-executed proposals. A rejected AI proposal leaves no audit trail. **Rejected proposals are also retained forever** with their AI-generated `payload`, `rationale`, `title`, and `decision_note` — an AI hallucination baked into a `rejected` row can't be removed.

**Privacy implication.** Doubles the security-audit H-8 forensic gap with a CCPA §1798.140(o) angle (AI outputs reasoning about a consumer's records are personal information). GDPR Art. 17(1)(b) consent-withdrawal: if a household later sets `log_prompts=false` or disables AI, the rejected-proposal history retains the AI's prior commentary on their data.

**Recommendation.** Pre-real-user. Add audit-log writes at proposal create / approve / reject (covered by security audit's filed issue #222). Add admin-only `DELETE /v1/ai/proposals/{id}` hard-deleting rejected proposals only; approved stay (audit chain).

### H-13 · No "everything about me" enumeration endpoint
**Stream:** D (F-PD-001 / F-PD-006 / F-PD-011). **Location:** no `GET /v1/users/me/export`; per-table absence — `users`, `sessions`, `ai_invocations`, `pending_proposals`, `notifications`, `attachments`, `mfa_recovery_codes` have no per-subject enumeration.

GDPR Art. 15(1) and CCPA §1798.110(a) both require enumeration on request. Today this requires raw SQLite queries. The journal export covers ledger postings only (Art. 20 partial); the audit-log report filters by user but omits sessions, AI invocations, notifications, attachments, and the `users` row itself. Admin has no member-scoped export tool for delegated-controller requests.

**Privacy implication.** Art. 15 / §1798.110 unfulfilled. Once households have non-admin members (Phase 9), Art. 12(3) "facilitate the exercise of rights" failure once the relationship turns adversarial.

**Recommendation.** Phase 8. `GET /v1/users/me/export` returns a JSON envelope `{user, sessions, audit_log_rows, ai_invocations, pending_proposals, notifications, attachments_metadata, mfa_recovery_codes_status, transactions_created_by_me}`. Mirror as admin command `tulip household member-export <user_id>`. Pair with H-2 / H-3 (delete cascade).

### H-14 · Rectification is half-supported — POSTED descriptions immortal; profile fields write-once
**Stream:** D (F-PD-002 + F-PD-003). **Location:** `packages/tulip-api/src/tulip_api/routers/transactions.py:300-402` (void); `routers/auth.py:104-184` (only mutating user endpoint).

POSTED transactions can only be "voided" — the void leaves `description` / `reference` / `notes_encrypted` / `postings` untouched, stamps `voided_by_transaction_id`, and creates a reversal sibling whose own description is `"Reversal of {original_description}: {reason}"` — duplicating the original PII into a second row. Profile fields (email, display_name, password) are write-once at register; there is no `PATCH /v1/users/me`, no `POST /v1/auth/password/change`.

**Privacy implication.** GDPR Art. 16 (rectification) is unworkable for any POSTED transaction. The reversal description is a second-order privacy bug — fixing a mis-typed counterparty name requires the reversal to *also* be redacted.

**Recommendation.** Phase 8. (a) `PATCH /v1/transactions/{id}/description` for POSTED txs (description-only, no posting effects), audited as `description_rectified`. Use redaction-token placeholder for the reversal's quoted description. (b) `PATCH /v1/users/me` for `display_name`, `email` (re-auth gated); `POST /v1/auth/password/change` requiring current password. Both must emit `audit_log` rows.

### H-15 · Multi-user invite surface absence is the latent-bug deferrer
**Stream:** F (F-PF-008). **Location:** absence in `routers/auth.py` — no endpoint to invite, create, demote, or delete additional users; no admin-over-member password-reset / session-revocation surface; no role-change route.

`POST /v1/auth/register` is the only path that creates a user; it hardcodes `role=UserRole.ADMIN`. The `UserRole.VIEWER` enum value exists in the schema but no router accepts it and `_filter_for_role` doesn't special-case it. **Every household in v1 has exactly one admin.**

**Privacy implication.** Dual-edged: (a) positive — H-5 through H-9 cross-user-within-household leaks are dormant because there are no other users to leak to; (b) the moment a Phase-9 invite endpoint lands, all six dormant findings become live. The Phase-9 work must therefore ship the visibility fixes in the same slice.

**Recommendation.** Document the linkage explicitly: any invite-endpoint slice must land with H-5 through H-11 + M-7 / M-8 / M-9 (private-resource visibility + per-user AI restriction + per-user consent) as preconditions. Architecturally: the `VIEWER` enum should either get plumbed (with explicit decisions on every read endpoint) or be removed until P9+.

### H-16 · `ai_invocations` history has no retention policy and survives every other cleanup path
**Stream:** C (F-PC-005). **Location:** `packages/tulip-storage/src/tulip_storage/models/ai_invocation.py:52-86`; write paths in all four capability modules.

`ai_invocations` is append-only by design (ADR-0005 §Q6). Rows persist `prompt_hash` always; `prompt_json` + `response_text` when `log_prompts=true`. No delete path. CASCADE fires only on household-delete (which doesn't exist — H-2). **Toggling `log_prompts=false` does not retroactively scrub rows written while it was true.** Deleting a PENDING transaction does not affect AI rows that referenced it.

**Privacy implication.** GDPR Art. 17(1)(b) — withdrawal of consent. Toggling `log_prompts=false` is exactly that withdrawal; historical rows should be subject to erasure on that toggle. "Redacted" ≠ "non-personal" (per F-PA-003 — `prompt_hash` is pseudonymous, not anonymous).

**Recommendation.** Pre-real-user. (a) On `log_prompts` flip-to-false, scrub `prompt_json` + `response_text` to NULL across the household (row + hash + cost-metadata survive for the audit chain). (b) Default 90-day TTL for non-proposal `ai_invocations`; longer for proposal-linked rows (tied via `pending_proposals.ai_invocation_id` — pair with security audit M-21's composite FK fix).

### H-17 · Free-text user-input fields are a GDPR Art. 9 risk vector the threat model doesn't acknowledge
**Stream:** A (F-PA-002). **Location:** Twelve free-text user-input fields across `transactions.description`, `transactions.reference`, `transactions.notes_encrypted`, `postings.memo`, `accounts.notes_encrypted`, `allocation_pools.name`, `pending_proposals.decision_note` / `rationale`, `notifications.body`, `shadow_transactions.description`, `shadow_postings.memo`, `statement_lines.description` / `counterparty`.

None have a content classifier or input-boundary warning. A user typing `"Payment to Planned Parenthood — $40"` introduces GDPR Art. 9(1) special-category data by inference (health / sexual orientation / political opinion depending on what the org represents). The system has no way to know; current tiering treats free-text as just "High."

**Privacy implication.** Art. 9 requires explicit consent or §9(2) basis. Self-typed data has §9(2)(e) "manifestly made public" for the typer, but third-party references (a household member's medical bill) attach Art. 9 obligations without a consent path. CCPA "Sensitive Personal Information" §1798.140(ae)(1)(B).

**Recommendation.** Phase 8. Add a "Free-text user-content tier" to `THREAT_MODEL.md §2` with explicit Art. 9 carve-out: *treat the column as Art. 9-tainted by default for retention and access-control purposes*. No code change today; flag for Phase 9 multi-tenant access-control work. The H-1 / H-16 retention controls become more urgent under this framing.

---

## 6. Medium-severity findings

Numbered M-N. Citations to underlying stream finding for traceability.

- **M-1 · Audit-log "forever" retention is GDPR Art. 17 / 5(1)(e) incompatible by default** (Streams C F-PC-002, G F-PG-002; privacy framing of security L-21 / F-F-006). Recommend tiered retention via `households.audit_retention_policy`; 7 years for ledger / accounting; 90 days for auth events; 30 days for AI capabilities. `tulip admin audit-prune` recipe.
- **M-2 · `audit_log.ip_address` / `user_agent` are stored on every auth event in two tables, omitted from threat-model §2, not on redaction whitelist** (Streams A F-PA-004, G F-PG-003). Truncate IP to /24 IPv4 / /48 IPv6 at write; add to `_SENSITIVE_FIELDS`; surface in §2 classification table.
- **M-3 · Email is misclassified in `THREAT_MODEL.md §2` (Medium → should be High)** (Stream A F-PA-001 partial). Reconfirms security audit H-5. Either reclassify or fix code; both are doc/code-drift.
- **M-4 · `transactions.description` lives in eight surfaces** (Stream A F-PA-001 full). Highest-multi-presence field in the system: `transactions`, `audit_log.before/after_snapshot`, `ai_invocations.prompt_hash` always + `prompt_json` opt-in, `pending_proposals.payload`, journal export, two report HTML surfaces, encrypted attachment file. Deletion touches eight places, not one. Reclassify to **Critical (free-text)** in §2; document the footprint.
- **M-5 · `ai_invocations.prompt_hash` is pseudonymous, not anonymous** (Stream A F-PA-003). SHA-256 over a small per-household input space → rainbow-tablable. GDPR Recital 26 = pseudonymisation, not anonymisation. Threat-model framing as "forensics-light" misleads. Recommend per-household salt; reclassify to High tier in §2.
- **M-6 · Sessions and MFA recovery codes accumulate indefinitely; revoked sessions retain IP + UA forever** (Stream C F-PC-004; reconfirms security L-2). Daily pruning handler: delete `sessions` where `revoked_at < now()-90d`, `mfa_recovery_codes` where `used_at < now()-90d`.
- **M-7 · Consent provenance for `log_prompts` is a bare bool** (Stream D F-PD-010). `PUT /v1/ai/config` flip emits `log.info` only; no `audit_log` row; the blob has no consent-version, no actor, no timestamp distinct from `updated_at`. Acute once non-admin members exist. Recommend `audit_log(action="ai.consent_changed", before/after, actor_user_id)`.
- **M-8 · Per-capability redaction logic is duplicated outside `PromptRedactor`** (Stream E F-PE-004). `nl_query._redact_description`, `forecast.bucket_time_series`, `proposals` name-elision all reimplement the categorize heuristic with their own constants. Drift risk: raising `_KEEP_MIN_LEN` in `redaction.py` won't follow in `nl_query.py`. Centralise in `PromptRedactor.redact_<cap>` methods; tests assert capability invokes the redactor.
- **M-9 · Profile semantics are inconsistent across capabilities under `default`** (Stream B F-PB-002). `nl_query._redact_row` strips counterparty tokens under `default`; `PromptRedactor.redact_categorize` under `default` is pass-through. Users reading ADR-0005 §Q3 expecting "default = full, strict = redacted" find that `nl_query.default ≈ categorize.strict`. Align or document.
- **M-10 · `POST /v1/ai/preview` is body-faithful but does not include the system prompt** (Stream B F-PB-004; Stream E F-PE-002 expands the same concern). Preview shows `messages[1].content` but omits `messages[0]` (the system instruction). For categorize the system prompt is static — no current divergence — but ADR-0005 §Q4 promises "what would be sent," which is a superset. **Plus:** no automated byte-faithful regression test for any capability. ADR-0005 mandated `test_preview_byte_faithful.py`; the file doesn't exist. Recommend both: extend preview to return the full `messages` array; add a `RecordingAdapter` test per capability asserting `recorded == preview`.
- **M-11 · Cost-cap degrade swaps provider but doesn't record degrade-state on the audit row** (Streams B F-PB-005, E F-PE-007). When `enforce_pre_call` returns `degraded=True`, the success-path audit row stamps `provider=<fallback>` but does not record the swap reason or the `spent_so_far_usd` / `cap_usd` numbers. A reviewer reading `provider="ollama"` on a household whose `default_provider="anthropic"` must reason backwards. Set `policy_resolved="degraded"` or add a `degraded` column. Also write an `audit_log(action="ai.cost_cap_degraded")` on first-fire-of-the-month so operators see the transition.
- **M-12 · `prompt_hash` domain inconsistent across outcomes** (Stream E F-PE-006). Disabled / no-key audit rows hash `payload.to_dict()` (pre-redaction); gate-blocked / provider-error / success paths hash the redacted body. ADR §Q6 says "SHA-256 of the *redacted* prompt." The "was this prompt seen before" property fails across outcomes. Always hash the redacted body, regardless of outcome.
- **M-13 · `scheduled_job_runs.last_error` stores raw exception strings** (Streams A F-PA-006, G F-PG-005). `runner.py:317, 439` writes `error=str(exc)`. Python exceptions frequently embed row values (`UNIQUE constraint failed: users.email='alice@example.com'`). Long-tail PII via Python error messages. Wrap with class-name + traceback-location, drop full message; or bound retention.
- **M-14 · Pydantic 422 `errors[].input` echoes rejected request-body values** (Stream G F-PG-006). `_sanitize_for_json` strips type-coercion artefacts but not the `input` echo. A 500-char transaction `description` that fails validation hits the response body. Strip `input` from error sanitiser; add regression assertion.
- **M-15 · `import_batches` rows have no delete path; statement lines retain raw bank text** (Streams C F-PC-007 secondary). The `statement_lines.raw_json` column contains full bank-emitted rows, sometimes with branch codes / memos beyond what the curated columns capture. No GC, no per-batch delete endpoint, no cascade other than household-level (which doesn't exist).
- **M-16 · `users.ai_keys_encrypted` is dead storage; per-user keys never read** (Stream E F-PE-003 partial). Column exists at `models/user.py:53`; no code path reads it. Categorize at `categorize.py:295-300` explicitly defers user-level override. ADR-0005 §Q2 promises per-user keys. Pair with H-11 (the wiring is symmetric).
- **M-17 · `daily_insights` handler exists but is never registered with the Runner** (Stream B F-PB-001). `make_daily_insights_handler` is exported but `create_app()` never calls `runner.register_handler("daily_insights", …)`. Background forecast egress cannot fire today, regardless of `ai_policy.capabilities.forecast`. Doc/code drift: `tulip ai status` enumerates `forecast` as if it were live. Either register the handler with a feature flag, or document deferral in PHASE_STATUS / ADR-0005.
- **M-18 · `pending_proposals.ai_invocation_id` and `notifications.ai_invocation_id` have no FK constraint** (Stream F + security audit M-21). Reconfirms the security finding from a privacy angle: H-2 (proposal `created_by_kind` spoof) compounds with the FK gap to allow a member to falsely link a proposal to any AI invocation UUID. Composite FK in next migration window.
- **M-19 · `litellm` is not configured to suppress its outbound version-check / telemetry hooks** (Stream B F-PB-006). No `litellm.telemetry = False`, no explicit `litellm.callbacks=[]`. A future litellm default that turns telemetry on silently begins egressing — defeating ADR-0005's "AI is the only egress" promise without any code change in tulip. Pin off-flags at adapter import.
- **M-20 · Refill-schedule cancel + AI-key forget write no audit row** (Stream C F-PC-010). `runner.cancel(...)` (`runner.py:185-196`) and `DELETE /v1/ai/keys/{provider}` (`ai.py:113-130`) both mutate state silently. Add `AuditLogWriter` writes; pair with security audit M-19 / H-8 / proposal-lifecycle fixes.
- **M-21 · No erasure-verification path: "is this data actually gone?" is unanswerable** (Stream C F-PC-011). Once H-2 lands, nothing answers "show me every place this user's PII appears." Recommend `tulip admin grep-pii --user-id <uuid>` walking JSON / Text columns reporting matches against the user's id / email / display name. Post-delete verification step.
- **M-22 · `tulip backup` archives contain `prompt_json` + `response_text` plaintext when `log_prompts=true`** (Stream B F-PB-007). Locked design (`backup.py:19-23`) is no tarball-level encryption; field-encrypted columns protect high-confidentiality data. But `prompt_json` / `response_text` are not field-encrypted — they're plaintext columns gated by the opt-in. A household that enables `log_prompts` for debugging and forgets to turn it off commits every prompt body to every backup thereafter. Document the trade-off in the `/v1/ai/config` API + `tulip ai config log-prompts on` CLI confirmation; consider field-encrypting these columns.
- **M-23 · Backup rotation policy "30 daily / 12 monthly / 5 yearly" is aspirational, not implemented** (Stream C F-PC-009). `docs/ARCHITECTURE.md:616-620` advertises the policy; `backup.py` has no rotation parameter; no scheduled-job handler exists. Implement as a `scheduled_jobs` handler; document the residue surface in `tulip backup --help` (every deletion that happens after a backup is residue in that archive).
- **M-24 · No object-to-profiling persistence; rejecting one proposal doesn't stop re-generation** (Stream D F-PD-009). Art. 22(1) bar is exceeded by Tulip's explicit-approval gate, but Art. 21(1) "I object to profiling" has no persistent record. Low practical impact today (admin controls AI policy); document the design framing in ADR-0005.
- **M-25 · Per-household email uniqueness — privacy implications undocumented** (Stream A F-PA-007; reconfirms security audit H-7 with a different framing). Same data subject can exist as two `users` rows. Deletion targets a subject not a row; no global "find all users with this email" admin query exists. Document in `THREAT_MODEL.md §2`: email is the only cross-household-correlatable identifier; future erasure flow needs cross-tenant scan via `admin_scope()`.
- **M-26 · `VIEWER` role is nominal — no read-only access tier exists in practice** (Stream F F-PF-001). The enum exists; no router accepts it; `_filter_for_role` only special-cases `"admin"`. Future "read-only spouse view" / "auditor seat" cannot be safely built with `VIEWER` today. Either wire it through `require_role` on every read endpoint, or deprecate until P8+.
- **M-27 · `GET /v1/scheduled-jobs` lists jobs across invisible pools** (Stream F F-PF-006). Per-pool refill endpoints correctly filter; the catch-all listing does not. Modest leak — `pool_id` correlates with separate 404 probes to confirm private-pool existence + refill cadence. Apply `filter_for_role(pool, claims)` per row, or gate the endpoint on `require_role("admin")`.
- **M-28 · No user-facing documentation of data-subject rights** (Stream D F-PD-012). `README.md`, `docs/QUICKSTART.md`, `SECURITY.md` cover privacy in AI context only. Zero hits for GDPR / CCPA / data-subject / right-to / erasure / portability / rectification. Art. 12(1) — controller must provide rights information "in a concise, transparent, intelligible and easily accessible form." Add `docs/USER_RIGHTS.md` mapping each right to the commands that today partially satisfy it; update as H-13 / H-2 / H-14 land.

---

## 7. Low-severity findings

Abbreviated. Each cites the underlying stream finding for traceability.

- **L-1 · `/v1/system/diagnostics` enables long-term install fingerprinting** (Stream G F-PG-001 / security audit F-B-006). Unauthenticated; `master_key_source` + alembic head + writable probe are stable identifiers over time. Localhost-bind or doctor-token gate.
- **L-2 · Problem-Details `instance` echoes URLs containing resource UUIDs** (Stream G F-PG-007). RFC-9457 design feature; relevant at Phase 9 multi-tenant.
- **L-3 · CLI `auth login` / `auth status` round-trips email to stdout** (Stream G F-PG-009). Terminal history + `script(1)` exposure. Multi-user-shared-workstation context. Redact to `a***@example.com` for Phase 9.
- **L-4 · Pre-emptive OpenTelemetry cardinality discipline** (Stream G F-PG-008). Not wired today; ADR-stub before tracing dependency lands. Forbid `OTEL_RESOURCE_ATTRIBUTES` user-identifier injection; use route templates as `http.route`; never `user.email` on a span.
- **L-5 · Journal export embeds household name in header comment** (Stream B F-PB-008). Bytes-to-tax-preparer carries the household name silently. Optional `include_metadata=false` flag.
- **L-6 · Statement-line `is_excluded` is honestly named (positive control)** — exclusion from matching pool, not a deletion claim; not exposed as `DELETE`. Calling out as a deletion-vocabulary positive (Stream C catalogue).
- **L-7 · GDPR Art. 9 *structured* collection: zero** (Stream A F-PA-008). No DOB / phone / postal address / government ID / gender / racial / biometric / health / children's-data columns. Any Art. 9 content rides via free-text (H-17). Add `THREAT_MODEL.md §2.1 Explicitly absent categories` so future audits don't waste cycles.
- **L-8 · `csv_profiles.yaml_body` is not personal data** (Stream A inventory). Mapping configuration; cleared as low-tier in §2.
- **L-9 · `mfa_recovery_codes.used_at` reveals MFA-event timing per user** (Stream C F-PC-004 secondary). Pair with M-6 retention bound.
- **L-10 · `notifications.body` is AI-generated paraphrase of ledger** (Stream A inventory). Free-text inference output; retention bounded by `dismissed_at`-then-prune (M-6 shape extended).
- **L-11 · `provider_response_id` on `ai_invocations` is an opaque vendor-side re-identifier** (Stream A inventory). Pseudonymous bridge to vendor records; document.
- **L-12 · `reconciliation_matches.match_amount` / `confidence` / `matcher_version` is an Art. 22 automated-decision artefact** (Stream A inventory). Audit-trail purpose justifies retention; document the Art. 22 framing in ADR-0004.
- **L-13 · `period.closed_by_user_id` survives user-delete** (Stream A inventory). Pseudonymous after H-2's `actor_user_id` carve-out; calling out for completeness.
- **L-14 · `attachments.filename` retains user-supplied name verbatim** (Stream A inventory). Per ARCHITECTURE §5.2 design; display-only.
- **L-15 · `shadow_transactions.description` mirrors main-ledger description model** (Stream A inventory). Same retention concerns as M-4.
- **L-16 · `pending_proposals.decision_note` is a free-text user input** (Stream A inventory). Lands in audit_log via reject/approve. Reuses M-4 footprint.
- **L-17 · `households.name` rides into journal export header** (overlaps L-5). Often a surname; in a shared journal export, the household name reaches whoever holds the file.
- **L-18 · `sinking_funds.target_date` discloses a known future financial event** (Stream A inventory). High tier in §2; calling out as a behavioural-inference exposure.
- **L-19 · `users.last_login_at` / `totp_enrolled_at` are behavioural timestamps** (Stream A inventory). Pair with M-6 session-pruning.
- **L-20 · Stream-A side file** (deleted from the repo before commit). The Stream-A agent wrote a separate file; folded here. No action — `2026-05-13-deep-privacy-audit-personal-data-inventory.md` removed.
- **L-21 · Stream-C side file** (deleted). Same shape — folded here. `2026-05-13-privacy-deletion-retention-audit.md` removed.
- **L-22 · Stream-D side file** (deleted). Same shape — `2026-05-13-user-rights-audit.md` removed.

---

## 8. Informational / positive controls

Beyond findings, the streams collectively confirmed the load-bearing privacy controls:

- **Human-in-the-loop on every AI output** (Art. 21 / 22 by construction). All AI capabilities write to `pending_proposals.status=PENDING`; explicit `POST /v1/ai/proposals/{id}/approve` required. The byte-faithful preview shows what would leave the boundary before any provider call fires.
- **Prompt bodies opt-in NULL by default** (Art. 5(1)(c)). `ai_invocations.prompt_json` defaults NULL; `households.ai_policy.log_prompts=false` is the default; CLI flip emits a stderr privacy warning. H-1 is the *error-path* gap; the success path is clean.
- **AI capability allowlist closed**. Four capabilities only; no catch-all. Closed `Literal["categorize","nl_query","forecast","agentic"]` mirrored across the Capability type and the `AICapability` enum.
- **No silent provider fallback on 5xx**. `LitellmAdapter` wraps every exception into `AIProviderError`; capabilities write `outcome="provider_error"` and return the non-AI fallback. No retry-with-different-provider loop. `tulip ai status` prints the locked callout.
- **Cost-cap degrade is auditable** end-to-end — `provider="ollama"` lands on degraded rows, distinct from the household's `default_provider`. M-11 is the incomplete-detail finding; the core property holds.
- **AES-256-GCM field-level encryption** on `totp_secret_encrypted`, `notes_encrypted` (accounts + transactions), `external_account_number_encrypted`, `ai_keys_encrypted` (household + user). Security audit M-1 / M-6 (AAD + version byte) are orthogonal; the primitive is correctly chosen and correctly used.
- **AIInvocationWriter is the single chokepoint** for `ai_invocations` inserts, enforced by architecture test (`test_architecture_no_direct_ai_invocation_writes`). The privacy contract rides on this invariant; it holds.
- **Schema CASCADE coverage is comprehensive** from `households.id`. Every household-scoped model declares `ondelete="CASCADE"`. The deletion infrastructure is *schema-ready*; H-2 is the application-layer gap.
- **Composite-FK tenancy** keeps cross-household isolation watertight (covered in the security audit's tenancy stream).
- **No marketing / analytics / cookie surface**. No third-party analytics in `pyproject.toml`. CLI runs locally; API binds to 127.0.0.1 by default. CORS is absent by design.
- **No telemetry / observability libs**. No `opentelemetry`, `prometheus_client`, `sentry-sdk`, `posthog`, `mixpanel`, `segment`. L-4 documents Phase-9 cardinality discipline pre-emptively.
- **No outbound HTTP from reports**. WeasyPrint uses no `url_fetcher`, no `base_url`; templates contain no `<img>` / `<link>` / `@import`. SSRF surface verified absent.
- **GDPR Art. 9 structured collection: zero**. No DOB / phone / postal address / government ID / gender / racial / biometric / health / children's-data columns. Art. 9 risk rides only via free-text (H-17).
- **Editor subprocess** uses `shlex.split` + argv list, no `shell=True`. No `eval` / `exec` / unsafe-deserialization in production code.
- **CLI keyring-by-default** for token storage; JSON-file backend opt-in via `TULIP_TOKEN_STORE`.
- **PENDING-only hard-delete on transactions**, void-via-reversal for POSTED. Accounting-correct trade-off (ADR-0004 §P5.0). Privacy implication is the H-14 reversal-quote pattern; the structure itself is sound.
- **`reconciliation_matches.ledger_transaction_id` is `ON DELETE RESTRICT`** — deleting a matched tx fails loudly. Auditor's mental model preserved.
- **Statement-line `is_excluded`** is honestly named — exclusion from matching pool, not a deletion claim; not exposed under a `DELETE` verb.

---

## 9. Doc/code drift (cross-cut)

Five claims in `docs/THREAT_MODEL.md` / `docs/ARCHITECTURE.md` / ADR-0005 disagree with the code. The security audit captured three; this audit adds two:

| Claim location | Doc says | Code does | Recommended resolution |
|---|---|---|---|
| `THREAT_MODEL.md:19, 40` | "Logging redaction list keeps emails out of logs by default." | `_SENSITIVE_FIELDS` does not include `email` / `user_email`. | Code fix (add to whitelist) — security H-5 + privacy M-3. |
| `THREAT_MODEL.md:20, 37` | Refresh tokens "stored hashed (argon2id) in `sessions`." | Unsalted SHA-256 of the token. | Doc fix preferred — security M-8. |
| `THREAT_MODEL.md:106-107` | "OFX uses `ofxtools` with `defusedxml` under the hood for XXE safety." | `ofxtools` is a regex tokeniser; `defusedxml` is not invoked. | Doc fix — security audit §8. |
| `THREAT_MODEL.md §2` data classification | Email "Medium"; IP / user-agent absent from table; AI metadata conflates `prompt_json` with `prompt_hash`. | Email is personal data full stop (Art. 4); IP + UA stored in `sessions` + `audit_log`; `prompt_hash` rainbow-tablable. | §2 refresh — privacy M-2 / M-3 / M-4 / M-5. |
| **ADR-0005 §Q4** | "`local_only` overrides everything and pins to Ollama regardless of `default_provider`." | Reads `provider = household_policy.get("fallback_provider") or "ollama"`. | **Code fix preferred** — privacy C-1. The ADR is the contract; the code violates it. |
| `THREAT_MODEL.md:129` | "Prompt bodies are not logged by default; only metadata lands by default." | Error paths unconditionally write `response_text=str(exc)[:500]`. | Code fix — privacy H-1. |
| `docs/ARCHITECTURE.md:616-620` | Backup rotation "30 daily / 12 monthly / 5 yearly." | No rotation handler implemented; `backup.py` has no rotation parameter. | Doc fix or implement — privacy M-23. |
| `tulip ai status` | Enumerates `forecast` as a live capability. | `daily_insights` handler not registered with the Runner; forecast egress cannot fire today. | Doc fix or wire — privacy M-17. |

---

## 10. Data-subject rights coverage matrix

| Right | Supported in v1? | Mechanism | Gap |
|---|---|---|---|
| **Art. 15 access** — "show me everything you have on me" | Partial | `/v1/journal/export` (ledger postings); `/v1/reports/audit-log?actor_user_id=` (audit history) | No "everything about me" endpoint. Sessions, AI invocations, notifications, attachments, MFA codes, `users` row itself all unenumerable. See H-13. |
| **Art. 16 rectification** | Partial | `PATCH /v1/transactions/{id}` PENDING-only; void-and-recreate for POSTED. | POSTED-tx description preserved + duplicated in reversal text; profile fields (email / password) write-once. See H-14. |
| **Art. 17 / CCPA §1798.105 erasure** | **No** | None. PENDING-tx hard-delete only. Schema CASCADE wired but no API. | No `DELETE /v1/households/me`, no `DELETE /v1/users/{id}`; attachment ciphertext never deleted; backups not unwound. See H-2 + H-3. |
| **Art. 18 restriction** | **No per-user** | `tulip ai config disable <capability>` is household-wide. | `resolve_policy` accepts `user_policy` but never receives one. See H-11. |
| **Art. 20 portability** | Partial | `GET /v1/journal/export` round-trips through `/v1/journal/import`; format documented lossy. | No export for envelopes / sinking funds / refill schedules / AI invocations / CSV profiles / pending proposals / notifications / reconciliations. See H-13. |
| **Art. 21 objection** | Strong execution control by construction | Every AI output requires explicit human approval; the byte-faithful preview shows wire bytes before sending. | No "I objected" persistence — rejecting one proposal doesn't stop next-day re-generation. See M-24. |
| **Art. 7 consent** | Partial | `log_prompts` opt-in with stderr warning. | No timestamp / actor / version on consent itself; no `audit_log` row when toggled. See M-7. |
| **Per-member rights (admin-as-controller)** | Asymmetric | Admin sees everything; admin can revoke sessions. | No "export member X" / "delete member X" admin tooling. See H-13. |
| **Documentation** | No | — | No `docs/USER_RIGHTS.md`. See M-28. |

---

## 11. Re-evaluation of `THREAT_MODEL.md §2` data classification

The five-tier structural shape is correct (it maps cleanly to ops decisions: encrypt? log-redact? export-restrict?). Six revisions are warranted:

1. **Email is misclassified.** Move to **High** (was "Medium … PII-ish") — Art. 4 §1 covers email full stop. The "logging-redacted by default" footnote is false; either fix the code or fix the doc (M-3).
2. **IP + user-agent missing from the table.** Add as their own row ("Online identifiers / device fingerprint", High). Captured every auth event in two tables (M-2).
3. **Free-text user content needs its own tier (or Art. 9-taint footnote).** Twelve fields scattered through the High row. Elevate to **Critical (free-text)** or footnote the Art. 9 risk (H-17, M-4).
4. **AI metadata split awkwardly.** Current "Highest (Phase 6)" conflates `prompt_json` (firehose, opt-in NULL) with `prompt_hash` (always, pseudonymous). Split:
   - `prompt_json` + `response_text` → **Critical when populated, NULL by default**
   - `prompt_hash` + provider metadata → **High (pseudonymous AI usage record)** (M-5)
5. **`audit_log.before_snapshot` / `after_snapshot` inherit "highest field tier."** Currently treated monolithically as "High-integrity"; integrity claim is fine, confidentiality claim wrong — snapshots embed transaction descriptions (Critical via H-17 / M-4), emails on register, account names, reconciliation IDs. Inherits **Critical** today (M-1).
6. **Add "Multi-presence" column to the table.** For deletion (H-2) and minimisation (M-4), what matters is the full footprint of a field across tables / logs / exports / backups. The existing "where it lives today" column conflates source with derived copies.

Add a subsection **§2.1 Explicitly absent categories**: DOB / phone / address / government ID / gender / sexual orientation / racial-ethnic / biometric / health / children's-data — no structured columns request these. Future audits should not waste cycles confirming absence (L-7).

---

## 12. Prioritised remediation roadmap

Findings sort into three waves matching the security audit's structure.

### Wave 1 — Pre-real-user (Phase 8 hardening, before any multi-user rollout)

| # | Finding(s) | Effort |
|---|---|---|
| 1.1 | **C-1** `local_only` profile lock violation | S |
| 1.2 | **H-1** Error-path `response_text` bypasses opt-in | S |
| 1.3 | **H-2 + H-3** Household + user delete endpoints + attachment GC | L |
| 1.4 | **H-4** Soft-delete masquerading: redact-on-deactivate for 4 entity types | M |
| 1.5 | **H-5 + H-6 + H-7 + H-8 + H-9 + M-27** Cross-user-within-household visibility filter bundle (transactions / reconciliation / AI views / notifications / proposals / scheduled-jobs / audit-log report) — land alongside Phase-9 invite endpoint | L |
| 1.6 | **H-10** `import_batches.summary_json` encrypt (or redact ACCTID) | S |
| 1.7 | **H-11 + M-16** Per-user AI policy + per-user AI keys plumbing | M |
| 1.8 | **H-12** Proposal lifecycle audit + rejected-proposal delete (paired with security audit #222) | S |
| 1.9 | **H-13** `GET /v1/users/me/export` + admin `member-export` command | M |
| 1.10 | **H-14** Rectification: POSTED description-only PATCH; profile-field PATCH + password-change | M |
| 1.11 | **H-16** AI invocation retention + scrub-on-consent-withdrawal | M |
| 1.12 | **H-17 + M-3 + M-4 + M-5 + §11** `THREAT_MODEL.md §2` refresh | S |
| 1.13 | **M-1 + M-22 + M-23** Audit-log tiered retention + backup rotation + `log_prompts` backup-leak doc | M |
| 1.14 | **M-2** Truncate IP, add IP/UA to redaction whitelist | XS |
| 1.15 | **M-7** Consent provenance: audit row on `log_prompts` toggle | XS |
| 1.16 | **M-19** Pin litellm telemetry off-flags at adapter import | XS |
| 1.17 | **M-28 + USER_RIGHTS.md** Document data-subject rights | S |

### Wave 2 — Phase 8 hardening (operations + consistency)

- **M-6** Session + MFA-code daily pruning handler.
- **M-8 + M-9 + M-10 + M-11 + M-12** AI redaction consolidation: centralise per-capability redactor, align profile semantics, byte-faithful preview test, full `messages[]` in preview, cost-cap degrade audit detail, `prompt_hash` domain uniformity.
- **M-13** `scheduled_job_runs.last_error` redaction wrapper.
- **M-14** Strip `errors[].input` from 422 Problem-Details.
- **M-15** Statement-line / import-batch delete cascade (paired with H-2).
- **M-17** Wire or document `daily_insights` handler.
- **M-18** Composite FK on `ai_invocation_id` columns (paired with security audit M-21).
- **M-20** Refill-schedule + AI-key audit rows (paired with security audit M-19).
- **M-21** `tulip admin grep-pii` post-delete verification.
- **M-24** Document Art. 22 satisfaction in ADR-0005.
- **M-26** Decide `VIEWER` role: wire or deprecate.
- All Low findings except L-3 / L-4 (Phase-9 candidates).

### Wave 3 — Phase 9 cloud readiness (re-audit recommended)

- L-1 (`/v1/system/diagnostics` cloud-context).
- L-3 (CLI email-redaction on stdout).
- L-4 (OpenTelemetry cardinality discipline pre-merge).
- M-25 cross-household erasure scan via `admin_scope()`.
- Re-evaluation of every Wave-1/2 deferral under multi-tenant threat model.

---

## 13. References

- [`docs/THREAT_MODEL.md`](../THREAT_MODEL.md) — the threat-model checkpoint this audit refreshes.
- [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) — audit cadence at §10; encryption-at-rest at §7.4; tenancy at §3.3.
- [`docs/adrs/0005-ai-integration.md`](../adrs/0005-ai-integration.md) — ADR-0005 (authoritative AI integration contract; C-1 + H-1 + M-7 + M-10–M-12 verify against this).
- [`docs/audits/2026-05-12-deep-security-audit.md`](2026-05-12-deep-security-audit.md) — the companion security audit; cross-referenced throughout.
- Findings raw output: seven stream-specific reports (PII inventory, data flows, retention/deletion, user-rights, AI-flow re-audit, multi-user-within-household, operational PII) generated 2026-05-13 against `main @ 93ed433`.

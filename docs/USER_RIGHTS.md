# Data-subject rights — operator reference

This is the operator-facing reference for honouring GDPR / CCPA data-subject
rights against a Tulip install. Each section names the right, the article it
sits under, the **command(s)** that satisfy it today, and any **known gap**
with a link to the tracking issue.

Tulip is the *processor* from the data-subject's perspective; the operator
running Tulip is the *controller* under GDPR Art. 4(7). Nothing on this page
is legal advice — it documents what Tulip can do, not what your jurisdiction
or controller obligations require. Pair it with whatever review your privacy
counsel signs off on.

The threat model is in [`THREAT_MODEL.md`](THREAT_MODEL.md); the AI privacy
contract is [`adrs/0005-ai-integration.md`](adrs/0005-ai-integration.md); the
Phase 8 audits are in [`audits/`](audits/).

## Index

| Right | Article | Tulip surface |
|---|---|---|
| [Access (subject access request)](#right-of-access-art-15) | GDPR Art. 15 / CCPA §1798.110 | `tulip user export` |
| [Rectification](#right-of-rectification-art-16) | GDPR Art. 16 | `tulip transactions edit/void`, API `PATCH /v1/transactions/{id}/description`, API `PATCH /v1/users/me` |
| [Erasure ("right to be forgotten")](#right-of-erasure-art-17) | GDPR Art. 17 / CCPA §1798.105 | `tulip transactions delete`, API `DELETE /v1/users/{id}`, API household-erasure flow |
| [Restriction of processing](#right-of-restriction-art-18) | GDPR Art. 18 | `tulip ai config`, API `PUT /v1/users/{me,id}/ai-policy` |
| [Data portability](#right-of-portability-art-20) | GDPR Art. 20 | `tulip user export` + `tulip journal export` |
| [Objection](#right-of-objection-art-21) | GDPR Art. 21 | proposal-approval gate (`tulip ai proposals` / `approve` / `reject`) |
| [Consent withdrawal](#consent-withdrawal-art-7) | GDPR Art. 7 | `tulip ai config log-prompts off` |
| [Information about processing](#information-art-12) | GDPR Art. 12 / 13 / 14 | this document + [`THREAT_MODEL.md`](THREAT_MODEL.md) + [`adrs/0005-ai-integration.md`](adrs/0005-ai-integration.md) |

---

## Right of access (Art. 15)

> "The data subject shall have the right to obtain from the controller
> confirmation as to whether or not personal data concerning him or her are
> being processed, and, where that is the case, access to the personal data."

**Self-service for the subject:**

```bash
tulip user export > my-data.json
```

Returns the `UserDataExport` envelope: the user's own row (with
`password_hash` masked), every session, every `audit_log` row where they were
the actor, every `ai_invocation` they triggered, every proposal they created
or decided, attachment metadata for files they uploaded, MFA recovery-code
status (counts + use-timestamps; never the codes themselves), and every
transaction they created.

**Admin-led export for another household member:**

```bash
tulip household member-export <USER_ID> > member-data.json
```

Same shape as `tulip user export`, scoped to the targeted member. Caller must
have `admin` role; targeting a user outside the caller's household returns
`user.not_found` (404, tenant-scoped to prevent cross-household enumeration).

**Drift convention:** the export endpoint reflects per-user `ai_policy` (see
§Restriction) and any future per-user fields. If you add a `users.*` column,
extend `UserRecordExport` in the same PR.

**Status:** complete since [#241 (PR #314)](https://github.com/rmwarriner/tulip-accounting/pull/314)
+ per-user policy added to the envelope in [#239 (PR #316)](https://github.com/rmwarriner/tulip-accounting/pull/316).
**Known gaps:** none for the access right itself; see §Erasure for backup-residue caveats.

---

## Right of rectification (Art. 16)

> "The data subject shall have the right to obtain from the controller
> without undue delay the rectification of inaccurate personal data
> concerning him or her."

The ledger immutability invariant (postings can't be amended without a void)
means rectification is **scope-split** in Tulip: posting amounts/dates are
never mutated; descriptions, references, and notes are mutated in place under
audit; profile fields go through their own endpoint.

### Transaction descriptions, references, notes — POSTED / RECONCILED

```bash
# The CLI surface today is PENDING-only:
tulip transactions edit <TX_ID>          # opens $EDITOR; in-place for PENDING
tulip transactions void  <TX_ID>          # sign-flipped reversal for POSTED

# The "rectify a wrongly-quoted POSTED counterparty" surface is API-only:
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -H "content-type: application/json" \
  -d '{"description":"Pay [redacted]"}' \
  http://localhost:8000/v1/transactions/$TX_ID/description
```

Three properties to know:

1. **Postings are not touched.** Amounts, accounts, currencies, the date —
   all invariant. The endpoint only mutates `description` / `reference` /
   `notes_encrypted`.
2. **If the source was already voided, the reversal sibling's description
   gets rewritten in place.** The void route writes the reversal as
   `"Reversal of {old_description}: {reason}"`, which would otherwise
   preserve the wrongly-attributed PII in a second row at rest. The
   rectify endpoint substitutes `[redacted]` for the original quote.
3. **The OLD value goes into the audit log.** GDPR Art. 17(3)(e) carve-out
   permits this: the audit row preserves the rectification record until the
   user is later erased, at which point the user-erasure path nulls the
   `before_snapshot` / `after_snapshot` blobs.

### Profile fields — display name, email

```bash
# Self-service via API:
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -H "content-type: application/json" \
  -d '{"display_name":"New Name"}' \
  http://localhost:8000/v1/users/me

# Email change requires re-auth in the body:
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -H "content-type: application/json" \
  -d '{"email":"new@example.com","current_password":"..."}' \
  http://localhost:8000/v1/users/me

# Password rotation revokes every outstanding refresh token:
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "content-type: application/json" \
  -d '{"current_password":"...","new_password":"..."}' \
  http://localhost:8000/v1/auth/password/change
```

**Status:** shipped in [#242 (PR #315)](https://github.com/rmwarriner/tulip-accounting/pull/315).
**Known gaps:** none of these have CLI wrappers yet; the API is the operator
surface. Tracked under the general CLI-completeness backlog.

---

## Right of erasure (Art. 17)

> "The data subject shall have the right to obtain from the controller the
> erasure of personal data concerning him or her without undue delay."

### PENDING transactions — hard delete

```bash
tulip transactions delete <TX_ID>
```

`PENDING` transactions are workflow state, not ledger state — they can be
deleted outright. The corresponding `audit_log` row carries only a structural
snapshot (date, description, reference, status), and those PII fields are
nulled when the *user* is later erased.

### POSTED transactions — void (preserves the audit chain)

```bash
tulip transactions void <TX_ID> --reason "duplicate charge"
```

Posts a sign-flipped sibling that cancels the original. The original row
stays in the ledger for audit integrity; pair with `PATCH /description` (see
§Rectification) if the *description* of the source carries PII you also need
to strip.

### User erasure — admin-led

```bash
# Admin deletes a household member (and their sessions + recovery codes via
# schema-level CASCADE, plus audit_log PII redaction):
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/v1/users/$USER_ID
```

Cascades: `sessions` rows, `mfa_recovery_codes` rows.
Redacts in the same commit: `audit_log.before_snapshot` /
`after_snapshot` / `metadata_` for every row where the deleted user was
actor or entity.
Tombstone: a `user.deleted` audit row records `{deleted_role}` only — no
email, no display name.
Refuses to delete the household's last admin (`409 user.last_admin`).

### Household erasure — two-step, token-gated

```bash
# Step 1 — request a fresh confirmation token (10-minute TTL):
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/v1/households/me/erase-request

# Step 2 — submit the token to authorise the deletion:
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  -H "X-Erasure-Token: $ERASE_TOKEN" \
  http://localhost:8000/v1/households/me
```

Drops every table for the household and garbage-collects orphaned attachment
ciphertext from disk. Irreversible.

### Residue you should know about

- **Audit log:** historically-preserved PII is nulled out when the user is
  erased (`audit_log.before_snapshot` / `after_snapshot` / `metadata_` → `NULL`).
  The row's `actor_user_id` / `entity_id` survive as pseudonyms (FKs aren't
  declared from those columns, so nothing breaks). Art. 17(3)(e) carve-out.
- **AI invocations:** `ai_invocations.prompt_json` / `response_text` are
  scrubbed on `log_prompts` consent withdrawal (#243) and on user erasure
  (cascades through `actor_user_id`). The TTL handler also deletes rows older
  than `AI_INVOCATION_RETENTION_DAYS` (90).
- **Past exports:** any `tulip user export` JSON the subject downloaded
  previously is their own copy; the controller has no recall mechanism. This
  is expected for a portability right.
- **Backups:** any DB backup taken before the erasure still contains the
  data. The operator is responsible for backup rotation per their own
  retention policy. [#245](https://github.com/rmwarriner/tulip-accounting/issues/245)
  tracks formalising audit-log + backup tiered retention.
- **External processors:** if a tenant has opted into cloud AI
  (`tulip ai set-key <provider> <key>`), prompts sent to that provider before
  the erasure are not under Tulip's control. Local-only profile
  (`tulip ai config set-capability <cap> local_only`) prevents this for
  future calls.

**Status:** user + household erasure shipped in [#235 (PR #235)](https://github.com/rmwarriner/tulip-accounting/pull/235);
audit redaction wired through the same PR.
**Known gaps:** no CLI wrapper for `DELETE /v1/users/{id}` or the household
erasure flow; backup-side retention tracked in [#245](https://github.com/rmwarriner/tulip-accounting/issues/245).

---

## Right of restriction (Art. 18)

> "The data subject shall have the right to obtain from the controller
> restriction of processing where one of the following applies…"

In Tulip the practical surface is **AI processing restriction** — the user
can dial AI strictness up from the household floor.

### Household-wide (admin only)

```bash
# Disable every capability — no AI calls fire for any household member:
tulip ai config set-capability nl_query disabled
tulip ai config set-capability categorize disabled
tulip ai config set-capability forecast disabled
tulip ai config set-capability agentic disabled

# Or switch redaction to strict / local-only:
tulip ai config profile strict
tulip ai config profile local_only      # locks provider to ollama
```

### Per-user (members + admins) — API-only today

```bash
# Self — ratchet up from the household floor (max-severity wins):
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "content-type: application/json" \
  -d '{"capabilities":{"nl_query":{"policy":"disabled"}}}' \
  http://localhost:8000/v1/users/me/ai-policy

# Reset to inherit household:
curl -X PUT -H "Authorization: Bearer $TOKEN" -d '{}' \
  http://localhost:8000/v1/users/me/ai-policy

# Admin can also set policy for another member:
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "content-type: application/json" \
  -d '{"capabilities":{"forecast":{"policy":"disabled"}}}' \
  http://localhost:8000/v1/users/$USER_ID/ai-policy
```

The household sets the *floor*; the user can only ratchet *up* (stricter).
A user who sets `permissive` on a capability the household has at
`requires_approval` still gets `requires_approval` at resolve time. This is
the "max-severity wins" property enforced in `tulip_ai.policy.resolve_policy`.

**Status:** shipped in [#239 (PR #316)](https://github.com/rmwarriner/tulip-accounting/pull/316).
**Known gaps:** no CLI wrapper for `PUT /v1/users/{me,id}/ai-policy`;
restriction of *non-AI* processing (transactions, reports, etc.) is not a
right Tulip currently offers — the only AI surfaces are the four AI
capabilities, and the rest of the system processes data the subject
themselves entered.

---

## Right of portability (Art. 20)

> "The data subject shall have the right to receive the personal data
> concerning him or her, which he or she has provided to a controller, in a
> structured, commonly used and machine-readable format."

```bash
# Per-user envelope (everything the subject's id is attached to):
tulip user export > my-data.json

# The household's complete ledger (the operator decides whether to share):
tulip journal export > household.journal

# Specific import batch (statement-line round-trip):
tulip imports show <BATCH_ID> --json > batch.json

# Transactions in a structured machine-readable form:
tulip --json transactions list > transactions.json
```

`tulip user export` is the load-bearing surface. `tulip journal export`
produces hledger-format text that can be ingested by hledger, beancount, and
other plain-text accounting tools — that's the format-interoperability arm
of Art. 20.

**Status:** complete since [#241 (PR #314)](https://github.com/rmwarriner/tulip-accounting/pull/314)
and journal export shipped in Phase 7.
**Known gaps:** none.

---

## Right of objection (Art. 21)

> "The data subject shall have the right to object … to processing of
> personal data concerning him or her … including profiling based on those
> provisions."

Tulip's AI-driven automated decisions ("propose this category" / "propose
this budget" / "propose this refill") are gated **by construction** behind
human approval — no AI proposal mutates state without an
`approve` / `reject` decision recorded as an `audit_log` row.

```bash
# List proposals waiting on you:
tulip ai proposals

# Reject one (it stays in the chain for audit; no state change beyond the row):
tulip ai reject <PROPOSAL_ID>
```

Two stronger restrictions live alongside, both already covered above:

- **§Restriction** (per-user `ai_policy.capabilities[*].policy = disabled`) —
  blocks the AI from generating proposals for this user at all.
- **`tulip ai config log-prompts off`** — withdraws consent to retain the
  prompt + response bodies; runs an atomic scrub of historic
  `ai_invocations.prompt_json` / `response_text` in the same commit (#243).

**Status:** the proposal gate has been the design from the start
([ADR-0005 §Q3](adrs/0005-ai-integration.md)). The `actor_kind=ai_agent`
audit row infrastructure shipped in #311; the consent-provenance audit
shipped in [#247 (PR #317)](https://github.com/rmwarriner/tulip-accounting/pull/317).

---

## Consent withdrawal (Art. 7)

> "Consent should be given by a clear affirmative act … the data subject
> shall have the right to withdraw his or her consent at any time."

The two relevant consent surfaces:

### Prompt-log retention

```bash
# Withdraw consent to retain prompt + response bodies:
tulip ai config log-prompts off
```

Atomic with the toggle: a household-scoped `UPDATE` nulls
`ai_invocations.prompt_json` and `ai_invocations.response_text` on every
existing row. The row, `prompt_hash`, and cost metadata survive for the
audit chain. The scrub itself is recorded as
`audit_log.action="ai.prompt_log_scrubbed"` (#243), and the toggle is
recorded as `audit_log.action="ai.consent_changed"` (#247) — "when did
consent change and by whom" is answerable from the audit log.

### Provider-key revocation (effectively, BYOK consent)

```bash
# Household admin removes a provider key — no more AI calls go out via that provider:
tulip ai forget-key anthropic

# Per-user (API-only today):
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/v1/ai/keys/me/anthropic
```

Removing a key doesn't *delete* historic invocations — those are governed by
the prompt-log + TTL controls above — but it does ensure no further outbound
calls fire on the user's behalf via that provider.

**Status:** prompt-log toggle from Phase 6; consent-provenance audit in
[#247 (PR #317)](https://github.com/rmwarriner/tulip-accounting/pull/317);
per-user AI keys in [#239 (PR #316)](https://github.com/rmwarriner/tulip-accounting/pull/316).

---

## Information about processing (Art. 12 / 13 / 14)

The "transparent information" obligation is satisfied by:

- [`THREAT_MODEL.md`](THREAT_MODEL.md) §1 (trust boundaries), §2 (data
  classification), §5.3 (AI integration constraints).
- [`adrs/0005-ai-integration.md`](adrs/0005-ai-integration.md) — the
  authoritative AI privacy contract.
- [`audits/2026-05-13-deep-privacy-audit.md`](audits/2026-05-13-deep-privacy-audit.md) —
  the document-only deep privacy audit, finding-by-finding.
- This page.

What Tulip *doesn't* ship: a privacy policy template aimed at the
controller's own data subjects. That's controller-specific and out of scope
for the project.

---

## Doc/code drift convention

When you ship a Wave-1 privacy or rights issue, **update this page in the
same PR**. A right that ships a new CLI wrapper or API endpoint should land
its row in the relevant section before it's merged — not in a follow-up.

The phase-status page (`docs/PHASE_STATUS.md`) is the index of *what
shipped*; this page is the index of *what an operator can actually run*.
Both should agree.

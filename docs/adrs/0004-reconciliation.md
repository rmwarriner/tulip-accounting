# ADR 0004 — Reconciliation and statement-import design

**Status:** Proposed (2026-05-04) — to be reviewed before any Phase 5 code lands.
**Phase:** 5 (Importers + reconciliation).
**Supersedes:** None.

---

## Context

[ARCHITECTURE.md §5.8](../ARCHITECTURE.md) (reconciliation) and [§5.9](../ARCHITECTURE.md) (importers) commit Tulip to two intertwined Phase 5 capabilities: ingest of bank statements (OFX, QIF, CSV) and matching of those statements against the existing ledger. The architecture sketch is intentionally light — most of the design pressure lands one PR at a time, which is the failure mode that produced [issue #101](https://github.com/rmwarriner/tulip-accounting/issues/101). ADR-0001 (shadow ledger) and ADR-0002 (scheduler primitive) set the precedent of resolving shape questions in an ADR before code; this ADR continues that discipline for Phase 5.

What §5.8 / §5.9 leave open:

1. **Match candidates** — what produces a candidate match (date window, amount tolerance, counterparty heuristic, account)?
2. **Confidence scoring** — scalar or bucketed; rule-based or learned?
3. **Partial / split matches** — one bank line ↔ many ledger transactions (or vice versa); how represented?
4. **Manual override flow** — when the algorithm is wrong, what's the correction UX, and where is the correction stored?
5. **Unmatched inbox** — bank lines with no candidate go where; how does the user triage?
6. **Idempotency** — re-importing the same statement file: what's dedup'd, on what key?
7. **State model** — is "reconciled" a flag on the ledger row, a separate-table row, or a status on the bank-line side?
8. **Statement-format normalization** — OFX, QIF, CSV: what's the common-denominator import schema?
9. **Audit trail** — who reconciled what when; reversible?

Two pieces of context constrain the answers.

**Issue #55 (transaction void / PENDING-only edit) is prerequisite infrastructure.** Reconciliation revert, un-reconcile, and unmatched-cleanup all need a void mechanic on POSTED transactions and an in-place edit path on PENDING ones. #55 was deferred from Phase 4 specifically so the rules could be designed with reconciliation in view. It must ship first.

**The schema sketch in §4.1 advertises columns that do not yet exist.** §4.1 lists `transactions.cleared_at`, `transactions.reconciled_at`, `transactions.reconciliation_id`, and `transactions.imported_from_id` as if they were already in the schema. They are not — `20260429_2116_c2f963036df3_initial_schema.py` ships only `id`, `household_id`, `date`, `posted_at`, `description`, `reference`, `status`, `notes_encrypted`, `created_by_user_id`, `created_at`, `updated_at`. The `attachments`, `attachment_links`, `reconciliations`, and `import_batches` tables are likewise sketched but unbuilt. P5.1 is the first migration that touches the transaction shape after the initial schema — this ADR treats those columns as **to be added**, not as facts on the ground.

This ADR is opinionated. Where a knob would otherwise sprout (Levenshtein threshold, date window, currency tolerance), the choice is "decide a default, leave it private to the matcher, revisit on the first real complaint" — not a tunable.

## Decision

The structure below answers the nine questions in order. Where a worked example clarifies, one is given.

### Q1 — Match candidates

A candidate match is produced when **all four** of the following hold for a `(statement_line, ledger_transaction)` pair, scoped to the same `(household_id, account_id)`:

1. **Account is the same.** `statement_line.account_id == ledger_posting.account_id`. The matcher only looks at postings whose `account_id` matches the imported account; the contra-postings (income / expense legs) are irrelevant for matching.
2. **Amount is exact** (per currency). `|statement_line.amount - posting.amount| == 0` to the smallest representable unit. Tolerance is **deferred until first user complaint** — banks don't issue inexact amounts on the same currency, and the temptation to introduce an `amount_tolerance` knob to paper over a sign-flip bug is real.
3. **Date is within ±3 days** of the statement-line date. Three days is the published value in §5.8 and accommodates the most common drift (weekend posting, settlement delay). Configurable per household, but the configuration knob does **not** ship in v1 — `MATCH_DATE_WINDOW = timedelta(days=3)` lives as a private constant in `tulip_core.reconciliation.matcher`.
4. **The ledger transaction is not already reconciled.** A row whose `reconciled_at IS NOT NULL` is excluded from candidate pools. (Cleared-but-not-reconciled rows are still candidates.)

If all four hold, the pair becomes a candidate. The candidate's confidence (Q2) is determined separately, by counterparty-heuristic comparison.

The **counterparty heuristic** (description-similarity check) does **not** gate candidacy — it only feeds confidence. This is a deliberate choice: a $42.17 charge on the right day at the right account should always be a candidate, even if the bank truncates "PAYPAL *AMAZON" to "PAYPAL" in the statement and the user wrote "Amazon" in the ledger description.

#### Worked example A — straightforward candidate

User imports the May statement for Checking. One statement line:

```
2026-05-12  PAYPAL *AMAZON   -42.17 USD
```

Ledger has one POSTED transaction dated 2026-05-13 with description "Amazon — Kindle book" containing a Checking posting of $-42.17 USD. Same account, exact amount, dates 1 day apart, not reconciled — **candidate**. Confidence will be `medium` or `high` depending on description fuzzy match (Q2).

### Q2 — Confidence scoring

**Bucketed (`high` / `medium` / `low`), rule-based.** No machine learning in v1.

- **`high`** — exact amount, same date (±0 days), description fuzzy-match score ≥ 0.9 by `rapidfuzz.fuzz.token_set_ratio` divided by 100. The match auto-applies on `apply` unless the user has overridden it.
- **`medium`** — exact amount, date within ±3 days, fuzzy-match score ≥ 0.6, OR exact amount + same date + no description match. Surfaced in the proposal UI, requires the user to confirm.
- **`low`** — exact amount, date within ±3 days, fuzzy-match score < 0.6. Shown as a suggestion next to the unmatched statement line; user opts in.

The fuzzy-match library is `rapidfuzz` (~1 MB; no native deps required at runtime). It joins `python-dateutil` from ADR-0002 as a real new dep.

A scalar confidence (e.g., 0.0–1.0) was rejected: every consumer of the confidence (UI, CLI, audit log, the auto-apply rule) needs a bucket boundary anyway. Storing the bucket directly removes one layer of "who picked the threshold" indirection. The buckets themselves are private to `tulip_core.reconciliation.matcher`; if a future learned model wants to produce a scalar, it converts to a bucket at the boundary.

**A learned model is explicitly deferred to Phase 6** as part of the AI capability menu. The auto-categorization seam (designed below) is the same seam a learned matcher would plug into; one DI hook serves both.

### Q3 — Partial / split matches

Three shapes occur in practice; the model handles them via a **`reconciliation_matches`** table that can be many-to-many between statement lines and ledger transactions:

- **1:1** — one statement line, one ledger transaction. The common case.
- **N:1** — many statement lines, one ledger transaction. Example: a single ledger payroll transaction, but the bank reports the deposit as multiple lines (gross + reversals). One `reconciliation_match` row per `(statement_line, ledger_transaction)` pair, all sharing the same `ledger_transaction_id`.
- **1:N** — one statement line, many ledger transactions. Example: a Costco run booked as two ledger transactions (groceries split + party-supplies split), but the bank reports a single $80 charge. One `reconciliation_match` row per pair.

The constraint: **the sum of matched amounts on each side must equal the sum on the other side, per currency.** The matcher enforces this when a multi-row match is constructed; the API enforces it on `POST /v1/reconciliations/{id}/match`. A `reconciliation_match` row carries `match_amount` (the portion of the statement line / transaction that this match covers) so the same statement line can fan out to multiple ledger rows whose amounts sum to the line's amount.

**Partial-of-one is rejected for v1**: a $100 statement line cannot match $60 of a $100 ledger transaction (leaving $40 of the ledger transaction "for some other future statement line"). If users need this, they restructure with the void-and-resplit mechanic from #55. The constraint avoids a footgun where statement lines silently partially-clear ledger rows and the residual gets lost.

### Q4 — Manual override flow

Three correction shapes exist. All three flow through API endpoints; all three write to `audit_log`.

- **"This match is wrong" — reject a match.** `POST /v1/reconciliations/{id}/matches/{match_id}/reject`. Deletes the `reconciliation_match` row, returns the statement line and the ledger transaction to the unmatched pool, increments `audit_log` with `action=reconciliation_match_reject`. The user may then create a different match.
- **"This match is right but the algorithm missed it" — manual match.** `POST /v1/reconciliations/{id}/matches` with `{statement_line_id, ledger_transaction_id, match_amount}` (or a list, for split matches). Same row shape as auto-matches, distinguished by `created_by` (`user` vs `matcher`) and `confidence` (NULL for manual).
- **"This statement line should never have been imported" — soft-delete a statement line.** `DELETE /v1/imports/{batch_id}/lines/{line_id}`. Sets `statement_lines.is_excluded = true` (no hard delete; the original file is in `attachments` for audit). An excluded line is not a candidate for matching and does not appear in the unmatched inbox (Q5).

The override is **stored on the same `reconciliation_matches` row** as the auto-match would have used — a manual match has `created_by_user_id` populated, `matcher_version IS NULL`, and `confidence IS NULL`. The shape is uniform; the provenance distinguishes them.

### Q5 — Unmatched inbox

There are two unmatched populations, surfaced as two queries on the same `/v1/reconciliations/{id}` resource.

**Unmatched statement lines** — `statement_lines` with `reconciliation_match_id IS NULL` and `is_excluded = false`. These are the bank's view that the ledger doesn't cover. The user's options:

1. **Promote to PENDING transaction** (the §5.8 default behavior). API: `POST /v1/imports/{batch_id}/lines/{line_id}/promote`. Creates a single PENDING transaction with one Checking-side posting from the statement line and a placeholder `Imbalance:Unknown` posting; the user categorizes via the same flow as a manually-added PENDING. The §5.8 wording "unmatched statement lines become PENDING transactions awaiting categorization" is the literal contract.
2. **Match manually** to an existing ledger transaction (Q4).
3. **Exclude** (Q4) if the line was a duplicate or shouldn't apply.

**Unmatched ledger transactions** — POSTED ledger rows in the imported account that fall within the statement's `[start_date, end_date]` window and have no `reconciliation_match` for this reconciliation. These are the ledger's view that the bank doesn't cover. The user's options:

1. **Match manually** to an unmatched statement line.
2. **Void** via #55 (the user posted something the bank never saw — typo, duplicate ledger entry, etc.).
3. **Carry forward explicitly** — the user asserts this transaction is real and will appear on a *later* statement (in-flight check, pending ACH). The reconciliation completes; the carry-forward fact is recorded so the user doesn't lose track of it.

**Carry-forward is an explicit action**, not an implicit "anything left over rolls forward." It writes a denormalized pointer on the transaction (`transactions.carried_forward_from_reconciliation_id`, NEW column in P5.1) referencing the reconciliation in which the user first marked it. API: `POST /v1/reconciliations/{id}/carry-forward` with `{transaction_ids: [UUID, ...]}`. The action is reversible (`DELETE` on the same path) and writes a `transaction_carry_forward` audit row.

The carry-forward flag is auto-cleared in two cases:

- The transaction is matched in a later reconciliation (the matcher sets `reconciliation_id`, the chokepoint nulls `carried_forward_from_reconciliation_id` in the same UPDATE).
- The transaction is voided via #55 (the void-chokepoint nulls the carry-forward FK).

The flag does **not** clear when the *source* reconciliation is reverted — the user's intent ("I expect this on a future statement") is independent of whether the source reconciliation still exists. If a user reverts the source reconciliation and the in-flight check never arrives, they explicitly clear the carry-forward when they decide it was a typo.

The CLI inbox surface is `tulip reconcile --account ACCOUNT`, an interactive screen with two panes: unmatched-bank-lines on the left, unmatched-ledger-rows on the right, candidate matches highlighted between them. A `C` key marks the highlighted ledger row as carry-forward; a `?` key shows carry-forwards from previous reconciliations of the same account.

### Q6 — Idempotency

Re-importing the same statement file dedups on **`(household_id, account_id, content_hash)`**, where `content_hash` is the SHA-256 of the **raw file bytes** (not of the parsed payload). The check is at upload time, before parsing.

Two import_batches with the same hash on the same account are rejected with `import.duplicate_file` (409 Problem Details). The user can override with `?force=true` in which case a new `import_batch` row is created and the matcher will produce duplicate candidates against statement lines that are byte-identical to a previous batch's lines — those candidates surface as `medium` confidence (since the first batch already matched the ledger txs, those ledger txs are reconciled and not eligible) and the user can exclude or merge.

`content_hash` is what's stored on `attachments.content_hash` already (§4.1). Reusing the column is the cheap shape; per-batch idempotency is a unique constraint:

```sql
CREATE UNIQUE INDEX ix_import_batches_idempotency
  ON import_batches(household_id, account_id, source_file_attachment_id);
```

The constraint is on the attachment FK (which is itself unique-by-hash via the `attachments` row), not on the hash directly, so the FK and the index do the work together.

**Why hash the file, not the parsed payload?** Banks issue OFX files with timestamps in the header; parsing-then-hashing produces stable hashes across timezone differences but loses sensitivity to "the bank reissued the same statement after correcting an error." Hashing the bytes is conservative — a corrected re-issue gets a new hash and is treated as a new file, with the matcher disambiguating overlapping lines. Yes, this means a user who downloads the same OFX twice with a different `DTSERVER` field gets two batches; we accept this, because the alternative ("the matcher silently merged what looked like the same statement") is worse for an accounting tool.

### Q7 — State model

**Hybrid: separate `reconciliations` table is the audit aggregate; denormalized columns on `transactions` are the join shortcut.** The `TransactionStatus` enum is **not** changed.

- `reconciliations` (one row per "user closed the May 2026 Checking statement against the ledger" event) is the truth.
- `reconciliation_matches` (the M:N row from Q3) links statement lines to ledger transactions through a reconciliation.
- `transactions.reconciliation_id` (NEW column, P5.1 migration) is a denormalization populated by the matcher when a transaction is fully matched on the statement side. Allows fast "is this transaction reconciled" queries without a join.
- `transactions.cleared_at` (NEW column, P5.1) — set by the manual-clear flow (`POST /v1/transactions/{id}/clear`). Distinct from `reconciled_at`: cleared = "I, the user, see this on my own records"; reconciled = "I matched this against an actual statement." The §5.8 distinction.
- `transactions.reconciled_at` (NEW column, P5.1) — denormalization of `reconciliations.completed_at` for the linked reconciliation.
- `transactions.imported_from_id` (NEW column, P5.1) — `import_batches.id` if the transaction was created via promotion of a statement line. NULL for hand-entered.
- `transactions.carried_forward_from_reconciliation_id` (NEW column, P5.1) — see Q5.
- `TransactionStatus` enum is unchanged. RECONCILED already exists; setting it remains the API's job. The status is computed: `PENDING | POSTED | RECONCILED` is the existing enum, and `reconciled_at IS NOT NULL` ⟹ `status = RECONCILED`. The redundancy is intentional — it's the same denormalize-for-fast-query pattern as the rest of the schema.

Why not put the state on the bank-line side (`statement_lines.is_matched`)? Statement lines are an artifact of an import_batch; they belong to the import-side aggregate. Reconciliation status belongs to the ledger-side aggregate. Mixing them produces "is this line matched, and if so, is the matched transaction reconciled, and if so, when did the reconciliation complete" — three flags on the wrong side of the relationship. The current shape puts each flag where its primary reader expects it.

**Un-reconcile** is a row deletion on `reconciliations` with cascade behavior:

- `reconciliation_matches` rows cascade-delete (foreign key).
- `transactions.reconciliation_id` is set to NULL by the un-reconcile API (not by FK cascade — the user must pass `?cascade=true` to confirm).
- `transactions.reconciled_at` is set to NULL.
- `statement_lines` are returned to the unmatched pool (their `reconciliation_match_id` is NULL after the cascade).
- `transactions.carried_forward_from_reconciliation_id` is **left intact** for transactions whose carry-forward source is the reconciliation being deleted — see Q5.
- An `audit_log` row is written.

The un-reconcile API is `DELETE /v1/reconciliations/{id}` with `?cascade=true`. It does **not** delete the `import_batch` — the file and statement lines stay. Only the matching is undone.

### Q8 — Statement-format normalization

The common-denominator schema is **`StatementLine`**, a frozen dataclass in `tulip_core.reconciliation.statement_line`:

```python
@dataclass(frozen=True, slots=True)
class StatementLine:
    id: UUID                    # generated by importer
    import_batch_id: UUID
    line_number: int            # 1-based, in source-file order
    posted_date: date           # the date the bank says the line posted
    amount: Money               # signed; positive = credit to the account
    description: str            # bank's description, normalized whitespace
    counterparty: str | None    # FITID, payee, MEMO when distinguishable
    reference: str | None       # check number / FITID when present
    raw: dict[str, str]         # the format-specific original fields, for audit
```

Each importer (`tulip_importers.ofx`, `tulip_importers.qif`, `tulip_importers.csv`) is responsible for producing a list of `StatementLine` from its source file. The matcher consumes only `StatementLine` and never sees raw OFX / QIF / CSV. This is the seam that lets `tulip-importers` be swapped per-format without touching `tulip-core`.

The `raw` field carries everything the format produced that didn't fit the common schema, as a flat string-string dict, for audit-trail completeness. It's never read by the matcher.

**Per-format notes:**

- **OFX** — `ofxparse` library (per ARCHITECTURE.md §5.9). Verify maintenance status at P5.2.a kickoff; if stale, swap to `ofxtools`. `STMTTRN.FITID` populates `reference`; `NAME` + `MEMO` concatenate into `description`; `TRNAMT` populates `amount`; `DTPOSTED` populates `posted_date`. `FITID` is the **only stable identifier** for a transaction within a single financial institution; it is preserved in `statement_lines.fitid` (NEW column on `statement_lines`) for cross-statement dedup hints, distinct from `reference`.
- **QIF** — custom parser (small format). `D` field → `posted_date`, `T` → `amount`, `P` → `counterparty`, `M` → `description`, `N` → `reference`. No FITID equivalent; `fitid` is NULL for QIF lines.
- **CSV** — column-mapping profiles in a per-household `csv_profiles` table. Profiles are owned by the household; YAML is the **export / import format only**, never a runtime storage location. CLI: `tulip imports profiles {add,edit,list,show,delete,export,import}`. `export` emits YAML on stdout; `import` reads YAML and writes a DB row. This puts profiles inside the existing backup/restore boundary (the SQLite DB), inside the existing tenant scope (composite FK on `household_id`), and inside the field-level encryption story (P1.6) — no parallel filesystem state to lose, encrypt, or back up. Profile shape (the YAML the export emits and the import accepts):

  ```yaml
  name: chase-checking
  date_column: "Posting Date"
  date_format: "%m/%d/%Y"
  amount_column: "Amount"
  amount_negative_means: "debit"   # or "credit"
  description_column: "Description"
  reference_column: "Check or Slip #"
  encoding: "utf-8"
  delimiter: ","
  skip_header_rows: 1
  ```

### Q9 — Audit trail

Three layers of audit:

1. **`audit_log` rows** — one per user-initiated action on the reconciliation / import surface. Existing `audit_log` schema already supports new `entity_type` values: `reconciliation`, `reconciliation_match`, `import_batch`, `statement_line`. Actions added: `import_create`, `import_apply`, `import_revert`, `reconciliation_create`, `reconciliation_match_create`, `reconciliation_match_reject`, `reconciliation_complete`, `reconciliation_revert`, `transaction_clear`, `transaction_unclear`, `transaction_carry_forward`. The existing `before_snapshot` / `after_snapshot` JSON columns capture the diff.
2. **`reconciliation_matches.{created_by_user_id, matcher_version, confidence}`** — every match knows who or what created it.
3. **The original file as an attachment** — `import_batches.source_file_attachment_id` references an `attachments` row whose bytes are the raw uploaded file. The attachment is **encrypted at rest** using the field-level encryption helpers from P1.6 (`tulip_storage.encryption.encrypt_field`); v1 stores the ciphertext on the local filesystem under `~/.local/share/tulip/attachments/<content_hash>` per #74's note. Filename, content type, size, and SHA-256 are stored in clear on the `attachments` row; only the bytes are encrypted.

**Reversibility:**

- **Import revert** — `POST /v1/imports/{id}/revert`. Voids (via #55) every transaction created from this batch's statement-line promotions. Sets `import_batches.status = 'reverted'`. Statement lines remain (for audit); their `is_excluded` is set to true so they don't reappear in matching. Matched transactions in the batch — i.e., ledger txs that were already there before import and got reconciled by this batch — are **not** voided; they're un-reconciled (their `reconciliation_id` is cleared).
- **Reconciliation revert** — `DELETE /v1/reconciliations/{id}?cascade=true`. As described in Q7.
- **Match revert** — `POST /v1/reconciliations/{id}/matches/{match_id}/reject`. As described in Q4.
- **Carry-forward revert** — `DELETE /v1/reconciliations/{id}/carry-forward/{transaction_id}`. As described in Q5.

All four are real audit-log actions. None is a hard delete of user-visible data.

## Consequences

### Positive

1. **The matcher is pure domain logic.** `tulip_core.reconciliation.matcher.find_candidates(statement_lines: Sequence[StatementLine], ledger_window: Sequence[Transaction]) -> list[CandidateMatch]` is callable from a unit test with no fixtures, no DB, no clock injection. The hard test surface — "exact amount, date drift, fuzzy description across the seven combinations of confidence buckets" — is a hypothesis property test.
2. **The importer-vs-matcher boundary is sharp.** Importers produce `StatementLine`. Matcher consumes `StatementLine`. Promotion to PENDING transaction is a separate step, on the API side. Each can be tested without the others.
3. **The auto-categorization seam is a DI hook**, identical in shape to the runner's `register_handler`:

   ```python
   class Categorizer(Protocol):
       async def categorize(
           self,
           line: StatementLine,
           household_context: HouseholdContext,
       ) -> CategorizationResult: ...

   def register_categorizer(categorizer: Categorizer) -> None: ...
   ```

   The v1 default is `RuleBasedCategorizer` (matches description regex against a per-household rule list, falls back to `Imbalance:Unknown`). Phase 6 swaps in `AICategorizer` without touching the importer code.
4. **Idempotency on file hash means re-running an importer is safe.** A user re-running `tulip import ofx may.ofx --account checking` after a transient error gets a 409 the second time, not a duplicate import.
5. **The two-source-of-truth concern from Q7 is bounded.** `transactions.reconciled_at` and `transactions.reconciliation_id` are denormalizations; the truth is `reconciliations` and `reconciliation_matches`. The same architecture-test pattern that bans direct writes to `shadow_postings` extends to direct writes that change `reconciled_at` — only `tulip_storage.repositories.ReconciliationRepository.complete()` is allowed to set it.
6. **Un-reconcile, revert, and carry-forward are first-class**, not afterthoughts. Every action that creates state has a documented inverse, and the inverse writes its own audit row.
7. **CSV profiles share the rest of Tulip's backup / encryption / tenancy boundary.** A user who restores a household DB gets their profiles back. A user who restores onto a different host doesn't have to copy a parallel `~/.config` tree. Export / import via YAML serves the "share a profile with another user" use case without making the storage location of-record live in two places.

### Negative

1. **P5.0 is a real prerequisite.** The void-and-PENDING-edit work from #55 must land before any reconciliation slice. This adds one slice to the phase; it does not change the eventual shape, but anyone planning to "skip ahead to importers" cannot.
2. **`rapidfuzz` is a new dep.** ~1 MB; pure-Python fallback exists but is much slower. Justified by the matcher's hot path.
3. **Two-side denormalization** (state on both `reconciliations` and `transactions`) means the writer chokepoint must keep them consistent. This is the same `post_transaction` chokepoint pattern as ADR-0001's main-ledger-vs-shadow-ledger writer; the same `tulip_storage.repositories.ReconciliationRepository` becomes the analogue chokepoint here.
4. **Carry-forward adds a column and an action surface.** The alternative (implicit carry-forward — anything left unmatched silently rolls into the next statement) was simpler but lost the user's intent. Explicit costs one column + one API verb + one audit action; gets us "what was I expecting?" answerable without inspecting the diff between consecutive reconciliations.

### Neutral

1. **`StatementLine` is in `tulip-core`, not `tulip-importers`.** The reverse-dependency direction matters — `tulip-importers` will depend on `tulip-core` for the `StatementLine` shape. The matcher in `tulip-core` doesn't know any format exists. This is the same dependency direction as the existing `Posting` / `Transaction` / accounting engine.
2. **Statement-line storage is its own table.** `statement_lines` is a new table referencing `import_batches`. We could have stored them as JSON on `import_batches.lines_json`; we don't, because the matcher repeatedly reads them per-account-window query and an indexed table is cheaper than parsing a JSON column.
3. **`fitid` on `statement_lines` is OFX-specific** but lives on the common table, NULL for QIF / CSV. Per-format columns proliferating would be a worse smell than one nullable column.

### Compliance posture — Art. 22(2)(a) framing for matcher artefacts (added #352, priv audit L-12)

`reconciliation_matches.{match_amount, confidence, matcher_version}` are the
artefact-records of an automated decision (the matcher's "this statement
line corresponds to this ledger transaction" conclusion). GDPR Art. 22(1)
bars decisions based solely on automated processing with legal /
significant effects — but Art. 22(2)(a) carves out automated decisions
"necessary for entering into, or performance of, a contract between the
data subject and a data controller." Bank reconciliation falls
comfortably inside that carve-out: it is core accounting recordkeeping
the data subject (the household) explicitly performs against their
own ledger; no third party is profiled by the decision.

The audit-trail retention purpose (showing *why* a given match
happened, even years later) is what justifies the per-row
`matcher_version` + `confidence`: an auditor reconstructing
"did this auto-match happen correctly?" needs both the inputs and
the algorithm version. Cross-link: [`docs/USER_RIGHTS.md`](../USER_RIGHTS.md)
Art. 22 row, ADR-0005's "Compliance posture" subsection on the
parallel framing for AI proposals.

### Slice ordering

| Slice | What ships | Issue ref |
|---|---|---|
| **P5.0** | Transaction void via reversal + PENDING-only PATCH / DELETE. New `transactions.voided_by_transaction_id`, `voided_at`. API: `POST /v1/transactions/{id}/void`, `PATCH /v1/transactions/{id}` (PENDING only), `DELETE /v1/transactions/{id}` (PENDING only). CLI: `tulip transactions {edit,void,delete}`. | #55 |
| **P5.1** | Schema migration adding `transactions.{cleared_at, reconciled_at, reconciliation_id, imported_from_id, carried_forward_from_reconciliation_id}`; new tables `attachments`, `attachment_links`, `import_batches`, `statement_lines`, `reconciliations`, `reconciliation_matches`, `csv_profiles`. Repositories for each. Encrypted-attachment storage on local fs. Architecture tests banning direct writes to `reconciliations`, `reconciliation_matches`, `import_batches`. | new |
| **P5.2.a** | OFX importer. `tulip_importers.ofx.parse(file) -> list[StatementLine]`. CLI `tulip import ofx FILE --account ACCOUNT`. | #74 |
| **P5.2.b** | QIF importer. Same shape, custom parser. CLI `tulip import qif`. | #74 |
| **P5.2.c** | CSV importer + `csv_profiles` CRUD + YAML export / import. CLI `tulip import csv FILE --account A --profile P`, `tulip imports profiles {add,edit,list,show,delete,export,import}`. | #74 |
| **P5.3** | Reconciliation matcher in `tulip_core.reconciliation`. `find_candidates`, confidence buckets, partial / split match construction. The categorization-seam `Categorizer` protocol + `RuleBasedCategorizer` default. | new |
| **P5.4** | API + CLI surface. `POST /v1/imports`, `GET /v1/imports/{id}`, `POST /v1/imports/{id}/{apply,revert}`, reconciliation CRUD, carry-forward verbs, `tulip reconcile` interactive flow, `tulip imports {list,show,apply,revert}`. Closes Phase 5. | #74 |

P5.2.a/b/c can ship in any order or in parallel after P5.1; P5.3 depends only on P5.1 (the matcher consumes `StatementLine` and ledger transactions, does not care which importer produced them). P5.4 closes the loop.

## Alternatives considered

### Q1 — fuzzy amount tolerance

Considered: `|statement.amount - posting.amount| <= $0.01` for sub-cent rounding errors in foreign-currency conversions. Rejected for v1: cross-currency reconciliation is out of scope (v1 reconciles against an account in its native currency), and same-currency banks don't issue inexact amounts. The temptation to add a tolerance to mask a sign-flip bug is the real cost. Revisit when we ship multi-currency reconciliation.

### Q2 — scalar confidence with thresholds in config

Considered: `confidence: float in [0,1]`, configurable threshold per household. Rejected: every consumer (UI, CLI, audit log, the auto-apply rule) needs a bucket; a scalar just defers the bucket to the consumer, multiplying the places where the threshold lives. Buckets-as-truth is one knob, not three.

### Q2 — learned model in v1

Considered: small classifier trained on the user's existing categorized transactions. Rejected: no training data on day one of v1; cold-start would default to rules anyway; introduces ML deps and Phase 6 privacy work into Phase 5. Same DI seam serves both — Phase 6 plugs in a `LearnedCategorizer`, no Phase 5 rework.

### Q3 — partial-of-one matches (residual lingering)

Considered: a $100 statement line matches $60 of a $100 ledger transaction, leaving $40 of the ledger transaction "unmatched but same row." Rejected: the residual gets lost in mental models. Users who need this restructure with void-and-resplit, which makes the change explicit in the audit log.

### Q4 — corrections on a separate `match_overrides` table

Considered: keep auto-matches on `reconciliation_matches`, push manual matches to a parallel `manual_matches` table. Rejected: queries that ask "what's matched against this transaction" then need a UNION. Provenance columns (`created_by_user_id`, `matcher_version`, `confidence` NULL) on the same table cost three columns and zero queries.

### Q5 — implicit carry-forward

Considered: anything left unmatched at reconciliation completion silently rolls into the unmatched pool of the next reconciliation for the same account. Rejected: loses the user's intent. "Carry forward this in-flight check" and "I forgot about this row" are different states; the implicit version conflates them. Explicit costs one column and one verb; gets a queryable "what was I expecting?" surface.

### Q5 — auto-promote unmatched lines to POSTED transactions

Considered: §5.8's "PENDING transactions awaiting categorization" gives the user a triage step; an alternative is "auto-promote to POSTED with `Imbalance:Unknown` and let the user fix later." Rejected: a POSTED transaction with an `Imbalance` placeholder pollutes period balances and reports. PENDING is the right state for "not yet categorized."

### Q6 — dedup on parsed payload hash

Considered: hash the normalized `list[StatementLine]` rather than the file bytes, so re-issued files with the same lines dedup. Rejected: see Q6 above. Banks reissue with corrections, and we want the second copy to be visible.

### Q7 — `reconciled` as an enum value on `TransactionStatus`

Already exists — `TransactionStatus.RECONCILED` is in the enum. The decision here is **not** to make it the source of truth: a status enum can't carry "reconciled against which reconciliation," "reconciled when," "reconciled how (manually vs matcher)," or partial-match data. The enum stays as the fast-status check; the `reconciliations` row is the audit aggregate.

### Q7 — state on the bank-line side only

Considered: `statement_lines.reconciliation_match_id` is the only state, and the ledger-side `reconciled_at` is computed via a join. Rejected: every place in the codebase that asks "is this transaction reconciled" then needs to JOIN through `reconciliation_matches` → `reconciliations`. The denormalization on the ledger side is cheap (two columns, set in one place) and saves N joins.

### Q8 — common-denominator as a JSON dict instead of a dataclass

Considered: `dict[str, Any]` so per-format quirks need no schema migration. Rejected: type safety in the matcher matters more than format flexibility at the boundary. The `raw: dict[str, str]` field already provides the escape hatch for format-specific noise.

### Q8 — CSV profiles in `~/.config/tulip/csv-profiles/*.yaml`

Considered: filesystem-backed YAML profiles, optionally overridden by per-household DB rows. Rejected: filesystem state lives outside backup / encryption / tenancy. A user who restores their household DB onto a fresh machine wouldn't have profiles. A multi-household setup on one host would either share profiles globally (wrong for tenancy) or need per-household subdirectories (more state to manage). DB-of-record + YAML-as-export keeps storage in one place and the share use case still solvable via `tulip imports profiles export | import`.

### Q9 — file attachments unencrypted

Considered: bank statements are already on the user's home server; encrypting them in addition feels like belt-and-suspenders. Rejected: the file-level encryption helpers from P1.6 already exist, the cost to use them is one function call, and "I exfiltrated the user's home server's `~/.local/share/tulip/attachments/`" should not yield plaintext bank statements. Same-home-server-as-the-rest-of-Tulip is not a strong threat model.

## Implementation notes

### Schema (P5.1 migration sketch — not yet committed)

```sql
-- ---- transactions: new columns ----
ALTER TABLE transactions ADD COLUMN cleared_at        TIMESTAMP NULL;
ALTER TABLE transactions ADD COLUMN reconciled_at     TIMESTAMP NULL;
ALTER TABLE transactions ADD COLUMN reconciliation_id BLOB      NULL;
ALTER TABLE transactions ADD COLUMN imported_from_id  BLOB      NULL;
ALTER TABLE transactions ADD COLUMN carried_forward_from_reconciliation_id BLOB NULL;
ALTER TABLE transactions ADD COLUMN voided_by_transaction_id BLOB NULL;  -- P5.0 prerequisite
ALTER TABLE transactions ADD COLUMN voided_at         TIMESTAMP NULL;
-- (SQLite ALTER ADD COLUMN is in-place; FK constraints land via batch_alter_table.)

-- ---- attachments ----
CREATE TABLE attachments (
  household_id        BLOB    NOT NULL,
  id                  BLOB    NOT NULL,
  filename            TEXT    NOT NULL,
  content_type        TEXT    NOT NULL,
  size_bytes          INTEGER NOT NULL,
  content_hash        TEXT    NOT NULL,             -- sha256 hex
  storage_uri         TEXT    NOT NULL,             -- 'fs://<uuid>'
  data_key_wrapped    BLOB,                         -- per-attachment data key wrap; nullable in v1
  uploaded_by_user_id BLOB,
  uploaded_at         TIMESTAMP NOT NULL,
  PRIMARY KEY (household_id, id),
  FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX ix_attachments_hash
  ON attachments(household_id, content_hash);

-- ---- attachment_links ----
CREATE TABLE attachment_links (
  household_id   BLOB NOT NULL,
  attachment_id  BLOB NOT NULL,
  entity_type    TEXT NOT NULL CHECK (entity_type IN
                     ('transaction','account','reconciliation','sinking_fund','import_batch')),
  entity_id      BLOB NOT NULL,
  created_at     TIMESTAMP NOT NULL,
  PRIMARY KEY (household_id, attachment_id, entity_type, entity_id),
  FOREIGN KEY (household_id, attachment_id)
    REFERENCES attachments(household_id, id) ON DELETE CASCADE
);

-- ---- import_batches ----
CREATE TABLE import_batches (
  household_id              BLOB NOT NULL,
  id                        BLOB NOT NULL,
  account_id                BLOB NOT NULL,
  source_format             TEXT NOT NULL CHECK (source_format IN ('ofx','qif','csv','journal')),
  source_filename           TEXT NOT NULL,
  source_file_attachment_id BLOB NOT NULL,
  status                    TEXT NOT NULL CHECK (status IN ('parsed','applied','reverted')),
  imported_count            INTEGER NOT NULL DEFAULT 0,
  skipped_count             INTEGER NOT NULL DEFAULT 0,
  error_count               INTEGER NOT NULL DEFAULT 0,
  summary_json              TEXT,
  created_by_user_id        BLOB,
  created_at                TIMESTAMP NOT NULL,
  applied_at                TIMESTAMP,
  reverted_at               TIMESTAMP,
  PRIMARY KEY (household_id, id),
  FOREIGN KEY (household_id, account_id)
    REFERENCES accounts(household_id, id),
  FOREIGN KEY (household_id, source_file_attachment_id)
    REFERENCES attachments(household_id, id)
);
CREATE UNIQUE INDEX ix_import_batches_idempotency
  ON import_batches(household_id, account_id, source_file_attachment_id);

-- ---- statement_lines ----
CREATE TABLE statement_lines (
  household_id            BLOB NOT NULL,
  id                      BLOB NOT NULL,
  import_batch_id         BLOB NOT NULL,
  line_number             INTEGER NOT NULL,
  posted_date             DATE NOT NULL,
  amount                  NUMERIC NOT NULL,
  currency                TEXT NOT NULL,
  description             TEXT NOT NULL,
  counterparty            TEXT,
  reference               TEXT,
  fitid                   TEXT,                  -- OFX-only; null for QIF / CSV
  raw_json                TEXT NOT NULL,
  is_excluded             BOOLEAN NOT NULL DEFAULT 0,
  reconciliation_match_id BLOB,                  -- nullable; set when matched
  PRIMARY KEY (household_id, id),
  FOREIGN KEY (household_id, import_batch_id)
    REFERENCES import_batches(household_id, id) ON DELETE CASCADE
);
CREATE INDEX ix_statement_lines_batch
  ON statement_lines(household_id, import_batch_id);
CREATE INDEX ix_statement_lines_unmatched
  ON statement_lines(household_id, import_batch_id)
  WHERE reconciliation_match_id IS NULL AND is_excluded = 0;

-- ---- reconciliations ----
CREATE TABLE reconciliations (
  household_id              BLOB NOT NULL,
  id                        BLOB NOT NULL,
  account_id                BLOB NOT NULL,
  statement_period_start    DATE NOT NULL,
  statement_period_end      DATE NOT NULL,
  statement_starting_balance NUMERIC NOT NULL,
  statement_ending_balance  NUMERIC NOT NULL,
  currency                  TEXT NOT NULL,
  status                    TEXT NOT NULL CHECK (status IN ('in_progress','complete','abandoned')),
  source_import_batch_id    BLOB,                 -- nullable for manual reconciliations
  created_by_user_id        BLOB,
  created_at                TIMESTAMP NOT NULL,
  completed_at              TIMESTAMP,
  PRIMARY KEY (household_id, id),
  FOREIGN KEY (household_id, account_id)
    REFERENCES accounts(household_id, id),
  FOREIGN KEY (household_id, source_import_batch_id)
    REFERENCES import_batches(household_id, id)
);
CREATE INDEX ix_reconciliations_account
  ON reconciliations(household_id, account_id, statement_period_end DESC);

-- ---- reconciliation_matches ----
CREATE TABLE reconciliation_matches (
  household_id          BLOB NOT NULL,
  id                    BLOB NOT NULL,
  reconciliation_id     BLOB NOT NULL,
  statement_line_id     BLOB NOT NULL,
  ledger_transaction_id BLOB NOT NULL,
  match_amount          NUMERIC NOT NULL,
  currency              TEXT NOT NULL,
  confidence            TEXT CHECK (confidence IN ('high','medium','low')),  -- NULL for manual
  matcher_version       TEXT,                                                -- e.g. 'rules-v1'; NULL for manual
  created_by_user_id    BLOB,                                                -- NULL for matcher
  created_at            TIMESTAMP NOT NULL,
  PRIMARY KEY (household_id, id),
  FOREIGN KEY (household_id, reconciliation_id)
    REFERENCES reconciliations(household_id, id) ON DELETE CASCADE,
  FOREIGN KEY (household_id, statement_line_id)
    REFERENCES statement_lines(household_id, id) ON DELETE CASCADE,
  FOREIGN KEY (household_id, ledger_transaction_id)
    REFERENCES transactions(household_id, id)
);
CREATE INDEX ix_reconciliation_matches_recon
  ON reconciliation_matches(household_id, reconciliation_id);
CREATE INDEX ix_reconciliation_matches_tx
  ON reconciliation_matches(household_id, ledger_transaction_id);

-- ---- csv_profiles ----
CREATE TABLE csv_profiles (
  household_id   BLOB NOT NULL,
  id             BLOB NOT NULL,
  name           TEXT NOT NULL,
  yaml_body      TEXT NOT NULL,
  created_by_user_id BLOB,
  created_at     TIMESTAMP NOT NULL,
  updated_at     TIMESTAMP NOT NULL,
  PRIMARY KEY (household_id, id),
  FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX ix_csv_profiles_name ON csv_profiles(household_id, name);

-- ---- transactions FK additions for the new columns (batch_alter_table) ----
-- transactions.reconciliation_id                          -> reconciliations(id)
-- transactions.imported_from_id                           -> import_batches(id)
-- transactions.carried_forward_from_reconciliation_id     -> reconciliations(id)
-- transactions.voided_by_transaction_id                   -> transactions(id)  [P5.0]
```

### Domain types (`tulip-core`)

```
tulip_core.reconciliation/
  __init__.py
  statement_line.py     # StatementLine (frozen dataclass)
  candidate_match.py    # CandidateMatch + Confidence enum
  matcher.py            # find_candidates(statement_lines, ledger_txs) -> list[CandidateMatch]
  categorizer.py        # Categorizer Protocol + register_categorizer + RuleBasedCategorizer
```

The matcher is a pure function. No clock, no I/O, no SQLAlchemy. The household-context passed to the categorizer is a frozen dataclass of "user's existing rule list," "account whitelist," "household_id" — populated by the API handler from a query, then handed in.

### Module layout (`tulip-importers`)

```
tulip_importers/
  __init__.py
  ofx/
    __init__.py
    parser.py           # parse(file_bytes) -> list[StatementLine]
  qif/
    parser.py
  csv/
    parser.py
    profile.py          # CsvProfile dataclass + YAML codec (export / import only)
```

Each importer module exposes a `parse(file_bytes: bytes, *, profile: CsvProfile | None = None) -> list[StatementLine]`. Tests live in `packages/tulip-importers/tests/` and exercise round-trip parsing on fixture files.

### Module layout (`tulip-storage`)

```
tulip_storage/repositories/
  attachment.py            # AttachmentRepository (file write + encrypt + row insert)
  import_batch.py          # ImportBatchRepository
  statement_line.py        # StatementLineRepository (bulk insert; query by-batch / unmatched)
  reconciliation.py        # ReconciliationRepository (the chokepoint for reconciled_at writes)
  reconciliation_match.py  # ReconciliationMatchRepository
  csv_profile.py           # CsvProfileRepository
```

`ReconciliationRepository.complete(reconciliation_id)` is the **only** function that updates `transactions.reconciled_at` and `transactions.reconciliation_id` for the matched transactions. Direct UPDATEs to those columns from any other module are banned by architecture test.

### Architecture tests

- `tests/test_architecture_no_direct_reconciliation_writes.py` — AST scan rejects direct INSERTs into `reconciliations`, `reconciliation_matches`, `import_batches`, `statement_lines` outside their dedicated repository modules. Pattern lifted from P4.0's `test_architecture_no_direct_shadow_writes.py` and P4.3.a's `test_architecture_no_direct_scheduled_job_writes.py`.
- `tests/test_architecture_no_direct_reconciled_at_writes.py` — AST scan rejects UPDATEs to `transactions.reconciled_at` / `transactions.reconciliation_id` / `transactions.carried_forward_from_reconciliation_id` outside `ReconciliationRepository` and the carry-forward chokepoint.
- `tests/test_architecture_no_ai_in_importers.py` — AST scan rejects `import tulip_ai` from `tulip_importers.*`. Phase 6 plugs in via the `register_categorizer` DI hook, not via direct import.
- `tests/test_architecture_attachment_bytes_encrypted.py` — scan ensures every code path writing to `attachments.storage_uri` paths runs the bytes through `tulip_storage.encryption.encrypt_field` first.

### What P5.0 ships

1. `transactions.voided_by_transaction_id`, `voided_at` migration.
2. `tulip_core.transactions.transaction.Transaction` gets a `voided_by_transaction_id: UUID | None` field.
3. API: `POST /v1/transactions/{id}/void`, `PATCH /v1/transactions/{id}` (PENDING-only), `DELETE /v1/transactions/{id}` (PENDING-only).
4. CLI: `tulip transactions {edit,void,delete}`.
5. Tests per #55 acceptance criteria.

P5.0 does **not** touch reconciliation. Its only reconciliation-aware rule is "reject void of a transaction whose `reconciliation_id IS NOT NULL`" — and since `reconciliation_id` doesn't exist as a column until P5.1, P5.0 ships without that rule and P5.1 adds it as part of the same migration that creates the column. The CLI surface for "un-reconcile first" is added in P5.4.

## References

- [ARCHITECTURE.md §4.1](../ARCHITECTURE.md) — schema sketch (this ADR's columns are the actual delivery, not the sketch).
- [ARCHITECTURE.md §5.8](../ARCHITECTURE.md) — reconciliation feature spec.
- [ARCHITECTURE.md §5.9](../ARCHITECTURE.md) — importers feature spec.
- [ARCHITECTURE.md §10](../ARCHITECTURE.md) — phase plan; Phase 5 entry criteria.
- [ADR-0001](0001-envelope-shadow-ledger.md) — pre-code design pattern; chokepoint-writer pattern reused here.
- [ADR-0002](0002-scheduler-primitive.md) — DI seam pattern (`register_handler`); reused for `register_categorizer`.
- [docs/THREAT_MODEL.md](../THREAT_MODEL.md) — Phase 5 privacy notes; pre-Phase-6 audit cadence.
- [Issue #101](https://github.com/rmwarriner/tulip-accounting/issues/101) — closed by this ADR.
- [Issue #74](https://github.com/rmwarriner/tulip-accounting/issues/74) — Phase 5 umbrella.
- [Issue #55](https://github.com/rmwarriner/tulip-accounting/issues/55) — transaction void / PENDING edit; P5.0 prerequisite.
- [Issue #6](https://github.com/rmwarriner/tulip-accounting/issues/6) — downloader / SimpleFIN; adjacent, deferred to Phase 9.
- [Issue #33](https://github.com/rmwarriner/tulip-accounting/issues/33) — Amazon / Apple order matching; possible P5.5 follow-up using the matcher infra.
- [Issue #36](https://github.com/rmwarriner/tulip-accounting/issues/36) — DMS document linking; adjacent to attachment work.

## Decision log

| Date | Decision | By |
|---|---|---|
| 2026-05-04 | Proposed: bucketed confidence, rule-based; M:N matches table; hybrid state model; `StatementLine` as the common denominator. | P5 kickoff (this ADR draft) |
| 2026-05-04 | Proposed: P5.0 = #55 void / PENDING-edit; reconciliation depends on void infrastructure. | P5 kickoff |
| 2026-05-04 | Proposed: idempotency on raw-file SHA-256, not parsed-payload hash. | P5 kickoff |
| 2026-05-04 | Proposed: `MATCH_DATE_WINDOW = 3 days`, `FUZZY_HIGH_THRESHOLD = 0.9`, `FUZZY_MEDIUM_THRESHOLD = 0.6` as private constants in matcher; not user-tunable in v1. | P5 kickoff |
| 2026-05-04 | Proposed: CSV profiles stored only in `csv_profiles` DB table; YAML is export / import format only. (Filesystem-as-storage rejected — keeps profiles inside backup / encryption / tenancy boundary.) | P5 kickoff |
| 2026-05-04 | Proposed: carry-forward is explicit (new `transactions.carried_forward_from_reconciliation_id` column + dedicated API verb + audit action). Implicit-rollover rejected. | P5 kickoff |
| 2026-05-04 | Proposed: attachments encrypted at rest via P1.6 field-level encryption; local fs storage in v1. | P5 kickoff |

# ADR-0009: Normalised tags with posting / transaction / account scopes and inherited resolution

**Status:** Proposed (2026-05-21).

**Phase:** Decision lands during the Phase 9 follow-up wave alongside the
chart-of-accounts mutation surface (#431, #432, #443, #447). The
**implementation** is sequenced into three PRs — see *Phasing* below.

**Supersedes:** [#39](https://github.com/rmwarriner/tulip-accounting/issues/39)
("v1 labels-only tags surface"), which deliberately shipped the smallest
useful tag slice and explicitly deferred posting tags, account tags,
cascade semantics, and key=value pairs. This ADR closes that deferral
window.

---

## Context

The v1 tag surface that shipped under #39 is:

- One table: `transaction_tags(household_id, transaction_id, tag)`.
  Tag is a freeform `String(64)` with composite PK; no separate `tags`
  table.
- Tags exist only at the **transaction level**. Postings cannot carry
  tags; accounts cannot carry tags.
- The string is the identity — renaming a tag means a multi-row UPDATE
  across every transaction that uses it. There is no "tag exists in
  the household but isn't used yet" concept.

Two pressures forced reopening the design:

1. **QIF import** (#447): Banktivity (and Quicken-derived exporters)
   embed tags in the category field on split lines:

   ```
   SWants:Personal:Gifts/Birthday:Walter
   ```

   Category is `Wants:Personal:Gifts`, tags are `Birthday`, `Walter`.
   Tags are emitted **per split**, not per transaction. Tulip's
   per-transaction model can only land the *union* of split tags up to
   the parent — losing the per-leg attribution that the source data
   carries.

2. **Operator-stated need.** The user, validating the v1 model on real
   data, called out that they want tags at the posting level *and*
   want account-level tags that flow to a posting via the posting's
   account (e.g. every posting against `Visa` carries a `credit-card`
   tag implicitly). They also called out that the freeform string
   model is wrong: tags should be normalised, with names rename-able
   in one place, so a "Walter" → "Walter S." rename is one row
   instead of N.

PTA prior art that informed the design:

- **hledger** treats `tag:value` as posting-level by default, with
  transaction-level tags applying to every posting on that
  transaction. No account-level tags natively; you can fake it via
  description prefixes.
- **Beancount** treats `#tag` as transaction-level and doesn't expose
  posting tags directly; account-level metadata exists but is closer
  to "type info" than a tag system.
- **Ledger-cli** has posting tags via `; tag:` comments; transaction
  tags via the same syntax at the transaction level; no formal
  account tags but accounts can carry metadata via subdirectives.

The synthesis: posting tags are the most expressive layer (one tag can
describe one leg of a multi-leg transaction — the `Walter` tag on
the gift-card half of a credit-card payment). Transaction tags are
useful as the default-for-all-postings shorthand. Account tags add
real value for cross-cutting categorisation (`tax-deductible`,
`joint`, `child-college-fund`) without forcing the operator to tag
every transaction that touches the account.

The current v1 model can't carry any of this.

## Decision

Replace the single freeform `transaction_tags` table with a
normalised three-scope tag system.

### Schema

```sql
-- New: central tag registry, household-scoped.
CREATE TABLE tags (
    household_id  UUID    NOT NULL,
    id            UUID    NOT NULL,
    name          STRING(64) NOT NULL,
    description   STRING(500),    -- optional, for the tag-management UI
    color         STRING(7),      -- optional, "#RRGGBB" for TUI rendering
    created_at    TIMESTAMP NOT NULL,
    PRIMARY KEY (household_id, id),
    FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE CASCADE,
    UNIQUE (household_id, name)    -- name unique within household
);

-- Refactored: transaction_tags carries tag_id, not the string.
CREATE TABLE transaction_tags (
    household_id    UUID NOT NULL,
    transaction_id  UUID NOT NULL,
    tag_id          UUID NOT NULL,
    PRIMARY KEY (household_id, transaction_id, tag_id),
    FOREIGN KEY (household_id, transaction_id)
      REFERENCES transactions(household_id, id) ON DELETE CASCADE,
    FOREIGN KEY (household_id, tag_id)
      REFERENCES tags(household_id, id) ON DELETE CASCADE
);

-- New: posting-level tags.
CREATE TABLE posting_tags (
    household_id UUID NOT NULL,
    posting_id   UUID NOT NULL,
    tag_id       UUID NOT NULL,
    PRIMARY KEY (household_id, posting_id, tag_id),
    FOREIGN KEY (household_id, posting_id)
      REFERENCES postings(household_id, id) ON DELETE CASCADE,
    FOREIGN KEY (household_id, tag_id)
      REFERENCES tags(household_id, id) ON DELETE CASCADE
);

-- New: account-level tags.
CREATE TABLE account_tags (
    household_id UUID NOT NULL,
    account_id   UUID NOT NULL,
    tag_id       UUID NOT NULL,
    PRIMARY KEY (household_id, account_id, tag_id),
    FOREIGN KEY (household_id, account_id)
      REFERENCES accounts(household_id, id) ON DELETE CASCADE,
    FOREIGN KEY (household_id, tag_id)
      REFERENCES tags(household_id, id) ON DELETE CASCADE
);
```

Every cross-table FK is composite on `household_id` — tenant isolation
stays a query-builder concern, not a leak waiting to happen.

### Inheritance semantics

Inheritance is **resolved at read time, not stored.** For any
posting `P`:

```
effective_tags(P) =
    direct_tags(P)                              # from posting_tags
  ∪ direct_tags(P.transaction)                  # from transaction_tags
  ∪ direct_tags(P.account)                      # from account_tags
```

For any transaction `T`:

```
effective_tags(T) =
    direct_tags(T)                              # from transaction_tags
  ∪ ⋃ direct_tags(P)  for P in T.postings       # union of posting tags
  ∪ ⋃ direct_tags(P.account)  for P in T.postings   # union of account tags
                                                    # via the postings
```

API + TUI expose **both** direct and effective sets, with provenance
for inherited tags ("from account Visa"). This is critical: when the
operator removes a tag they need to see whether the tag was direct or
inherited and act accordingly (remove the direct edge, or detach from
the source).

Materialising the union on write was considered and rejected:

- Rename of tag `Walter` → `Walter S.` becomes O(rows) on every
  materialised view.
- Account merge (an out-of-scope-today operation but easy to
  imagine) cascades into every posting's denormalised tag set.
- The read query is a 3-arm UNION + GROUP BY against indexed PKs —
  cheap enough for any realistic household scale.

### Backwards compatibility

The existing `transaction_tags` table has rows in shipped databases.
The migration is **lossless**:

1. Create `tags` table.
2. For each distinct `(household_id, tag)` in `transaction_tags`:
   INSERT a row in `tags` with `id = uuid4()`, `name = tag`.
3. Add nullable `tag_id` column to `transaction_tags`.
4. UPDATE `transaction_tags` JOIN the lookup, populate `tag_id`.
5. Mark `tag_id` NOT NULL, add the FK constraint, drop the
   `tag` string column, drop the old PK, add the new composite PK.

Existing API callers passing `?tag=<name>` continue to work — the
filter resolves `<name>` → tag id at query time. Operators writing
tags via `PATCH /v1/transactions/{id}` continue to pass strings; the
handler resolves to id (or creates the tag if missing, gated by a
new `?create_missing_tags=true` query param to keep the existing
strict path).

## Consequences

### Positive

- One canonical tag identity. Renames are O(1).
- Tags at every layer of the ledger model that makes sense.
- Inheritance via view = correct semantics for free; no
  denormalisation hazard.
- Provenance ("inherited from account Visa") is just metadata on the
  effective-tags response — UX wins for free.
- QIF / Beancount / hledger imports map cleanly onto the per-posting
  + per-transaction model.

### Negative

- Migration touches a shipped table. Backup discipline (RECOVERY.md)
  is the safety net.
- Three new repo modules and a meaningful API surface refactor.
- Two new join tables increase write-path cost on bulk imports
  marginally. Profile in CI's benchmarks step.

### Open questions deferred

- **Hierarchical tags** (`parent_tag_id`). The schema reserves the
  optional column but no logic uses it in this ADR. A follow-up ADR
  can pin the inheritance semantics (does `expense:groceries` inherit
  `expense`? Probably yes, but the read query gets a recursive CTE).
- **Tag types / categories** (color, description) are stored but
  unused beyond display. No business logic gates on them in v1.
- **Bulk tag operations** (rename, merge, delete-cascade-with-
  unattaching) are out of this ADR's scope — they ship as needed
  once the system has real-world tag debt.

## Phasing

Three sequenced PRs.

### PR A — Normalisation migration

- Add `tags` table.
- Refactor `transaction_tags` to FK by tag_id; backfill in the
  Alembic migration.
- Update `TransactionTagRepository` to operate by id (resolve from
  name internally so the public API surface stays string-passing).
- Update `?tag=` filter, audit log writers, tests.

No new user-visible behaviour. Existing tag operations work
identically. Lowest-risk slice; closes the door on the freeform
model.

### PR B — Posting tags + account tags

- New `posting_tags` + `account_tags` tables.
- New repo methods on the relevant repositories.
- API: `tags` field on `PostingCreate` / `PostingRead`;
  `tags` field on `AccountCreate` / `AccountUpdate` / `AccountRead`.
- `?tag=` filter on transaction listings respects all three join
  tables (UNION search).
- CLI: `tulip accounts edit ACCOUNT --tag ...`,
  posting-level tags via the transaction-edit grammar.
- TUI: account modal gets a tag input; transaction modal
  gets per-posting tag inputs.

### PR C — Inheritance: effective_tags

- View / CTE that computes the UNION described above.
- New API endpoint `GET /v1/transactions/{id}/effective-tags`
  returning `{direct, effective, provenance}`.
- TUI surfacing in the transaction detail pane.
- #447 (QIF tag import) lands as a sub-PR of B or C — it writes
  per-split tags to `posting_tags`.

Each PR is independently shippable; B can land without C if the
operator is comfortable seeing only direct tags initially.

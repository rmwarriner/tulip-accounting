# ADR 0001 — Envelope and sinking-fund tracking via shadow ledger

**Status:** Accepted (2026-05-02) — adopted on P4.0 merge.
**Phase:** 4 (Envelopes + sinking funds).
**Supersedes:** None.

---

## Context

[ARCHITECTURE.md §5.2-5.4](../ARCHITECTURE.md) commits Tulip to first-class envelope budgeting and sinking funds. The schema sketch in §4.1 has `allocation_pools` as a polymorphic base for `envelopes` and `sinking_funds`, and `posting.pool_id` as an optional reference linking ledger postings to pools. What §4.1 does **not** specify is the mechanic by which pool balances are computed and updated — particularly:

1. Are pool balances **stored** (denormalized) or **derived**?
2. How does a refill (assigning new money to an envelope) interact with double-entry?
3. What's the sign convention for `posting.pool_id` postings?
4. Where does ad-hoc and after-the-fact refill modification live?

The phase planning surfaced four candidate models:

- **(a) Tag-on-posting + budget_amount field.** `pool_id` is a tag on a main-ledger posting. Envelope balance = `budget_amount - spending against it this period`. Refills update `budget_amount` on schedule, no ledger transaction.
- **(b) Envelopes-as-real-accounts** with magic `Equity:BudgetAvailable` companion. Refills are ordinary main-ledger transactions; spending becomes 4-leg.
- **(c) Allocations-as-events**, a non-double-entry table. Refills are events; spending is postings; pool balance = events − postings.
- **(d) Shadow ledger.** A parallel double-entry ledger where pools are first-class accounts. Main-ledger postings with `pool_id` auto-create paired shadow transactions. Refills, transfers, rollovers, modifications all live in the shadow ledger as ordinary shadow transactions.

## Decision

**Adopt (d) — shadow ledger.**

The shadow ledger is a self-contained parallel double-entry ledger with its own `shadow_transactions` and `shadow_postings` tables. The accounts of this ledger are `allocation_pools`. Three categories of pools exist:

- **User-created pools** — `envelope` and `sinking_fund`, both visible in `tulip envelopes list` / `tulip sinking-funds list`.
- **System pools** — `inflow`, `unallocated`, `spent`. Auto-created per `(household, currency)` combo. Not user-editable. Don't appear in the user's pool list by default; they're plumbing.
- **(future, not in v1)** Per-account "earmarked" pools, if we ever decide to model "this $500 in Checking is earmarked for Groceries."

Linkage to the main ledger is one-way: a main-ledger posting with `pool_id` set automatically generates a paired shadow transaction at write time. The shadow ledger does not back-reference main-ledger income or initial-deposit events; "money entering the budget system" is its own user action that posts a shadow transaction.

**Pairing rule:** *one main-ledger transaction produces at most one paired shadow transaction.* Multi-pool effects from a single main tx are bundled into the paired shadow tx as additional legs. This keeps the `paired_main_tx_id` linkage 1:1 and avoids fanout on the join. Worked examples below.

### Worked example A — paycheck-driven funding split

User receives a $1000 paycheck and wants to fund three envelopes from it: $250 to Groceries, $500 to Mortgage, $250 to Entertainment.

**Main ledger** (the paycheck — no `pool_id`s):

```
Tx: "Paycheck", reason=normal
  Assets:Checking   +1000 USD
  Income:Salary     -1000 USD
```

**Shadow ledger** (the funding split — one composite shadow tx with four legs):

```
Shadow tx: "Paycheck → budget", reason=budget_inflow,
           paired_main_tx_id=<paycheck.id>
  Inflow         -1000 USD
  Groceries      +250  USD
  Mortgage       +500  USD
  Entertainment  +250  USD
```

Sum = −1000 + 250 + 500 + 250 = 0 per currency. ✓ One paired shadow tx with four shadow postings.

The user could equivalently model this as one `budget_inflow` (Inflow −1000 / Unallocated +1000) plus three subsequent `refill` shadow txs from Unallocated; both shapes are valid and which one the user picks is a UX/CLI choice. The pairing rule is the same.

### Worked example B — multi-pool spending (auto-paired)

A single Costco run is $80 — $50 groceries plus $30 party supplies.

**Main ledger** (with `pool_id` on each expense leg):

```
Tx: "Costco", reason=normal
  Expenses:Groceries     +50 USD,  pool_id=Groceries
  Expenses:Entertainment +30 USD,  pool_id=Entertainment
  Assets:Checking        -80 USD
```

**Shadow ledger** (auto-paired at write time — one composite shadow tx):

```
Shadow tx: "Costco (envelope effects)", reason=spend,
           paired_main_tx_id=<costco.id>
  Spent          +80 USD
  Groceries      -50 USD
  Entertainment  -30 USD
```

The single `Spent` leg carries the absorbing offset; one leg per pool-tagged main posting carries the per-envelope effect.

## Consequences

### Positive

1. **Consistency and correctness.** Pool balance is *only* derived from `sum(shadow_postings)`. There is no second source of truth (no `current_balance` column, no `budget_amount`-minus-spending shortcut). Same invariant style as the main ledger (sum-to-zero per shadow_transaction per currency, enforced via DB trigger).
2. **No main-ledger pollution.** Refills, allocations, envelope-to-envelope transfers, and rollovers do not produce main-ledger transactions. The main ledger remains the truth of *real money*; the shadow ledger remains the truth of *intent*.
3. **Ad-hoc refills are first-class.** They're just shadow transactions. No special path; the same engine that handles scheduled refills handles ad-hoc.
4. **Refill modification after the fact** uses the same void/reverse mechanic that Phase 5 (#55) is going to land for main-ledger transactions. Audit trail preserved.
5. **Envelope-to-envelope transfers** are a normal shadow transaction with two pool postings (`+X` / `−X`). No new mechanic required.
6. **Time-travel queries** ("what was Groceries' balance on March 15?") are bounded sums over `shadow_postings`, exactly like account balances at a date.
7. **Symmetric formula across pool types.** Envelopes and sinking funds both compute balance via `sum(shadow_postings)`. The earlier asymmetric formula concern from option (a) — envelopes are period-bounded, sinking funds are cumulative — disappears at the storage layer; period-awareness is a query-time filter, not a different formula.

### Negative

1. **Roughly 1.5–2× the schema and code of option (a).** Two new tables for the shadow ledger plus a balance trigger; new domain types in `tulip-core`; a shadow-ledger engine module that mirrors `tulip_core.accounting` for shadow transactions.
2. **Two ledgers must be kept in sync.** Mitigation: a single domain-level `post_transaction` chokepoint at the API layer that atomically writes both main and shadow rows when `pool_id` is set; an architecture test that bans direct INSERTs into `shadow_postings`.
3. **`posting.pool_id` is technically redundant** with the shadow posting's pool reference — but kept as a denormalization for the join from main ledger to shadow ledger. Removal could happen later if it bothers anyone.

### Neutral

1. **System pools are per-currency.** A USD-and-EUR household has six system pools (Inflow/Unallocated/Spent × USD/EUR). Auto-created lazily on first use of a currency in budgeting.
2. **The architecture-doc `current_balance` column on `allocation_pools` goes away.** Pool balance is derived. Same correction P1.4 made for accounts.

## Alternatives considered (and why rejected)

### (a) Tag-on-posting + `budget_amount` field

Simplest. Lowest LOC. But:

- Pollutes the user mental model with two formulas (envelope = budget − spending; sinking fund = sum of postings) for what users perceive as "the same kind of thing."
- Refills modify a `budget_amount` field that has no audit trail by default — every retroactive change overwrites history.
- Ad-hoc refills require a separate "manual budget adjustment" mechanism to update `budget_amount` outside the rule.
- "Move $50 from Gas to Groceries" requires a custom transfer mechanism distinct from refills.

The simplicity is real but it's bought with asymmetry that compounds in Phase 4–7 reports.

### (b) Envelopes-as-real-accounts with `Equity:BudgetAvailable`

Classical double-entry, no shadow ledger needed. But:

- Refills clutter the main ledger with non-money transactions. "October Groceries refill: +500 BudgetAvailable, −500 Groceries" is noise in any report that filters on the main ledger.
- Spending has to be 4-leg (real money + envelope tracking + offset). The accounting engine has to distinguish "envelope-aware spending" from "ordinary spending" — same writer chokepoint problem as (d), but with main-ledger pollution as the cost.
- The `pool_id` field on `posting` becomes redundant with `account_id` (since the envelope IS the account), and the polymorphic `allocation_pools` table from §4.1 collapses into ordinary `accounts` rows.

(b) is internally consistent but throws away the §4.1 design and drags non-money concepts into the main ledger.

### (c) Allocations-as-events

Asymmetric: refills are events, spending is postings, pool balance = events − postings. But:

- Loses double-entry rigor for envelope movements.
- Envelope-to-envelope transfers are awkward (two events?).
- Modification after the fact still needs an event-versioning mechanism that mirrors what the shadow ledger gets for free.

(c) is essentially (d) without the second invariant. Once you add the second invariant, you have (d).

## Implementation notes

### Schema (P4.0 migration sketch — not yet committed)

```sql
-- ---- allocation_pools (the user-visible concept; superset of envelopes + sinking_funds + system pools) ----
CREATE TABLE allocation_pools (
  household_id     BLOB    NOT NULL,
  id               BLOB    NOT NULL,
  pool_type        TEXT    NOT NULL CHECK (pool_type IN
                       ('envelope', 'sinking_fund', 'inflow', 'unallocated', 'spent')),
  name             TEXT    NOT NULL,
  visibility       TEXT    NOT NULL DEFAULT 'shared' CHECK (visibility IN ('shared', 'private')),
  currency         TEXT    NOT NULL,
  is_active        BOOLEAN NOT NULL DEFAULT 1,
  is_system        BOOLEAN NOT NULL DEFAULT 0,
  created_by_user_id BLOB  REFERENCES users(id),
  created_at       TIMESTAMP NOT NULL,
  updated_at       TIMESTAMP NOT NULL,
  PRIMARY KEY (household_id, id),
  FOREIGN KEY (household_id) REFERENCES households(id)
);
CREATE INDEX ix_allocation_pools_household_active
  ON allocation_pools(household_id, is_active);
-- one inflow/unallocated/spent per (household, currency)
CREATE UNIQUE INDEX ix_allocation_pools_system_per_currency
  ON allocation_pools(household_id, pool_type, currency)
  WHERE is_system = 1;

-- ---- envelopes (joined to allocation_pools via composite FK) ----
CREATE TABLE envelopes (
  household_id     BLOB    NOT NULL,
  pool_id          BLOB    NOT NULL,
  budget_period    TEXT    NOT NULL CHECK (budget_period IN
                       ('weekly', 'biweekly', 'monthly', 'quarterly', 'annual', 'custom')),
  budget_amount    NUMERIC,
  rollover_policy  TEXT    NOT NULL CHECK (rollover_policy IN
                       ('reset', 'accumulate', 'cap_at_budget')),
  refill_rule_json TEXT,            -- JSON; see ARCHITECTURE.md §5.3
  PRIMARY KEY (household_id, pool_id),
  FOREIGN KEY (household_id, pool_id)
    REFERENCES allocation_pools(household_id, id) ON DELETE CASCADE
);

-- ---- sinking_funds (joined to allocation_pools via composite FK) ----
CREATE TABLE sinking_funds (
  household_id          BLOB     NOT NULL,
  pool_id               BLOB     NOT NULL,
  target_amount         NUMERIC  NOT NULL,
  target_date           DATE     NOT NULL,
  contribution_strategy TEXT     NOT NULL CHECK (contribution_strategy IN
                            ('manual', 'even_split', 'percentage_of_income')),
  contribution_amount   NUMERIC,
  PRIMARY KEY (household_id, pool_id),
  FOREIGN KEY (household_id, pool_id)
    REFERENCES allocation_pools(household_id, id) ON DELETE CASCADE
);

-- ---- shadow_transactions (the parallel ledger header) ----
CREATE TABLE shadow_transactions (
  household_id     BLOB     NOT NULL,
  id               BLOB     NOT NULL,
  date             DATE     NOT NULL,
  description      TEXT     NOT NULL,
  reason           TEXT     NOT NULL CHECK (reason IN
                       ('budget_inflow',     -- declaring "I have $X to budget"
                        'refill',            -- assigning Unallocated → envelope/sinking_fund
                        'spend',             -- paired with main-ledger pool_id posting
                        'transfer',          -- pool ↔ pool
                        'rollover',          -- end-of-period mechanic
                        'manual')),          -- escape hatch for corrections
  status           TEXT     NOT NULL DEFAULT 'posted'
                   CHECK (status IN ('pending', 'posted', 'voided')),
  paired_main_tx_id     BLOB,        -- when reason='spend', the main tx that triggered this
  voided_by_shadow_tx_id BLOB,       -- for the future void mechanic (Phase 5 alignment)
  voided_at        TIMESTAMP,
  posted_at        TIMESTAMP,
  created_by_user_id BLOB    REFERENCES users(id),
  created_at       TIMESTAMP NOT NULL,
  updated_at       TIMESTAMP NOT NULL,
  PRIMARY KEY (household_id, id),
  FOREIGN KEY (household_id) REFERENCES households(id),
  FOREIGN KEY (household_id, paired_main_tx_id)
    REFERENCES transactions(household_id, id),
  FOREIGN KEY (household_id, voided_by_shadow_tx_id)
    REFERENCES shadow_transactions(household_id, id)
);
CREATE INDEX ix_shadow_tx_household_date
  ON shadow_transactions(household_id, date DESC);
CREATE INDEX ix_shadow_tx_paired_main
  ON shadow_transactions(household_id, paired_main_tx_id)
  WHERE paired_main_tx_id IS NOT NULL;

-- ---- shadow_postings (the legs) ----
CREATE TABLE shadow_postings (
  household_id     BLOB     NOT NULL,
  id               BLOB     NOT NULL,
  shadow_transaction_id BLOB NOT NULL,
  pool_id          BLOB     NOT NULL,
  amount           NUMERIC  NOT NULL,        -- signed; +X = into pool, -X = out of pool
  currency         TEXT     NOT NULL,
  memo             TEXT,
  PRIMARY KEY (household_id, id),
  FOREIGN KEY (household_id, shadow_transaction_id)
    REFERENCES shadow_transactions(household_id, id) ON DELETE CASCADE,
  FOREIGN KEY (household_id, pool_id)
    REFERENCES allocation_pools(household_id, id)
);
CREATE INDEX ix_shadow_postings_pool
  ON shadow_postings(household_id, pool_id);
CREATE INDEX ix_shadow_postings_tx
  ON shadow_postings(household_id, shadow_transaction_id);

-- ---- balance trigger on shadow_postings ----
-- Mirrors the main-ledger balance triggers from migration 0001:
-- enforces SUM(amount) per shadow_transaction_id, per currency = 0
-- on transitions into status='posted'. Pending and voided are exempt.
-- (Trigger SQL omitted from this sketch; will follow the pattern of the
-- main-ledger triggers in 20260429_2116_c2f963036df3_initial_schema.py.)

-- ---- postings.pool_id FK addition ----
-- The column already exists (initial migration left it as nullable BLOB
-- with no FK). Add the constraint now:
-- (SQLite requires table-rebuild to add an FK; Alembic's batch_alter_table
-- handles this. The migration body will use op.batch_alter_table.)
```

### Domain types (`tulip-core`)

```
tulip_core.allocation/
  pool.py            # Pool (frozen dataclass), PoolType (enum)
  envelope.py        # Envelope (extends Pool with budget_period, etc.)
  sinking_fund.py    # SinkingFund
  shadow_posting.py  # ShadowPosting
  shadow_transaction.py  # ShadowTransaction (sum-to-zero invariant in __post_init__)
  refill_rule.py     # RefillRule value object (3 strategies, structured shape, no eval)
  engine.py          # post_shadow_transaction(...) — mirrors tulip_core.accounting.post_transaction
```

### Wiring at the API layer

Single chokepoint: when the API's `create_transaction` handler receives a `TransactionCreate` body whose postings include any `pool_id`, it:

1. Resolves and validates pool(s) — exists in household, visible to caller, active, currency matches posting, account-type permitted.
2. Calls `post_transaction` to write the main-ledger transaction (existing path).
3. Calls `post_shadow_transaction` to write **a single paired shadow transaction** (per the pairing rule above) carrying the per-pool effects plus the absorbing `Spent` (or `Inflow`) leg; `paired_main_tx_id` records the linkage.
4. Both writes commit atomically via the same SQLAlchemy session.

#### Pairing rule (re-stated for the writer)

For each main-ledger transaction, the auto-paired shadow tx is built like this:

- For each main posting `p` with `p.pool_id` set, emit one shadow posting `(pool=p.pool_id, amount=−sign_for_account_type(p.account_type) × p.amount, currency=p.currency)`. (See [§Q3 in the ADR context](#decision) for the account-type sign rule.)
- Add one absorbing leg in the household's system pool of the appropriate currency:
  - `Spent` when the net pool effect is negative (money flowing out of envelopes — i.e., a spending-shaped main tx).
  - `Inflow` when the net pool effect is positive (money flowing into envelopes — would happen on, e.g., a refund main tx whose income posting is pool-tagged). Rare; documented as a real case.
  - The absorbing leg's amount is whatever balances the shadow tx to zero per currency.
- Set `reason=spend` (or `refund` if we add that later — out of scope for v1).
- Set `paired_main_tx_id` to the main tx's id.

User-initiated shadow transactions (refills, transfers, rollovers, ad-hoc allocations) follow the same composite shape but originate from explicit API calls, not from auto-pairing. Their `paired_main_tx_id` is `NULL` unless the user explicitly links them to a main-ledger source (e.g., the paycheck-funding-split case in Worked Example A above).

Architecture test (`tests/test_architecture_no_direct_shadow_writes.py`): AST scan rejects direct INSERTs into `shadow_transactions` / `shadow_postings` outside the engine module. Same pattern as the existing `test_architecture_no_http_exception.py`.

### What P4.0 ships

1. The migration above.
2. Domain types in `tulip-core`.
3. Storage repos: `AllocationPoolRepository`, `ShadowTransactionRepository`. Includes `balance_for_pool(pool_id, *, as_of=None, currency=None)`.
4. Engine: `tulip_core.allocation.engine.post_shadow_transaction`.
5. Auto-creation of system pools at household creation (and lazy on first use of a new currency).
6. Architecture test for direct-shadow-write ban.
7. ARCHITECTURE.md §4.1 + §5.2-5.4 refinements (drop `current_balance` column; clarify the shadow-ledger model; document the `Inflow`/`Unallocated`/`Spent` system pools).

P4.0 explicitly does **not** ship the API endpoints, CLI, refill rules execution, or scheduled-tx runner — those are P4.1, P4.2, P4.3 respectively per the slicing plan. P4.0 is a pure storage + domain layer slice.

## References

- [ARCHITECTURE.md §4.1 Core Entities](../ARCHITECTURE.md)
- [ARCHITECTURE.md §5.2-5.4 Envelopes / Sinking funds](../ARCHITECTURE.md)
- [PHASE_STATUS.md](../PHASE_STATUS.md)
- [docs/THREAT_MODEL.md §5.1](../THREAT_MODEL.md) — Phase 4 constraints (tenant scoping, no expression eval in refill rules)
- #55 (transaction void → Phase 5) — the void mechanic that lands there extends naturally to shadow transactions; this ADR's `voided_by_shadow_tx_id` field anticipates the model.

## Decision log

| Date | Decision | By |
|---|---|---|
| 2026-05-01 | Proposed: shadow ledger over (a) tag-on-posting + budget field. | conversation in P4 kickoff |
| 2026-05-01 | Decided: 3 system pool types (Inflow, Unallocated, Spent), per-currency. | P4 kickoff Q1+Q2 |
| 2026-05-01 | Decided: auto-pairing on every main posting with `pool_id`. | P4 kickoff Q3 |
| 2026-05-01 | Pairing rule: one main-ledger tx → at most one paired shadow tx (composite, multi-leg as needed). Worked examples added. | clarifying question on multi-envelope paychecks |
| 2026-05-02 | Accepted on P4.0 merge (#60). Refill-rule JSON shape finalized as `fixed_amount` / `fill_to_amount` / `percentage_of_income` to match the structured value object. | P4.0 implementation |

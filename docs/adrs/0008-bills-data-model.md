# ADR-0008: Bills as a third allocation entity, linked to envelopes and sinking funds

**Status:** Accepted (2026-05-17). No code ships with this ADR; it locks the
shape so the eventual implementation slice does not re-litigate it.

**Phase:** Decision lands during Phase 9 (terminal UI) design. The
*implementation* of `Bill` is out of v1 TUI scope and will land in a
dedicated backend slice (post-[#309](https://github.com/rmwarriner/tulip-accounting/issues/309) P9.0–P9.4) before the Bills browser TUI screen is built.

**Supersedes / extends:** [ADR-0001](0001-envelope-shadow-ledger.md) (envelope + sinking-fund shadow ledger).

---

## Context

The Phase 9 wireframes ([docs/TUI_WIREFRAMES.md](../TUI_WIREFRAMES.md))
introduce a `Bills` screen — recurring outflows grouped by frequency,
with autopay matching, past-due flags, and a `⇄` linkage to sinking funds
for annual bills. The bills screen is presented alongside the existing
Envelopes screen (period budgets) and Sinking Funds screen (multi-month
goals). [Issue #318](https://github.com/rmwarriner/tulip-accounting/issues/318) flagged
that the relationship between these three concepts is not pinned down
anywhere — and the wireframes deliberately stayed agnostic on it so the
data-model question could be resolved before any of it became implementable.

What's already shipped, and constrains this decision:

- **ADR-0001** treats `Envelope` and `SinkingFund` as distinct `Pool`
  subtypes in a parallel shadow ledger. Both have shipped (`tulip-core
  allocation/`). Refills into either pool use the shared `RefillRule`
  value object.
- **No `Bill` entity exists** anywhere in the codebase as of 2026-05-17.
  Recurring transactions are handled informally via the importer +
  user-driven entry; there is no scheduled-payment object, no past-due
  detection, no auto-match against the bank feed.

Three model shapes were considered for where Bills should live:

1. **Single entity (collapse)** — every recurring outflow is an envelope.
   `Bill` is a property of certain envelopes ("category=bill"). Sinking
   funds also collapse into envelopes-with-targets.
2. **Three entities, explicit cross-refs** — `Bill`, `Envelope`,
   `SinkingFund` stay distinct. Bills *link* to envelopes (which expense
   bucket the post lands in) and optionally to sinking funds (which
   accumulator funds the annual payment).
3. **Bill as a property of Envelope or SinkingFund (hybrid)** —
   `Envelope` gains an optional `payment_schedule` field. `SinkingFund`
   already has `target_date`. The "Bills" screen is a filtered view over
   both, not a separate entity.

## Decision

**Adopt (2) — three distinct entities, with optional cross-references.**

`Bill` is a new first-class domain entity alongside `Envelope` and
`SinkingFund`. All three are scoped per `household_id` and follow the
same `tulip-core` / `tulip-storage` split that ADR-0001 established.

### Shape (preliminary — final field set will be refined in the
implementation slice)

```
Bill
├─ household_id            (FK households.id; tenant scope)
├─ id                      (UUID v4)
├─ payee                   (str; normalized form used for matching)
├─ display_name            (str; what the UI shows)
├─ status                  (enum: active | paused | archived)
├─ pay_from_account_id     (FK accounts.id; optional — null for "any")
├─ payment_method          (enum: ach_pull | ach_push | card | check
│                                | manual | unknown)
├─ expected_amount_kind    (enum: fixed | statement | estimated)
├─ expected_amount         (Money; null when kind=statement)
├─ schedule                (RecurrenceSpec; see "Schedule shape" below)
├─ next_due_on             (DATE; denormalised from schedule for
│                          forecast/sort queries — like Pool balance)
├─ charge_envelope_id      (FK allocation_pools.id; optional. The
│                          envelope the spend lands in when posted.)
├─ funded_by_sinking_fund_id  (FK allocation_pools.id; optional. The
│                          sinking fund accumulating for this bill.
│                          Surfaces the `⇄` marker in the wireframes.)
├─ match_amount_tolerance_pct   (NUMERIC; null = use global default)
├─ match_date_tolerance_days    (INTEGER; null = use global default)
├─ past_due_tolerance_days      (INTEGER; null = use global default)
├─ last_paid_transaction_id     (FK transactions.id; null until matched)
├─ last_paid_on                 (DATE; null until matched)
├─ created_by_user_id, created_at, updated_at
```

`RecurrenceSpec` is a structured value object — same posture as
[`RefillRule`](../../packages/tulip-core/src/tulip_core/allocation/refill_rule.py): no
expression eval, finite set of recurrence shapes (monthly-on-day,
monthly-nth-weekday, fixed-interval-days, annual-on-date,
quarterly-on-day). The exact discriminator set lands in the
implementation slice and gets its own short ADR if it grows.

### Cross-reference semantics

- **`charge_envelope_id`** — purely informational at the `Bill` level.
  It does not auto-tag the matched transaction's `posting.pool_id`; the
  envelope linkage that drives the shadow ledger is on the *posting*,
  not on the bill. The bill→envelope link is what the Bills screen uses
  to show "Envelope: Utilities" in the detail strip and to power the
  Envelopes-screen `▣` marker for "this envelope is scheduled."
- **`funded_by_sinking_fund_id`** — drives the `⇄` marker on Sinking
  Funds. When set, the bill participates in the annual-bill
  accumulation pattern: the sinking fund refills over the year, the
  bill draws it down when it pays. Both directions must be the same
  household; both pools must be active.

### What `RefillRule` is and is not

`RefillRule` (existing, ADR-0001) governs **how money flows into an
allocation pool on a schedule.** It belongs on the `SinkingFund` (or on
an `Envelope`) — not on the `Bill`. A bill that's funded by a sinking
fund inherits the accumulation cadence from `SinkingFund.refill_rule`,
not from a separate field on `Bill`.

`Bill.schedule` is the **payment schedule** — when the outflow is
expected, not when accumulation happens. The two are different concepts
that happen to both be "recurring."

### What ships when

This ADR pins the *shape*. It does not ship:

- The `bills` table or migration.
- The `Bill` domain type in `tulip-core`.
- The `BillRepository`, API endpoints, or CLI commands.
- The Bills TUI browser screen.
- The autopay matching engine (matching policy lives in
  [docs/TUI_WIREFRAMES.md §Cross-cutting decisions](../TUI_WIREFRAMES.md);
  the rules engine is its own future slice).

[#309](https://github.com/rmwarriner/tulip-accounting/issues/309) explicitly slices Phase 9 v1 as P9.0–P9.4 covering accounts,
transactions, reports, and reconciliation/import status — **not** bills.
The Bills implementation lands after that, on a separate phase ticket
(provisionally Phase 9.5 or a new Phase 11 depending on what's prioritised
when v1 ships).

## Why this shape

- **Symmetry with the existing model.** ADR-0001 already chose distinct
  pool types (Envelope, SinkingFund) over collapse. A third distinct
  entity is the consistent continuation. The "fewer concepts is better"
  argument was made and lost in ADR-0001; replaying it for Bills would
  reopen that decision.
- **The cross-refs are real, not imagined.** The wireframes show a
  `⇄` marker tying annual bills to sinking funds. That linkage *exists*
  in user mental models — "I save monthly into the auto-insurance fund,
  then the insurance bill draws it down once a year." Collapsing them
  loses the relationship; explicit cross-refs preserve it.
- **Bills carry state nothing else does.** Past-due tolerance, match
  policy, last-paid linkage, statement-based vs estimated amount —
  these are not envelope concepts and not sinking-fund concepts. Bolting
  them onto Envelope inflates a working, tested domain object with
  optional fields that mean something only for the "this envelope is
  also a bill" subset.
- **Implementation cost is contained.** The shadow ledger does not
  change. Bills are a sibling table that *references* allocation_pools
  but does not extend it. No ADR-0001 invariants are touched.

## Alternatives considered

### (1) Single entity (every recurring outflow is an envelope)

Rejected. Would require unwinding ADR-0001's Envelope-vs-SinkingFund
distinction (they have different semantics — period-bounded vs
goal-bounded — that don't degrade gracefully into one shape). Also
loses the bill ↔ sinking-fund linkage entirely: you can't have
"my Vacation envelope is funded by my Vacation envelope" coherently.

### (3) Bill as a property of Envelope (hybrid)

Rejected, but more narrowly than (1). The objection isn't structural —
you *could* hang a `payment_schedule` off Envelope and call it done.
The objection is:

- It conflates two lifecycles (envelope-period vs payment-schedule)
  that have independent cadences. A monthly envelope can be funded by a
  bi-weekly refill and drained by a weekly schedule of small bills; one
  shape doesn't model that well.
- The `⇄` linkage to sinking funds gets awkward. A sinking fund linked
  to an "envelope-that-is-actually-a-bill" reads weirdly in the model
  and in the docs.
- The Bills screen would still have to filter envelopes by "has a
  schedule," producing the same UI surface as having a distinct entity,
  for no domain-modelling saving.

## Consequences

### Positive

1. **Wireframes become implementable** without ambiguity. The Bills
   screen has a domain backing; the Envelopes `▣` marker has a
   referent; the Sinking Funds `⇄` marker has a referent.
2. **No churn on ADR-0001.** Shadow ledger, pool taxonomy, refill rule
   — all unchanged. The new entity sits next to them.
3. **Matching engine has a clean place to live.** A future
   `BillMatcher` runs per-tenant over the bank feed, scoring against
   active bills using each bill's tolerance fields (falling back to
   global defaults from settings). One concern, one module.
4. **Past-due, skip, pause are first-class operations on `Bill`.**
   They're not properties of envelopes-with-schedules retroactively.

### Negative

1. **Yet another table to migrate, query, scope-by-household,
   tenant-test, audit-log, and cover.** The "three pool types plus a
   bill table" surface is bigger than option (3)'s "envelope-with-
   schedule." The trade is paid in code volume and test count.
2. **Two ways to express "I always pay $200/mo for X."** Option A: an
   envelope with a $200 monthly budget. Option B: a $200 monthly bill
   charging that envelope. The Bills screen and Envelopes screen
   together must teach the user when each shape is right (bill = a
   *specific recurring payment*; envelope = a *budget bucket*, possibly
   funded by multiple bills or by ad-hoc spending). The wireframes
   already lean into this distinction; docs will need to reinforce it.
3. **Bill ↔ Envelope cross-ref is informational, not enforcing.** A
   matched transaction's `posting.pool_id` is still authoritative for
   the shadow ledger. If a user fat-fingers the envelope on a manual
   txn that the matcher links to the bill, the bill says
   "charge_envelope=Utilities" but the shadow ledger says
   "Miscellaneous." This is by design (the posting is the truth) but
   surfaces as "wait, why didn't this hit my Utilities envelope?"
   surprises until a UI affordance ("apply bill's envelope to matched
   txn?") lands.

### Neutral

1. **The `RecurrenceSpec` shape is left to the implementation slice.**
   It will mirror `RefillRule`'s structured-value-object style. If the
   discriminator set grows past ~5 cases or needs cron-style flexibility,
   that's a new ADR.
2. **Match policy defaults live in settings, not in `Bill`.** Per-bill
   overrides are nullable columns. The system-wide defaults
   (`match_amount_tolerance_pct=5`, `match_date_tolerance_days=3`,
   `past_due_tolerance_days=5`) are captured in
   [TUI_WIREFRAMES.md §Cross-cutting decisions](../TUI_WIREFRAMES.md).

## References

- [TUI_WIREFRAMES.md](../TUI_WIREFRAMES.md) — Bills screen and the
  `⇄`/`▣` markers this decision underpins.
- [ADR-0001](0001-envelope-shadow-ledger.md) — envelope + sinking-fund
  shadow ledger (the model this extends).
- [ADR-0007](0007-terminal-ui.md) — Phase 9 TUI scope (v1 is read-only;
  Bills implementation is post-v1).
- [#318](https://github.com/rmwarriner/tulip-accounting/issues/318) — the design issue this ADR resolves.
- [#309](https://github.com/rmwarriner/tulip-accounting/issues/309) — Phase 9 kickoff and its P9.0–P9.4 slice plan
  (Bills implementation explicitly out of scope).

## Decision log

| Date       | Decision                                                                                  | By                  |
|------------|-------------------------------------------------------------------------------------------|---------------------|
| 2026-05-17 | `Bill` adopted as a third distinct entity with optional `charge_envelope_id` + `funded_by_sinking_fund_id`. | #318 design slice |
| 2026-05-17 | `RecurrenceSpec` shape deferred to implementation slice (structured value object, no eval). | #318 design slice |
| 2026-05-17 | Bills implementation explicitly out of #309's P9.0–P9.4 scope; lands on a separate phase ticket. | #318 design slice |

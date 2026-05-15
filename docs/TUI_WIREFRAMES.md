# tulip TUI wireframes (design exploration)

> **Status:** design exploration · 2026-05-15
> **Scope:** wireframes only — not a committed spec, not scheduled, not gated.
> Use as a starting point if/when a TUI surface is picked up. Open questions
> below need decisions before any of this becomes implementable.

This document captures wireframe sketches for a possible terminal UI on top of
the tulip data model. The mockups assume monospace rendering (box-drawing
characters, fixed-width columns); the design language is intended for a Python
TUI framework like [Textual](https://textual.textualize.io/) or
[urwid](https://urwid.org/) but is framework-agnostic at this level.

The dashboard is the home view. The other seven screens are drill-ins reached
via single-key actions from the dashboard footer or via the `▸` affordance on
header-strip vitals.

---

## Cross-cutting patterns

Reused conventions that span screens. Keeping them consistent reduces what the
user has to learn.

### Vitals strip

Pinned beneath the title bar on every screen. Three pieces of context that the
user never wants to lose track of:

```
│ ⟳ synced 14m    Unalloc: $408.78 ▸    7d→$11,892 ▸         │
```

- **Sync state** — `⟳ synced 14m ago` (fresh) · `⟳ synced 6h ago` (stale-ish,
  dim) · `⚠ sync failed 2h ago` (warn) · `⟳ syncing…` (in-flight, animate).
- **Unallocated income** — `Unalloc: $408.78 ▸` (positive) · `Unalloc: $0.00 ✓`
  (fully assigned) · `⚠ Over-budget: -$42.18` (spent more than this period's
  income). `▸` glyph hints "drill in to assign."
- **Forecast end-value** — `7d→$11,892 ▸` reflects the active forecast window.
  `▸` drills into the full Forecast screen. `tab` cycles 7/14/30 in the header
  without leaving the screen.

The three drill-ins (`u` to assign, `f` to forecast, `r` to sync) are
available on every screen, not just the dashboard.

### Status markers

Single-character glyphs in a left margin column. Meanings are stable across
screens:

| Marker | Meaning                                                            |
|--------|--------------------------------------------------------------------|
| `▶`    | Cursor (selected row) — rendered as reverse video, shown as `▶` in mocks |
| `●`    | Healthy / synced / on-track                                        |
| `⚠`    | Needs attention (over budget, stale, past-due, unreconciled)       |
| `⚡`   | Falls within the active forecast window                            |
| `◌`    | Pending / uncleared                                                |
| `↔`    | Paired internal transfer (two legs of one move)                    |
| `⇄`    | Linked to another entity (e.g. sinking fund ⇄ bill)                |
| `○`    | Archived / inactive (hidden by default)                            |
| `✓`    | Confirmed (reconciled, completed, accepted)                        |

### Selected-item detail strip

A 3–5 row block at the bottom of most list screens that elaborates on the
cursor row. Pattern:

- Row 1: name + context
- Row 2: the headline metric or warning math
- Row 3: history / trend / linkage info
- Row 4 (optional): explanation, additional warning, or note
- Last row: context-sensitive one-key actions

This is where the *why* and the *what to do about it* live. Keeps the list
table dense without dropping decision-grade detail.

### Footer keybind conventions

Two-line footer; first line is single-key row actions, second line is global /
search / navigation. Examples:

```
  ↑↓ nav  ⏎ detail  c categorize  e edit  s split
  / search    f filter    space select    q back to dashboard
```

`q` returns to the prior screen. `Esc` cancels modal/edit state. `?` opens a
help overlay (not mocked).

---

## Screens

### Dashboard

The home view — daily quick reference. Two-line header (title + vitals), 2×3
panel grid below.

```
┌─ tulip ─────────────────── 2026-05-15  Mahoney HH ─┐
│ ⟳ synced 14m   Unalloc: $408.78 ▸   7d→$11,892 ▸  │
├─────────────────────────────────────────────────────┤
│ Accounts                │ Envelopes (May 2026)      │
│  Checking    $3,241.18  │  Groceries $412/$600 ▓▓▓░ │
│  Savings   $12,500.00   │  Gas        $88/$200 ▓░░░ │
│  Visa        -$842.55   │  Dining    $215/$250 ▓▓▓░ │
├─────────────────────────┼───────────────────────────┤
│ Pending (uncleared)     │ Sinking funds             │
│⚠04-22 Chk #1042   -$240 │  Car repair $1,200/$3,000 │
│ 05-09 Card hold    -$85 │  Vacation     $650/$5,000 │
│ 05-12 ACH xfer    -$120 │  Insurance    $420/$1,200 │
├─────────────────────────┼───────────────────────────┤
│ Recent transactions     │ Bills due soon            │
│ 05-14 Trader Joe -$67   │  05-18 Mortgage  -$1,850  │
│ 05-13 Shell       -$42  │  05-20 Visa pay    -$842  │
│ 05-12 Payroll  +$2,418  │  05-25 Electric    -$112  │
│ 05-12 Mortgage-$1,850   │  05-29 Internet     -$89  │
└─────────────────────────┴───────────────────────────┘
 [a]cct [e]nv [t]xn [p]end [f]cast [b]ills [s]ink  tab:7/14/30
```

**Purpose:** "How are we doing?" in one screen. Optimized for a daily glance,
not for doing work. Each panel is a fixed window into a domain; everything is
read-only here. Acting on anything means drilling in.

**Anatomy:**

- **Six panels** — Accounts, Envelopes, Pending, Sinking funds, Recent
  transactions, Bills due soon. Each panel previews 3–4 rows of the
  corresponding full screen.
- **Stale-check flagging** — `⚠` on the Pending panel for items older than the
  stale threshold. The user's primary case: tracking that a check written
  weeks ago hasn't been cashed.
- **Bills due soon** complements the in-header forecast end-value. Header says
  "where you'll land"; this panel says "what's about to hit". Together they
  answer the runway question without a chart.
- **Footer** — one key per drill-in, plus `tab` to cycle the forecast window
  shown in the vitals strip without leaving the screen.

**Open questions:** none specific to this screen at this stage of exploration.
The dashboard is the most-iterated screen and feels settled relative to others.

---

### Transactions list

The full transaction ledger, reachable via `[t]xn` from the dashboard.

```
┌─ tulip · Transactions ───────────── 2026-05-15  Mahoney HH ─┐
│ ⟳ synced 14m     Unalloc: $408.78 ▸     7d→$11,892 ▸        │
├──────────────────────────────────────────────────────────────┤
│ Account: [all ▾]    Period: [May 2026 ▾]    Search: ____ /  │
│ Show: [☑ posted ☑ pending ☑ income ☐ transfers]   312 txns  │
├──────────────────────────────────────────────────────────────┤
│      Date   Description       Account   Envelope     Amount │
│ ──────────────────────────────────────────────────────────── │
│      05-14  Trader Joe's      Checking  Groceries  -$ 67.21 │
│      05-13  Shell             Visa      Gas        -$ 42.18 │
│      05-12  Payroll — Acme    Checking  —       +$2,418.00  │
│      05-12  Mortgage Co       Checking  Housing -$1,850.00  │
│ ▶    05-11  Netflix           Visa      Subs       -$ 15.49 │
│ ⚡    05-10  Amazon            Visa      ?? AI(3)   -$ 84.02 │
│      05-09  Costco            Checking  Groceries -$ 218.44 │
│ ◌    05-08  Card hold (Shell) Visa      Gas        -$ 42.00 │
│      05-07  Whole Foods       Checking  Groceries -$ 113.88 │
│      05-06  ACH transfer in   Savings   —          +$500.00 │
│ ⚠    04-22  Check #1042       Checking  Housing    -$240.00 │
│ ──────────────────────  47 of 312  ·  ↓ more below ──────── │
└──────────────────────────────────────────────────────────────┘
 ↑↓ nav   ⏎ detail   c categorize   e edit   s split   x clear
 / search    f filter    space select    q back to dashboard
```

**Anatomy:**

- **Filter bar (2 rows)** — account picker, period picker, search; checkbox
  row for status filters; total-matching count on the right.
- **Marker column** — `▶` cursor, `⚡` AI proposal pending, `◌` pending,
  `⚠` stale (>14d uncleared).
- **`?? AI(3)`** in the envelope column means "uncategorized, 3 AI proposals
  waiting." Connects to the AI categorizer.
- **`space` to multi-select** for batch categorize/edit operations.
- **Status line** under the table for viewport position (`47 of 312`).

**Open questions:**

1. **AI-proposal interaction** — pressing `⏎` on a row with `⚡` should either
   (a) open a detail screen with the 3 proposals listed (consistent with other
   drill-ins), or (b) expand inline like an accordion (faster but more state).
   Power-categorizers want (b); design simplicity favors (a).
2. **Running balance column** — useful for reconciliation, adds visual noise.
   Default off, toggleable with a key?
3. **Group by date** — current view interleaves txns. Could insert subtle
   date dividers when scrolling long lists.
4. **Transfers display** — currently filterable out. When shown, render as
   one row (`→ Savings`) or as the matched pair?

---

### Forecast (full)

Cash projection, reachable via `[f]cast` from the dashboard or via the `▸`
glyph on the in-header forecast end-value.

```
┌─ tulip · Forecast ─────────────── 2026-05-15  Mahoney HH ──┐
│ ⟳ synced 14m    Unalloc: $408.78 ▸    7d→$11,892 ▸         │
├─────────────────────────────────────────────────────────────┤
│ Window:  7d  [14d]  30d  60d  90d        Scope: [Liquid ▾] │
├─────────────────────────────────────────────────────────────┤
│ Projected balance — Liquid (Checking + Savings)             │
│                                                             │
│ $15k ┤●                                                     │
│ $14k ┤ ●                                                    │
│ $13k ┤  ●                                                   │
│ $12k ┤   ●                                                  │
│ $11k ┤    ●                            ●●●                  │
│ $10k ┤     ●                                                │
│  $9k ┤      ●        ●                                      │
│  $8k ┤- - - -●●●●●●●●- - - - - - - - - - -  ← min $7,842   │
│      └┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──→           │
│       Now    +3    +6    +9   +12   +14d                    │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│ ⚠ Checking alone dips to $208 on 05-26 (Visa autopay)       │
│   Suggested: transfer $500 Savings → Checking before 05-25  │
│   [a] accept · [m] modify · [d] dismiss                     │
├─────────────────────────────────────────────────────────────┤
│ Upcoming events (next 14d)                                  │
│  +3d   05-18  Mortgage Co                 ● rec  -$1,850.00 │
│  +5d   05-20  Visa autopay                ◐ stmt   -$842.55 │
│  +7d   05-22  Payroll — Acme              ● rec +$2,418.00  │
│ +10d   05-25  Electric                    ○ est    -$112.00 │
│ +11d   05-26  Internet                    ● rec     -$89.00 │
│ +14d   05-29  Mortgage Co                 ● rec  -$1,850.00 │
│                                                             │
│ Legend:  ● recurring   ◐ statement-based   ○ estimated      │
└─────────────────────────────────────────────────────────────┘
  ↑↓ nav events   ⏎ event detail   s simulate   e edit recurring
  tab cycle window    a switch scope    q back to dashboard
```

**Anatomy:**

- **Window selector** — same 7/14/30 as the dashboard plus 60d/90d for the
  longer view. Carries over whatever was active when the user drilled in.
- **Scope picker** — `Liquid` (Checking + Savings) is the default. `a` cycles
  through Liquid / All accounts / per-account / Net (post credit-card
  payoff). The per-account view surfaces "Checking alone dips" warnings.
- **Chart** — daily end-of-day balance points. Dashed horizontal line at the
  projected minimum makes it findable.
- **Warning block** — most actionable thing on the screen. Given its own slab
  between chart and events list. One-key accept on the suggested transfer.
- **Events list** — drivers of the curve. Confidence marker per event:
  `●` recurring (known schedule) · `◐` statement-based (Visa: balance known
  but not paid yet) · `○` estimated (avg of last 6 mo for variable bills).

**Open questions:**

1. **Simulate (`s`) interaction** — opens an inline form: "Add hypothetical
   txn: date / account / amount" → re-renders chart with that txn included.
   Lets you answer "can I afford a $1,200 car repair next Friday?" without
   committing data. Worth building, or out of scope?
2. **Per-account series on the chart** — should the chart optionally overlay
   multiple lines (Checking solid, Savings dashed)? Or always single-series
   and switch via scope picker? Single-series is simpler and matches the
   rest of the design.
3. **Confidence intervals** — for `○ est` events, the actual could vary.
   Show a shaded band around the line, or keep it as a single point estimate
   with the per-event marker doing the disclosure?
4. **Recurring edit (`e`)** — pressing `e` on a recurring event drills into
   recurring-schedule management. Separate screen worth mocking (not yet
   covered here).

---

### Accounts

Account list and reconciliation overview, reachable via `[a]cct`.

```
┌─ tulip · Accounts ─────────────── 2026-05-15  Mahoney HH ──┐
│ ⟳ synced 14m    Unalloc: $408.78 ▸    7d→$11,892 ▸         │
├─────────────────────────────────────────────────────────────┤
│        Account              Institution      Balance        │
│  ─────────────────────────────────────────────────────────  │
│   Assets ────────────────────────────────────  $19,991.28   │
│  ●     Checking ····· 4218  Big Bank        $3,241.18 ✓rec  │
│  ●     Savings ······ 7702  Big Bank       $12,500.00 ✓rec  │
│  ⚠     HSA ·········· 0153  Fidelity        $4,250.10 ⚠Feb  │
│                                                             │
│   Credit ────────────────────────────────────     -$842.55  │
│  ●     Visa Platinum  9924  Big Bank Card    -$842.55 ✓rec  │
│             limit $5,000  util 17%  stmt $725.00 due in 18d │
│                                                             │
│  ─────────────────────────────────────────────────────────  │
│   Net worth: $19,148.73 · Liquid: $14,898.18 · 4 accounts   │
├─────────────────────────────────────────────────────────────┤
│ ▶ HSA — Fidelity                                            │
│   ⚠ stale sync (3d ago, threshold 24h)         retry: [s]   │
│   ⚠ not reconciled since 2026-02-15        reconcile: [r]   │
└─────────────────────────────────────────────────────────────┘
  ↑↓ nav  ⏎ open txns  r reconcile  e edit  n new  s sync
  /search    a archive    h show hidden    q dashboard
```

**Anatomy:**

- **Groups (Assets / Credit)** with subtotals. A third group (Loans /
  Investments) appears when relevant. Keeps credit-utilization math
  visually separate from cash.
- **Account number masking** — `····· 4218` (last four with dotted leaders).
- **Status marker** — `●` healthy · `⚠` needs attention · `○` archived
  (`h` to show hidden).
- **Trailing badge** — `✓rec` reconciled within window · `⚠Feb` not
  reconciled since that month.
- **Credit-card sub-line** — limit / utilization / statement balance /
  payment due. Cards carry state nothing else has, so they get a second row.
- **Selected detail strip** — shows the *why* behind the cursor row's status
  flags with one-key actions for each.

**Open questions:**

1. **Scope of "accounts"** — checking/savings/credit clearly in. Investment
   / brokerage accounts? The project is cash-flow-focused, not net-worth
   tracking, so probably no — or a read-only "investments" group with one
   aggregated balance per institution.
2. **Sort within a group** — balance descending? Manual order? Most-recently
   used? Manual is most flexible but a TUI faff.
3. **Group by Type vs Institution** — Type shown above. Institution would
   group all Big Bank accounts together (checking + savings + visa). Toggle
   with `g`?
4. **Open-txns drill-in target** — pressing `⏎` could either (a) jump to the
   existing Transactions screen pre-filtered to that account, or (b) open a
   per-account view optimized for reconciliation. (a) is consistent;
   (b) is reconcile-flow-optimized.

---

### Envelopes

Monthly category budgets, reachable via `[e]nv`.

```
┌─ tulip · Envelopes ────────────── 2026-05-15  Mahoney HH ──┐
│ ⟳ synced 14m    Unalloc: $408.78 ▸    7d→$11,892 ▸         │
├─────────────────────────────────────────────────────────────┤
│ Period: [May 2026 ▾]      Day 15 of 31 (48%)       16 left │
├─────────────────────────────────────────────────────────────┤
│    Envelope           Spent  /  Budget        Remaining     │
│ ─────────────────────────────────────────────────────────── │
│   Needs                                                     │
│      Groceries     $412.55 /  $600.00 ▓▓▓▓▒░░   $187.45    │
│      Gas            $88.40 /  $200.00 ▓▓░░░░░   $111.60    │
│  ⚠   Household     $124.00 /  $150.00 ▓▓▓▓▓▒░    $26.00    │
│      Utilities      $89.00 /  $250.00 ▓▓▒░░░░   $161.00    │
│                                                             │
│   Wants                                                     │
│  ⚠   Dining out    $215.10 /  $250.00 ▓▓▓▓▓▒░    $34.90    │
│      Subscriptions  $42.49 /   $50.00 ▓▓▓▓▓▒░     $7.51    │
│      Hobbies        $18.00 /  $100.00 ▓░░░░░░    $82.00    │
│                                                             │
│   Bills                                                     │
│      Mortgage           $0 /$1,850.00 ░░░░░░░ $1,850.00 ▣  │
│      Insurance          $0 /  $180.00 ░░░░░░░   $180.00 ▣  │
│                                                             │
│   Family                                                    │
│      Kids           $62.18 /  $100.00 ▓▓▓▓░░░    $37.82    │
│                                                             │
│ ─────────────────────────────────────────────────────────── │
│   Total          $1,051.72 /$3,830.00 ▓▓░░░░░ $2,778.28    │
├─────────────────────────────────────────────────────────────┤
│ ▶ Dining out                                                │
│   $215.10 spent of $250 · 86% used at 48% of month         │
│   ⚠ Burn rate $448/mo → projected to exceed by $198        │
│   Last 30d avg $234   ·   Last 90d avg $221                │
│   [f] fund $25   [m] move from another   [t] txns   [e]    │
└─────────────────────────────────────────────────────────────┘
  ↑↓ nav  ⏎ open txns  f fund  m move  e edit  n new envelope
  g toggle groups    p prior period    q back to dashboard
```

**Anatomy:**

- **Period strip** — month picker + day-of-month progress so users can compare
  each envelope's spend-% against the period-% (48%).
- **Groups (Needs / Wants / Bills / Family)** — user-defined; toggle off
  with `g` for a flat list.
- **`⚠` marker** flags envelopes whose burn-rate projects an over-budget
  month. Computed from spend-pace vs period-pace, not raw spent vs budget.
- **`▣` marker** indicates scheduled (linked to a recurring bill). The
  envelope sits dormant at $0 until the recurring txn lands.
- **Total row** at the bottom mirrors per-group totals if/when those get
  added.
- **Selected detail strip** — burn-rate math, recent-trend comparison, and
  the canonical envelope-budgeting move: `m` to pull from another envelope.

**Open questions:**

1. **Rollover semantics** — at month-end, does leftover roll into June,
   return to unallocated, or absorb into a designated bucket? Probably a
   per-envelope selector (`rollover / sweep / fixed`) on the edit screen.
2. **Bills envelopes vs. the Bills-due-soon panel** — there's overlap. Two
   clean answers: (a) the Bills group on this screen *is* the bills list
   (each scheduled bill is an envelope of category=bill), or (b) bills live
   separately and don't pollute envelopes. Leaning (a) — fewer concepts.
3. **Group subtotals** — small visual cost, decent payoff for "how much have
   we spent on Needs vs Wants this month?"
4. **6-month sparkline in detail strip** — a tiny inline `▁▂▃▅▇█` trend of
   monthly spend per envelope. Cheap to render, useful for spotting runaway
   categories.

---

### Pending

Uncleared transactions and stale-check tracking, reachable via `[p]end`.

```
┌─ tulip · Pending ──────────────── 2026-05-15  Mahoney HH ──┐
│ ⟳ synced 14m    Unalloc: $408.78 ▸    7d→$11,892 ▸         │
├─────────────────────────────────────────────────────────────┤
│ Account: [all ▾]   Type: [☑ chk ☑ hold ☑ ach ☑ xfer]       │
│ Sort: [age ▾]                                    9 pending  │
├─────────────────────────────────────────────────────────────┤
│     Date    Description            Acct      Type   Amount  Age│
│ ────────────────────────────────────────────────────────────── │
│   Stale (>14d)                                                 │
│ ⚠   04-22  Check #1042 — Smith    Checking   chk  -$240.00 23d│
│ ⚠   04-28  Check #1043 — St.V     Checking   chk   -$75.00 17d│
│                                                                │
│   Recent                                                       │
│     05-14  Card hold — Shell      Visa       hold  -$42.00  1d│
│     05-13  Check #1044 — electric Checking   chk  -$180.00  2d│
│     05-12  ACH out — IRA          Savings    ach  -$500.00  3d│
│     05-12  Card hold — Amazon     Visa       hold  -$84.02  3d│
│ ↔   05-11  Xfer Sav→Chk           Savings    xfer -$200.00  4d│
│ ↔   05-11  Xfer Sav→Chk (in)      Checking   xfer +$200.00  4d│
│                                                                │
│ ────────────────────────────────────────────────────────────── │
│   Out: -$1,121.02   In: +$200.00   Net pending: -$921.02   │
│   "Real" liquid: $14,898 − $921 = $13,977                  │
├─────────────────────────────────────────────────────────────┤
│ ▶ Check #1042 — Smith                                       │
│   Written 04-22 · 23 days outstanding · Checking            │
│   Memo: "Lawn repair quote — May invoice"                   │
│   ⚠ Most banks stop honoring checks after 180 days          │
│   [c] mark cleared  [v] void  [r] reissue  [n] add note     │
└─────────────────────────────────────────────────────────────┘
  ↑↓ nav  ⏎ detail  c clear  v void  m match-to-bank  n new
  /search  f filter  q back to dashboard
```

**Anatomy:**

- **Two visual groups: Stale (>14d) and Recent** — stale is the actionable
  bucket (call, void, reissue). Recent is "expected to clear, ignore."
- **Age column** instead of sub-rows. Keeps each item one line so the screen
  scales to 20+ pending items.
- **`↔` marker for paired transfers** — both legs of an internal transfer
  show as separate rows; marker indicates the pairing. Detail strip shows
  the partner row.
- **Per-type markers (chk / hold / ach / xfer)** — different lifecycles,
  different actions in the detail strip.
- **Net pending and "real" liquid** — raw balance minus
  committed-but-uncleared = what's actually available. Prevents the
  "wait, I thought I had $14k" overdraft surprise.
- **Detail strip** — context-sensitive: a check shows memo + bank-expiration
  warning + void/reissue actions; a card hold shows capture window +
  auto-expiry; an ACH shows clear ETA.

**Open questions:**

1. **Auto-match policy** — when a manually-entered check eventually posts via
   the bank feed, fuzzy-match by amount+date+account and auto-clear, or
   always require user confirmation? Auto-match is faster; manual is safer.
2. **Stale threshold per type** — 14d is the placeholder. Card holds become
   stale faster (~5d, since banks expire at 7); checks at 14–30d; ACH after
   ~5 business days. Per-type thresholds or one global?
3. **Auto-expire card holds** — bank feed drops a hold without capture →
   tulip inherits that. What about holds entered only via direct user input
   (rare)? Probably never — card holds are bank-fed only.
4. **Surface "real liquid" in vitals?** — current vitals shows `Unalloc` and
   `7d→`. Could add `Real: $13,977` for pending-aware available. Or keep
   that level of detail on this screen only.

---

### Bills

Recurring bill management, reachable via `[b]ills`.

```
┌─ tulip · Bills ────────────────── 2026-05-15  Mahoney HH ──┐
│ ⟳ synced 14m    Unalloc: $408.78 ▸    7d→$11,892 ▸         │
├─────────────────────────────────────────────────────────────┤
│ Status: [☑ active ☐ paused]    Sort: [next due ▾]           │
│                                              13 active bills│
├─────────────────────────────────────────────────────────────┤
│    Bill                  Pay from   Method   Next     Amount│
│ ─────────────────────────────────────────────────────────── │
│  ⚠ Past due                                                 │
│  ⚠   Auto loan          Checking   ACH     was 05-10  $320 │
│         expected 5d ago, not seen in feed                   │
│                                                             │
│   Monthly                                                   │
│  ⚡   Mortgage Co        Checking   ACH      05-18  $1,850  │
│  ⚡   Visa autopay       Checking   ACH      05-20  stmt $843│
│  ⚡   Electric           Checking   ACH      05-25    ~$112 │
│  ⚡   Internet           Visa       card     05-26       $89│
│      Netflix            Visa       card     06-01       $15│
│      Spotify            Visa       card     06-04       $11│
│      Gas company        Checking   ACH      06-09     ~$78 │
│                                                             │
│   Quarterly                                                 │
│      Trash service      Checking   check    06-30       $95│
│                                                             │
│   Annual                                                    │
│      Auto insurance     Savings    ACH      11-02    $1,440│
│      Domain renewal     Visa       card     09-14       $24│
│      Costco membership  Visa       card  2027-03-08     $65│
│                                                             │
│ ─────────────────────────────────────────────────────────── │
│   Monthly burn: $4,852     Annual obligation: $59,750      │
│   Next 7d: $2,693 (⚡)     Next 30d: $5,047                │
├─────────────────────────────────────────────────────────────┤
│ ▶ Electric — Big Energy Co                                  │
│   Next 05-25 (in 10d) · Pay from Checking · ACH autopay    │
│   Envelope: Utilities · Variable, avg $112 over last 6mo   │
│   Last 6: $98 · $115 · $124 · $108 · $119 · $112           │
│   [m] mark paid  [s] skip once  [p] pause  [e] edit  [h] hist│
└─────────────────────────────────────────────────────────────┘
  ↑↓ nav  ⏎ detail  m mark paid  s skip  p pause  e edit  n new
  /search  f filter  h history    q back to dashboard
```

**Anatomy:**

- **Past due block** — appears only when something's overdue (expected bill
  not seen in feed within tolerance). "Not seen in feed" is the actionable
  why.
- **Frequency groups (Monthly / Quarterly / Annual)** — so annuals don't get
  lost between monthly noise.
- **Amount column variants** — fixed (`$1,850`), `stmt $843` (statement-based),
  `~$112` (estimated from history). Tells you the trustworthiness of the
  forecast number.
- **`⚡` marker** — bills hitting within the active forecast window. Same
  semantics as forecast screen.
- **Date format** — `MM-DD` within current year, full `YYYY-MM-DD` when next
  occurrence crosses into next year.
- **Aggregate row** — monthly burn + annual obligation (full-year sum
  including quarterlies/annuals so they can't sneak up) + 7d / 30d totals.
- **Detail strip** — for variable bills, last 6 occurrences inline for
  variance at a glance.

**Open questions:**

1. **Autopay matching** — when an autopay'd bill posts, match it back to the
   schedule so "last paid" updates and past-due disarms. Match by payee +
   amount window + expected date window. How fuzzy? (False match = bill
   marked paid when it wasn't; false miss = nag about a paid bill.)
2. **Past-due tolerance** — 5d feels right for autopays; too eager for
   external ACH. Per-bill tolerance or one global?
3. **Credit card "bills" specifically** — Visa autopay shows `stmt $843`,
   the statement balance at cycle-cut. Warn if statement > available in
   Checking on due date? (Cross-screen logic.)
4. **Skip-once vs. pause** — `skip once` removes one occurrence; `pause`
   removes all occurrences until manually resumed. Both should flow into
   the forecast correctly.

---

### Sinking funds

Multi-month savings goals and bill-paired accumulations, reachable via
`[s]ink`.

```
┌─ tulip · Sinking funds ─────────── 2026-05-15  Mahoney HH ──┐
│ ⟳ synced 14m    Unalloc: $408.78 ▸    7d→$11,892 ▸          │
├─────────────────────────────────────────────────────────────┤
│ Status: [☑ active ☐ completed ☐ paused]   Sort: [need-by ▾] │
│                                              8 active funds │
├─────────────────────────────────────────────────────────────┤
│     Fund              Saved / Target     Need-by   Need $/mo│
│ ─────────────────────────────────────────────────────────── │
│   Goals                                                     │
│       Car repair    $1,200 /  $3,000 ▓▓▓▓░░░   open    $200│
│  ⚠    Vacation 2026   $650 /  $5,000 ▓░░░░░░   08-15 $1,450│
│       Kitchen reno  $2,400 /  $8,000 ▓▓▓░░░░  ~2027    $467│
│                                                             │
│   Recurring annuals                                         │
│  ⚡ ⇄  Auto insurance   $420 / $1,200 ▓▓▓▓░░░  11-02    $158│
│     ⇄  Property tax  $1,100 / $3,200 ▓▓▓░░░░  12-15    $300│
│     ⇄  HOA annual      $200 /   $480 ▓▓▓▓░░░  09-01     $70│
│                                                             │
│   Reserves                                                  │
│       Emergency fund $4,200 /$10,000 ▓▓▓░░░░   open    $250│
│       Buffer          $500 /   $500 ▓▓▓▓▓▓▓  ✓ full     — │
│                                                             │
│ ─────────────────────────────────────────────────────────── │
│   Saved: $10,670 of $31,380 target (34%)                   │
│   Planned $/mo: $1,025     Avg actual: $876 (last 3mo)     │
├─────────────────────────────────────────────────────────────┤
│ ▶ Vacation 2026                                             │
│   $650 / $5,000 saved · need by 08-15 (3 months out)        │
│   ⚠ Behind: need $1,450/mo to hit target                    │
│       (currently $200/mo — short by $1,250/mo)              │
│   Linked: Savings · Standalone (not bill-linked)            │
│   Last contributed: 05-01 ($200)                            │
│   [c] contribute  [w] withdraw  [e] edit goal  [p] pause    │
└─────────────────────────────────────────────────────────────┘
  ↑↓ nav  ⏎ history  c contribute  w withdraw  e edit  n new
  /search  f filter    q back to dashboard
```

**Anatomy:**

- **Three groups: Goals / Recurring annuals / Reserves** — different mental
  models. Goals have a one-time finish line. Recurring annuals refill on a
  cycle and pair with a Bill on the Bills screen. Reserves are open-ended.
- **`⇄` marker** — fund is linked to a bill schedule. When the bill pays,
  the fund draws down.
- **`⚡` marker** — need-by date is inside the active forecast window.
- **`⚠` marker** — behind schedule (won't hit target at current pace). The
  required catch-up $/mo is in the rightmost column.
- **"Need $/mo" column** — required monthly contribution to hit the target
  on time. Behind: catch-up number. Open-ended: current rate. Completed: `—`.
- **`✓ full`** — fund is at target. Stays visible (you want to know the
  buffer is intact) but doesn't demand attention.
- **Aggregate row** — total saved/target, planned vs. *actual* monthly
  contribution. The gap is the diagnostic.

**Open questions:**

1. **Bill-fund linkage flow** — when you create an annual bill, prompt to
   create a paired sinking fund, auto-create it, or stay manual?
   Auto-create is opinionated but matches how the screens are meant to be
   used together.
2. **What happens at need-by + draw-down** — when the bill pays and the fund
   is drawn to zero, does the fund auto-restart for next year's cycle, or
   require manual reset? For `⇄` recurring funds, auto-restart feels right.
   Goals just go to "completed."
3. **Behind threshold** — at what tolerance is a fund flagged `⚠`? 100%
   strict is noisy. 90% of plan? 30-day grace?
4. **Withdrawal mechanics** — when you `w` withdraw to pay an unexpected
   expense, does the money flow back to its source envelope, become a txn
   on the linked account, or get tracked as a fund-event? Third is cleanest
   but adds a concept.

---

## Cross-cutting open questions

Decisions that affect multiple screens. These should land before any of
this becomes implementable scope.

### Bill ↔ envelope ↔ sinking-fund model

These three concepts overlap in the current sketches:

- A monthly bill could be (a) an envelope of category=bill, (b) a separate
  Bill entity, or (c) both linked.
- A sinking fund could be (a) paired with a Bill (`⇄` marker), or (b) an
  independent goal.
- The Envelopes screen shows a "Bills" group; the Bills screen exists
  separately; sinking funds can link to bills.

A coherent data model needs to pick one of:

1. **Single entity**: every recurring outflow is an envelope. Bills are just
   envelopes with a schedule. Sinking funds are just envelopes with a target
   and need-by date. One concept, many screens look at it from different
   angles.
2. **Three entities, explicit links**: Bill, Envelope, SinkingFund are
   separate domain objects with optional cross-references. More flexibility,
   more cognitive load on the user.
3. **Hybrid**: Bills and Envelopes are unified; sinking funds stay separate
   because their time horizon and accumulation semantics differ.

The wireframes are loosely consistent with (3) but the question deserves a
proper ADR before any of this is built.

### "Real liquid" surfacing

The Pending screen computes `Real liquid = raw balance − net pending`. This
is the number that actually matters for "can I afford X right now." It
appears nowhere on other screens. Options:

- Add to vitals strip as a third figure (replaces or supplements `Unalloc`).
- Surface only on Accounts and Pending screens.
- Keep it on Pending only (current sketch).

### Stale thresholds

Many screens have "stale" concepts with placeholder thresholds:

- Account sync: 24h placeholder (Accounts)
- Pending check: 14d placeholder (Pending, Dashboard)
- Card hold: 5d? (mentioned, not encoded)
- Bill past-due: 5d placeholder (Bills)

These should be:

- Configurable globally with sensible defaults.
- Overridable per-entity for edge cases (a known-slow ACH endpoint, a
  long-outstanding check the user has accepted will never clear).

### AI categorization integration

`⚡` in the transactions list and `?? AI(N)` in the envelope column are
disclosure surfaces for AI-suggested categorization. The full
AI-categorize flow isn't mocked yet. Decisions to make:

- Single-confidence threshold for "auto-categorize," or always show
  proposals for user confirmation?
- Batch-categorize UX: multi-select with `space`, then `c` — what does the
  confirm screen look like?
- When AI proposes 3 categories with confidences 0.78 / 0.14 / 0.05, is the
  default `⏎` to accept the top one, or does it open the selection list?

### Recurring-bill matching policy

When the bank feed posts a transaction that "looks like" a known recurring
bill (Mortgage Co for $1,850 on/around the 18th), tulip should match it back
to the schedule. False matches mark bills paid when they weren't; false
misses generate spurious past-due flags. The matching rules need to be:

- Explicit (the user can see why a match was made).
- Editable (the user can confirm/reject and feed back into the rules).
- Conservative by default (better to false-miss and nag than false-match
  and miss an actual missed bill).

---

## Screens not yet mocked

Surfaces referenced but not drafted in this round:

- **AI-categorize flow** — what the user sees when they press `c` on a
  transaction with `⚡` or on a multi-selection.
- **Assign-unallocated flow** — what the user sees when they press `u` from
  any screen, with `$408.78 ▸` in the vitals.
- **Reconcile flow** — what happens when `r` is pressed on the Accounts
  screen for an unreconciled account.
- **Add/edit forms** — new account, new envelope, new bill, new sinking
  fund, edit recurring schedule, edit goal target.
- **Settings screen** — stale thresholds, sync policies, AI confidence
  cutoffs, group definitions, household management.
- **Help overlay (`?`)** — full keybind reference per screen.

Worth mocking when this design direction gets picked up.

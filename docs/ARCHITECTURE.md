# Tulip Accounting — Architectural Specification (v1)

**Status:** Phases 0–7 complete; Phase 8 (operations + hardening) in progress. Internal-beta ready. The full v1 surface is in place: ADR-0005 AI integration end-to-end, all nine reports rendered in HTML/PDF/CSV, hledger-format journal export + import, and the `tulip reports` / `tulip journal` CLI groups. Phase 8 deep security + deep privacy audits have shipped, along with their highest-severity Wave-1 follow-ups and a post-audit CLI/importers usability bundle.
**Document version:** 1.3
**Date:** 2026-04-29 (original) · 2026-05-07 (Phase 5 close + roadmap refresh) · 2026-05-12 (Phase 7 close) · 2026-05-14 (Phase 8 audits + Wave-1)

---

## 1. Project Overview

Tulip Accounting is a household-focused, double-entry accounting system with first-class envelope budgeting and sinking-fund support. v1 ships a hardened, multi-user-capable API server and a scriptable CLI client. The architecture is designed from day 1 for eventual deployment as a multi-tenant cloud service, even though v1 targets a single-household home-server install.

### 1.1 Design Principles

1. **Correctness first.** Decimal arithmetic everywhere. Double-entry invariants enforced at the database layer where possible, in the application layer otherwise.
2. **Tenant-aware from day 1.** Every row carries a `household_id`; every query is scoped. Adding multi-tenancy to a system not built for it is a year-long refactor; doing it now is one column and a query helper.
3. **Boring choices for load-bearing pieces.** SQLAlchemy 2.0, FastAPI, Pydantic, pytest, alembic, structlog — all mature, all well-documented, all known to Claude Code.
4. **Plain old data over clever data.** Schemas are explicit, normalized where it matters, denormalized only where measured.
5. **Defense in depth.** SQLCipher for the database, separate field-level encryption for the most sensitive fields, separate file-level encryption for attachments. No single key compromise leaks everything.
6. **AI as a participant, never a gatekeeper.** Every AI capability has a non-AI fallback path. The system is fully usable with all AI features disabled.
7. **Errors are actionable, not apologetic.** No error message ever says "an error occurred," "something went wrong," or any other generic placeholder. Every error names *what* failed, *why*, and — wherever the system has enough context — *what the user can do to recover*. Internal identifiers (UUIDs, stack frames, raw exception names) belong in logs, not in user-facing copy. See §7.8 for the standards (RFC 9457 Problem Details on the API; equivalent shape in the CLI).
8. **Recover gracefully where the user expects it.** Transient failures (network blips, AI provider rate limits, attachment-store stalls) retry with backoff before surfacing. Hard failures degrade to a known-good fallback (rule-based categorization when the AI provider is down; local export when the cloud is unreachable; queued action with an explicit "will retry at ..." status). The system does not silently drop work.

### 1.2 v1 Scope (in)

- API server (Python + FastAPI)
- CLI client (Python + Click or Typer; ledger-style command verbs)
- SQLite storage backend (with abstraction layer ready for Postgres)
- Single-household installation, multi-user
- Soft-close period model
- Envelope budgeting + sinking funds (distinct entities)
- Scheduled transactions (server-side runner)
- Importers: OFX, QIF, CSV
- Journal-format export and basic import (lossy round-trip, documented)
- Manual reconciliation + statement-driven matching
- Attachments (receipts, statement PDFs)
- Append-only audit log
- TOTP-based MFA
- Toner-friendly PDF, HTML, CSV reports
- Structured JSON logging
- Encrypted scheduled backups + CLI restore
- Pluggable AI provider adapters (Anthropic, OpenAI, Google, Ollama via litellm); BYOK; per-tenant + per-user policy

### 1.3 v1 Scope (out — explicitly deferred)

- Web/mobile front-end clients (other clients planned later, against the same API)
- Postgres backend (abstraction designed for it; integration deferred)
- True OS-level audit log immutability (deferred to Postgres phase)
- Multi-tenant cloud deployment (architecture supports it; ops work deferred)
- OCR on receipts and full-text attachment search (deferred to v1.x)
- WebAuthn / passkey MFA (deferred; TOTP only in v1)
- FX rate fetching and revaluation (multi-currency schema in place; rate engine deferred)
- Polished journal-format importer (basic import in v1; polish in v1.1)

---

## 2. Technology Choices

### 2.1 Language and Framework

| Layer | Choice | Rationale |
|---|---|---|
| Language | **Python 3.12+** | Best-in-class AI SDK ecosystem; excellent decimal handling; readable for a developer returning from an early-2000s background |
| API framework | **FastAPI** | Auto-generates OpenAPI 3 spec from Pydantic models — that spec is the contract every future client (CLI, mobile, web, importers) consumes |
| ORM / DB toolkit | **SQLAlchemy 2.0** (async) | Mature multi-backend support; well-known to Claude Code; clean migration story via Alembic |
| Validation / models | **Pydantic v2** | First-class FastAPI integration; rigorous typing; fast |
| Migrations | **Alembic** | Standard SQLAlchemy companion |
| Package manager | **uv** | Fast, reproducible installs; workspace support; replaces pip+venv+pip-tools |
| CLI framework | **Typer** | FastAPI-style ergonomics for the CLI; auto-completion; good help text |
| Testing | **pytest** + **pytest-asyncio** + **hypothesis** + **pytest-cov** + **schemathesis** | Industry-standard TDD stack |
| HTTP server | **uvicorn** behind **gunicorn** for production | Standard FastAPI deployment |
| Logging | **structlog** | Structured JSON logs that drop straight into ELK / Loki / Datadog |
| HTTP client (AI providers, importers) | **httpx** | Async, modern, used by FastAPI tooling |
| AI provider abstraction | **litellm** | Single API surface across Anthropic, OpenAI, Google, Mistral, Ollama, etc. |

### 2.2 Versioning and Compatibility

- API versioning by URL prefix: `/v1/...`. Breaking changes go to `/v2/`.
- Deprecation policy: announce in `/v1/.well-known/deprecations`, support deprecated endpoints for at least one minor version after announcement.
- Database schema versioning via Alembic; every release ships migrations.

---

## 3. Architecture

### 3.1 Topology — v1 (single-household, home server)

```
┌────────────────────────────────────────────────────────────┐
│                      Home Server (LAN)                     │
│                                                            │
│   ┌─────────────┐    ┌─────────────────────────────────┐   │
│   │  CLI client │◄──►│         Tulip API Server        │   │
│   │             │    │        (FastAPI + uvicorn)      │   │
│   └─────────────┘    │                                 │   │
│                      │   ┌─────────────────────────┐   │   │
│   ┌─────────────┐    │   │  Scheduled Tx Runner    │   │   │
│   │  Importer   │◄──►│   │  (in-process scheduler) │   │   │
│   │  (CLI cmd)  │    │   └─────────────────────────┘   │   │
│   └─────────────┘    │                                 │   │
│                      │   ┌─────────────────────────┐   │   │
│   ┌─────────────┐    │   │  AI Adapter (litellm)   │   │   │
│   │  Reporter   │◄──►│   │  → Anthropic/OpenAI/... │   │   │
│   │  (CLI cmd)  │    │   └─────────────────────────┘   │   │
│   └─────────────┘    └─────────────────────────────────┘   │
│                                  │                         │
│                                  ▼                         │
│                      ┌───────────────────────┐             │
│                      │   SQLCipher (SQLite)  │             │
│                      │   + encrypted attach. │             │
│                      └───────────────────────┘             │
└────────────────────────────────────────────────────────────┘
```

### 3.2 Topology — eventual (multi-tenant cloud)

Same shape, with:
- Postgres replacing SQLite
- Object storage (S3/MinIO) replacing local filesystem for attachments
- Reverse proxy (Caddy or nginx) terminating TLS
- Separate worker process for scheduled-tx runner (decoupled from API for HA)
- Per-tenant key isolation enforced by application layer + DB row-level policies

### 3.3 Tenancy Model

- **`households`** (= tenants). Every domain entity carries `household_id`. Foreign keys are always composite (`household_id`, `entity_id`) for query-locality and to make tenant-scoping a query-builder concern, not a "did the developer remember" concern.
- A SQLAlchemy event listener enforces `household_id` filtering on every query. Bypassing it requires an explicit `with admin_scope():` context manager (used only by tenant-creation, audit, and migration code).
- The CLI authenticates a user; all CLI requests are implicitly scoped to that user's household.

### 3.4 User & Permission Model

- **Users** belong to exactly one household in v1 (multi-household membership deferred — schema-friendly to add).
- **Roles** (per household): `admin`, `member`, `viewer`.
  - `admin`: manage users, settings, scheduled tx, period close, see and edit all accounts including private ones, set tenant AI policy, set tenant MFA policy.
  - `member`: see and edit shared accounts, manage own private accounts/envelopes/sinking funds.
  - `viewer`: read-only on shared accounts; no visibility into private accounts.
- **Visibility** (per account / envelope / sinking fund): `shared` (default) or `private` (creator + admins only).
- **Right to erasure** (GDPR Art. 17 / CCPA §1798.105 — shipped Phase 8 Wave-1):
  - `DELETE /v1/users/{user_id}` — erases one user; cascades their `sessions` + `mfa_recovery_codes` and redacts their PII from `audit_log` snapshots. Admin-only (or self).
  - `POST /v1/households/me/erase-request` → `DELETE /v1/households/me` — a two-step, token-gated household erasure (the confirmation token lives in `pending_household_erasures`). The `DELETE` cascades every household-scoped table via `ondelete="CASCADE"` and garbage-collects attachment ciphertext from disk. Admin-only.

---

## 4. Data Model

All money fields are `Numeric(precision=20, scale=8)` and always paired with a 3-letter ISO 4217 currency code. Internal arithmetic uses `decimal.Decimal` exclusively. **No `float` ever touches money.**

### 4.1 Core Entities

```
households
  id (uuid pk)
  name
  base_currency (ISO 4217, default 'USD')
  created_at, updated_at
  master_key_wrapped (bytes — wrapped per the encryption section)
  ai_policy (json — see §6.5)
  mfa_policy (enum: optional|required_for_admins|required_for_all)

users
  id (uuid pk)
  household_id (fk)
  email (unique within household)
  password_hash (argon2id)
  display_name
  role (enum)
  totp_secret_encrypted (nullable)
  recovery_codes_hashed (json)
  ai_user_overrides (json — per-capability overrides)
  created_at, updated_at, last_login_at

accounts
  id (uuid pk)
  household_id (fk, indexed)
  parent_account_id (nullable fk — chart of accounts is a tree)
  code (e.g., '1100' — optional but enabled by default for canned starter)
  name
  type (enum: asset, liability, equity, income, expense)
  subtype (e.g., 'checking', 'credit_card', 'cash', 'fixed_asset')
  currency (ISO 4217)
  visibility (enum: shared, private)
  is_active (bool)
  external_account_number_encrypted (nullable, field-level encrypted)
  notes_encrypted (nullable, field-level encrypted)
  created_by_user_id, created_at, updated_at

allocation_pools (base for envelopes + sinking_funds + system pools — see [ADR-0001](adrs/0001-envelope-shadow-ledger.md))
  id (uuid pk)
  household_id (fk)
  pool_type (enum: envelope, sinking_fund, inflow, unallocated, spent) — discriminator
  name
  visibility (enum)
  currency (ISO 4217)
  is_active (bool)
  is_system (bool — auto-created system pools have is_system=true)
  created_by_user_id, created_at, updated_at
  — Note: balance is derived from sum(shadow_postings), NOT stored on this row.
  —       The earlier `current_balance` column was dropped per ADR-0001.

envelopes (joined to allocation_pools)
  pool_id (pk + fk)
  budget_period (enum: weekly, biweekly, monthly, quarterly, annual, custom)
  budget_amount (Numeric)
  rollover_policy (enum: reset, accumulate, cap_at_budget)
  refill_rule (json — see §5.3)

sinking_funds (joined to allocation_pools)
  pool_id (pk + fk)
  target_amount (Numeric)
  target_date (date)
  contribution_strategy (enum: manual, even_split, percentage_of_income)
  contribution_amount (Numeric, nullable)

transactions
  id (uuid pk)
  household_id (fk, indexed)
  date (date — accounting date)
  posted_at (timestamp — entry time)
  description
  reference (free text — check number, confirmation, etc.)
  status (enum: pending, posted, reconciled)
  cleared_at (nullable timestamp)
  reconciled_at (nullable timestamp)
  reconciliation_id (nullable fk)
  scheduled_tx_id (nullable fk — if materialized from a schedule)
  imported_from_id (nullable fk — if from an import batch)
  notes_encrypted (nullable)
  created_by_user_id, created_at, updated_at

postings (the actual double-entry lines)
  id (uuid pk)
  transaction_id (fk, indexed)
  account_id (fk)
  pool_id (nullable fk — if this posting affects an envelope/sinking fund)
  amount (Numeric — signed; +debit, -credit by convention)
  currency (ISO 4217)
  fx_rate (Numeric, nullable — when posting currency != account currency)
  fx_amount (Numeric, nullable — amount in account currency)
  memo

  CONSTRAINT: SUM(amount) per transaction_id, per currency, must = 0
    (enforced via DB trigger on SQLite; CHECK constraint on Postgres)

scheduled_transactions
  id (uuid pk)
  household_id (fk)
  template (json — full transaction template including postings)
  schedule (json — RRULE-style: frequency, interval, byday, etc.)
  next_run_at (timestamp, indexed for the runner)
  last_run_at (nullable)
  is_active (bool)
  approval_required (bool — if true, materializes as 'pending' and requires user action)
  created_by_user_id, created_at, updated_at

reconciliations
  id (uuid pk)
  household_id (fk)
  account_id (fk)
  statement_date
  statement_starting_balance, statement_ending_balance
  status (enum: in_progress, complete, abandoned)
  created_by_user_id, created_at, completed_at

attachments
  id (uuid pk)
  household_id (fk)
  filename (original)
  content_type
  size_bytes
  content_hash (sha256)
  storage_uri (e.g., 'fs://<uuid>')
  data_key_wrapped (bytes — wrapped attachment data key)
  uploaded_by_user_id, uploaded_at

attachment_links (many-to-many)
  attachment_id (fk)
  entity_type (enum: transaction, account, reconciliation, sinking_fund, ...)
  entity_id (uuid)
  created_at

import_batches
  id (uuid pk)
  household_id (fk)
  source_format (enum: ofx, qif, csv, journal)
  source_filename
  imported_count, skipped_count, error_count
  raw_payload_attachment_id (nullable fk — original file kept as attachment)
  status (enum: parsed, applied, rolled_back)
  created_by_user_id, created_at, applied_at

audit_log
  id (uuid pk)
  household_id (fk, indexed)
  occurred_at (timestamp, indexed)
  actor_user_id (nullable — null for system actions like scheduler firing)
  actor_kind (enum: user, system, ai_agent, importer)
  action (enum: create, update, delete, login, logout, mfa_enroll, period_close, period_reopen, ai_invoke, ai_approve, ai_reject, ...)
  entity_type
  entity_id
  before_snapshot (json, nullable)
  after_snapshot (json, nullable)
  request_id (correlation id — see §7.2)
  ip_address (nullable)
  user_agent (nullable)
  metadata (json — action-specific context)

ai_invocations
  id (uuid pk)
  household_id (fk)
  user_id (nullable — null for scheduler-triggered)
  capability (enum: categorization, nl_query, forecasting, agentic)
  provider (e.g., 'anthropic', 'openai', 'ollama')
  model (e.g., 'claude-opus-4-7')
  prompt_token_count, completion_token_count
  cost_estimate_usd (nullable)
  status (enum: success, error, refused, awaiting_approval, approved, rejected)
  outcome_summary
  request_id (correlation id)
  occurred_at

periods
  id (uuid pk)
  household_id (fk)
  start_date, end_date
  status (enum: open, soft_closed)
  closed_by_user_id (nullable), closed_at (nullable)
  reopened_by_user_id (nullable), reopened_at (nullable)
```

**Auxiliary tables** (auth, AI, scheduling, notifications — created by migrations, not detailed in the core-entities view above): `sessions` (refresh-token store, SHA-256), `mfa_recovery_codes` (argon2id), `used_mfa_challenges` (single-use MFA-challenge `jti` burn list — Phase 8 Wave-1), `pending_household_erasures` (two-step GDPR Art. 17 erasure tokens — Phase 8 Wave-1), `pending_proposals` (AI proposals awaiting human approval), `notifications`, and `scheduled_jobs`. Two tenancy notes on these: `pending_proposals.ai_invocation_id` carries a **composite FK** `(household_id, ai_invocation_id) → ai_invocations` (Phase 8 Wave-1 — closes a gap where a proposal could reference another household's invocation); and every household-scoped table declares `ondelete="CASCADE"` from `households.id`, which is what makes the household-erasure endpoint a single `DELETE`.

### 4.2 Double-Entry Invariants

- For any `transaction`, the sum of `postings.amount` per currency must equal zero. Enforced via:
  - SQLite: `AFTER INSERT/UPDATE/DELETE` trigger on `postings` that raises if a violation is detected at end of statement.
  - Postgres (later): `DEFERRABLE INITIALLY DEFERRED CHECK` constraint, or trigger.
- A transaction may not be `posted` until its postings balance.
- Multi-currency transactions: each currency's postings must independently sum to zero. FX gain/loss postings are required for currency-crossing transactions (engine generates them automatically, see §5.6).
- Postings to closed periods are rejected at the API layer with a warning that the period is soft-closed; the user can override (logged with reason in audit_log).

### 4.3 Account Codes (Chart of Accounts)

Default canned starter pack (`us_household.yaml`) ships with US household-appropriate accounts. Loaded at household creation. Sample structure:

```yaml
accounts:
  - code: "1000"
    name: "Assets"
    type: asset
    children:
      - code: "1100"
        name: "Cash"
        children:
          - code: "1110", name: "Checking", subtype: checking
          - code: "1120", name: "Savings", subtype: savings
      - code: "1200"
        name: "Investments"
        # ...
  - code: "2000"
    name: "Liabilities"
    type: liability
    children:
      - code: "2100", name: "Credit Cards"
      - code: "2200", name: "Mortgage", subtype: long_term_debt
  - code: "3000"
    name: "Equity"
    # ...
  - code: "4000"
    name: "Income"
    # ...
  - code: "5000"
    name: "Expenses"
    children:
      - code: "5100", name: "Food"
      - code: "5200", name: "Housing"
      # ...
```

Seed loader is generic — additional templates (debt-payoff, side-hustle, full GAAP) can be added later as data files only, no code changes.

---

## 5. Features

### 5.1 Double-Entry Accounting

Standard double-entry. The accounting engine module (`tulip.core.accounting`) is the single chokepoint for posting transactions. Any code path that writes a transaction goes through it. Direct INSERTs into `transactions`/`postings` are forbidden by lint rule and architecture-test (see §7.3).

**Posted vs pending balances** (#274): balance queries default to *posted* balances (`status` in `posted`/`reconciled`). `GET /v1/accounts/{id}/balance?include_pending=true` and `GET /v1/reports/trial-balance?include_pending=true` (CLI: `tulip balance --pending`, default `--no-pending`) fold in `pending` transactions; pending-inclusive output is always labelled ("balance (incl. pending)") and pending-affected trial-balance rows carry a `(P)` marker, so it can't be mistaken for the settled ledger.

### 5.2 Envelope Budgeting

> **Mechanic:** see [ADR-0001 — envelope and sinking-fund tracking via shadow ledger](adrs/0001-envelope-shadow-ledger.md). Refills, allocations, transfers, and rollovers are double-entry shadow-ledger transactions; spending is auto-paired from main-ledger postings carrying `pool_id`. Pool balances are derived from `sum(shadow_postings)`, not stored.

- Envelopes are funded from income or from accounts (depending on user model preference — both supported).
- Spending against an envelope is achieved by including a `pool_id` reference on an expense-account posting; the engine auto-pairs a shadow transaction that decrements the envelope.
- Refill happens on a schedule (typically monthly), driven by the scheduled-tx runner reading each envelope's `refill_rule` and posting a shadow refill transaction.
- Overspend is permitted but flagged on reports.

### 5.3 Refill Rules (envelope `refill_rule` JSON)

Three supported strategies, persisted as structured JSON only (no expression
language, no string-to-eval — see [docs/THREAT_MODEL.md §5.1](THREAT_MODEL.md)).
The shape is round-trippable through `tulip_core.allocation.RefillRule.{to,from}_dict`:

```json
{ "strategy": "fixed_amount", "amount": "500.00", "currency": "USD" }

{ "strategy": "fill_to_amount", "amount": "500.00", "currency": "USD" }

{ "strategy": "percentage_of_income", "percentage": "0.10" }
```

`fixed_amount` contributes `amount` per period. `fill_to_amount` tops the
envelope up to `amount` per period. `percentage_of_income` contributes
`percentage` (a fraction in (0, 1]) of the next inflow.

### 5.4 Sinking Funds

- Each sinking fund has `target_amount` and `target_date`.
- Recommended monthly contribution = `(target_amount - current_balance) / months_until_target`, where `current_balance` is the derived `sum(shadow_postings)` for the pool (per [ADR-0001](adrs/0001-envelope-shadow-ledger.md)). Reported live; not auto-applied unless `contribution_strategy` says so.
- Spending from a sinking fund follows the same `pool_id`-on-posting mechanic as envelopes — auto-paired into the shadow ledger.
- Reports show "saved toward goal" rather than "remaining this period."

### 5.5 Period Closing (soft)

- Admin closes a period via `POST /v1/periods/{id}/close`.
- Closed periods are flagged. Subsequent edits to postings dated in a closed period:
  - Return HTTP 200 with a `Warning` header describing the soft-close violation.
  - Are recorded in `audit_log` with `metadata.closed_period_override = true`.
- Reopening a closed period is admin-only and audit-logged.
- Reports show the close date and indicate whether figures are pre-close (immutable in spirit) or post-close-edited.

### 5.6 Multi-Currency

- Every monetary field stores `(amount, currency)`.
- Each account has a single native `currency`.
- A posting may be denominated in any currency; if it differs from the account currency, the engine requires an `fx_rate` and computes `fx_amount` (rounded to the account currency's minor units).
- Currency-crossing transactions automatically generate FX gain/loss postings to a designated `Equity:FX_Gain_Loss` account (created by the seed pack).
- v1 ships the schema and the FX-aware engine. **FX rate fetching from external sources is deferred** — users supply rates manually in v1. A pluggable rate-provider interface is stubbed.

### 5.7 Scheduled Transactions

- Server-side runner. In v1 it's a background task in the API process (using `apscheduler` or a simple async loop with `next_run_at` polling). When we move to multi-tenant cloud, this becomes its own worker process.
- Each scheduled transaction has a template (full transaction including postings) and an RRULE-style schedule.
- `approval_required=true` materializes the transaction as `status=pending` and notifies the household admin (and optionally the configured notifier).
- `approval_required=false` materializes as `status=posted` immediately.
- Every materialization is audit-logged.

### 5.8 Reconciliation

Both flows supported:

1. **Manual flag.** Users can mark transactions as `cleared` (matched against their own records) and `reconciled` (matched against an actual statement). A reconciliation summary report shows uncleared/unreconciled. **Paper-statement reconciliation** (#275): a reconciliation need not be backed by an uploaded statement file — `tulip reconcile start` opens one with `source_import_batch_id IS NULL` and `reconciliation_match.line_id` is nullable, so a user comparing transactions against a paper statement in hand can reconcile without an import batch.
2. **Statement-driven matching.** User imports a statement (OFX or CSV) for an account. The reconciliation matcher compares statement entries against existing transactions and creates a `reconciliation` record. Matching algorithm: amount + date (±3 days configurable) + fuzzy description match. Unmatched statement lines become `pending` transactions awaiting categorization. Unmatched ledger transactions are flagged for review.

### 5.9 Importers (v1)

| Format | Parser library | Notes |
|---|---|---|
| OFX | `ofxtools` (chosen over `ofxparse` for active maintenance + XXE safety) | Most banks/credit cards |
| QIF | Custom line-oriented parser (format is small, public domain) | Older Quicken format; handles `!Account` multi-account files, `S`-line splits, and `L[Account]` transfers |
| CSV | Custom parser with column-mapping config | Per-institution mapping profiles savable as YAML |

Each importer:
- Reads the source file → produces a `proposed_import` (in-memory, with proposed transactions and postings).
- Runs the auto-categorization AI capability if enabled (or rule-based fallback). `--no-categorize` skips this for bulk historical migrations (#202).
- Presents the proposed batch to the user for review.
- On user `apply`, posts transactions atomically and records an `import_batch`.
- Original file is stored as an attachment for audit trail.

**Multi-account QIF** (#195a/#195b, #198): Banktivity-style QIF exports carry several accounts in one file (`!Account` section headers) and intra-file transfers (`L[Account]` links). `POST /v1/imports/multi-account` takes the whole file plus an `account_map` JSON form field (QIF account name → Tulip account) in a single POST; the importer pairs the two legs of each transfer into one balanced transaction, and an unmatched leg falls back to a one-sided line with a warning. Non-transaction QIF sections (`!Type:Cat`, `!Type:Class`, etc.) are skipped rather than erroring.

### 5.10 Journal Format Export/Import (Level 3)

- Export: `tulip export journal --account=<code> --from=<date> --to=<date> > out.journal`. Output is hledger-compatible. Lossy fields (envelope linkage, audit metadata, attachment refs) are emitted as comments where standard format allows.
- Import: `tulip import journal in.journal`. v1 supports postings + standard directives (account, commodity, P, ~). Envelope/sinking-fund metadata in comments is parsed if present (Tulip-specific extension); standard journals import as plain transactions.

---

## 6. AI Integration

> **Phase 6 design is locked in [ADR-0005](adrs/0005-ai-integration.md)**, which closes #102 (AI provider data-flow contract). The subsections below are the architectural sketch; the ADR is authoritative for module structure, per-capability prompt contracts, redaction profiles, policy resolution, audit-log shape, cost / rate enforcement, failure modes, and slice ordering.

### 6.1 Adapter Layer

All AI calls go through `tulip.ai.adapters`. Per-tenant configuration selects a provider; `litellm` provides a uniform call surface. Supported providers in v1:

- Anthropic (Claude family)
- OpenAI (GPT family)
- Google (Gemini family)
- Ollama (local models)
- Generic OpenAI-compatible endpoint (LM Studio, vLLM, etc.)

API keys are stored encrypted (field-level) per household. Per-user keys are also supported (override household keys).

### 6.2 Capabilities (all four, first-class)

1. **`categorize`.** `AICategorizer` plugs into the `Categorizer` Protocol DI seam from P5.3. Classifier sees payee + amount + date + currency + chart of accounts. Returns suggested account code and a confidence score. User reviews on import. Failures fall back silently to `Imbalance:Unknown` so importer flow never blocks on a flaky provider.
2. **`nl_query`.** Two-turn flow per ADR-0005 §Q3: turn 1 emits SQL against a read-only AI view, `tulip_ai.sql_safety.validate_and_rewrite` (built on sqlglot) enforces SELECT-only + tenant-scope + a row cap; turn 2 summarises the results. Raw rows are returned to the user alongside the summary so they can verify.
3. **`forecast` + anomaly detection.** Periodic `daily_insights` job (via the ADR-0002 runner) scans for anomalies (spending >2σ above rolling mean per envelope) and generates forecasts (envelope-runout dates, sinking-fund-on-track via `AIForecastCapability`). Results land in `notifications`; nothing auto-acts. Sinking-fund + envelope variants share a single `ForecastRequest` dataclass.
4. **`agentic` workflows.** AI proposes journal entries, restated budgets, or sinking-fund plans via `AIProposalCapability`. Proposals are stored as `pending_proposals` and require explicit user approval. Once approved, they execute through the same accounting engine path as user-initiated actions, with the audit log noting `actor_kind=ai_agent`, `metadata.proposal_id`, and `ai_invocation_id` linking back to the originating AI call. v1 ships one executor (`envelope_budget_update`); new kinds register one function in `tulip_api.services.proposal_executor._EXECUTORS`.

### 6.3 Privacy Posture

Three policies per capability, applied at tenant level (admin sets):

- `permissive` (default for v1) — capability runs against the configured cloud provider without per-action approval.
- `requires_approval` — every invocation pauses for explicit user approval before sending data off-host.
- `disabled` — capability is unavailable.
- Additionally, each capability can be configured to use a `local_only` provider (Ollama), bypassing the off-host question entirely.

Users can ratchet their own restriction *up* (more cautious than tenant policy) but not *down*.

### 6.4 Audit & Cost Controls

- Every AI invocation produces an `ai_invocations` row with provider, model, token counts, cost estimate, latency, outcome, and a SHA-256 hash of the redacted prompt (`prompt_hash`). Prompt bodies are NOT persisted by default; opt in via `households.ai_policy.log_prompts=true`.
- Per-household monthly cost cap (default: unconfigured / unlimited; admin opts in via `tulip ai config set monthly_cost_cap_usd ...`). Cap reached → behaviour governed by `cost_cap_behaviour`:
  - `degrade` (default when set): swap to `fallback_provider` (typically Ollama). Audit row records `provider=ollama` explicitly — per ADR-0005 §Q7 this is the only place a provider swap is permitted, and it's logged as a budget signal not a silent failover.
  - `hard_fail`: raise `AICostCapped`; no swap.
- Per-user rate limit (default 60 invocations/hour, sliding window) to bound the blast radius of a runaway loop or compromised credential. Configurable via `households.ai_policy.rate_limit_per_hour`.
- Both gates run **pre-call** via `tulip_ai.cost.enforce_pre_call`. Blocked calls still write an `ai_invocations` row with `outcome=rate_limited` / `outcome=cost_capped` so capped capacity is observable.

### 6.5 Tenant AI Policy (`households.ai_policy` JSON shape)

```json
{
  "default_provider": "anthropic",
  "default_model": "claude-opus-4-7",
  "profile": "default",
  "monthly_cost_cap_usd": "10.00",
  "cost_cap_behaviour": "degrade",
  "rate_limit_per_hour": 60,
  "fallback_provider": "ollama",
  "fallback_model": "llama3:70b",
  "log_prompts": false,
  "capabilities": {
    "categorize": { "policy": "permissive",        "provider": null, "model": null, "profile": null },
    "nl_query":   { "policy": "permissive",        "provider": null, "model": null, "profile": null },
    "forecast":   { "policy": "permissive",        "provider": null, "model": null, "profile": null },
    "agentic":    { "policy": "requires_approval", "provider": null, "model": null, "profile": null }
  }
}
```

Capability-level `null` inherits the household default. `profile` selects a redaction profile (`default` / `strict` / `local_only`); `local_only` pins the resolved provider to `fallback_provider` regardless of other config. Edit via `tulip ai config show / set / clear / set-capability / log-prompts`; the API surface is admin-only `GET/PUT /v1/ai/config` + `PUT /v1/ai/config/capabilities/{capability}`.

---

## 7. Cross-Cutting Concerns

### 7.1 Authentication

- **Password hashing:** argon2id (via `argon2-cffi`), with OWASP-2024 minimum parameters; PHC-formatted hashes are self-describing, so `needs_rehash` re-tunes old hashes on next successful login. The login path is constant-time with respect to "user exists" (Phase 8 Wave-1 — closes a user-enumeration timing oracle).
- **Session model for first-party clients (CLI):** API issues a JWT access token (15 min) + opaque refresh token (30 days, rotating). The refresh token is 256-bit `secrets.token_urlsafe` and stored **SHA-256** in `sessions` — full token entropy makes a slow KDF unnecessary, and the deterministic hash lets the revoke-on-logout lookup hit an index. CLI persists tokens in OS keyring (via `keyring` library) — never plaintext on disk.
- **API tokens for non-interactive clients:** scoped, revocable, optionally with expiry. Stored hashed.
- **MFA:** TOTP (RFC 6238) via `pyotp`; TOTP secrets are AES-256-GCM-encrypted at rest. Recovery codes are argon2id-hashed with an 80-bit entropy floor (Phase 8 Wave-1). MFA challenge required at login when enabled: a successful password check issues a short-lived MFA-challenge JWT carrying a single-use `jti`, redeemed at `POST /v1/auth/login/mfa` or `/login/recover`; the `jti` is burned in `used_mfa_challenges` on redemption so a captured challenge token can't be replayed (Phase 8 Wave-1). Default tenant policy: required for admins, optional for members.
- **Auth rate limiting:** the four most abuse-exposed `/v1/auth/*` endpoints sit behind per-IP `slowapi` quotas — see §7.6.

### 7.2 Logging & Observability

**Structured JSON logging via structlog.**

- Each request gets a `request_id` (UUID) attached to the structlog context. The request_id propagates through every log line and into the `audit_log` and `ai_invocations` rows for the duration of the request.
- Standard fields on every log line: `timestamp`, `level`, `event`, `request_id`, `household_id` (when in tenant scope), `user_id` (when authenticated), `module`.
- Default sink: stdout (works directly with Docker, journald, syslog, or direct file capture). Configurable to file with rotation (`logging.handlers.RotatingFileHandler`).
- **PII redaction:** structlog processor redacts known-sensitive fields (account numbers, password fields, TOTP secrets, API keys, the master key, and — as of Phase 8 Wave-1 — email addresses and IP addresses) before serialization. Redaction is whitelist-based on field names; unknown fields are emitted as-is. A test enforces redaction on every known-sensitive field.
- **OpenTelemetry hooks:** `opentelemetry-instrumentation-fastapi` and `-sqlalchemy` are installed but disabled by default. Enable via env var; emits to OTLP endpoint. This makes future observability (Tempo, Jaeger, Honeycomb) a config flip.
- **Log levels:** `DEBUG` for development, `INFO` for production default. `WARNING` for soft-close overrides, AI provider degradation. `ERROR` for failures requiring attention. Standard severity discipline.

**App log vs audit log:** the app log is for operational debugging; the audit log is for security and accounting forensics. They are distinct stores. App log can be rotated/expired; audit log is retained per tenant retention policy (default: forever).

### 7.3 Testing & TDD

The user has explicitly called out comprehensive testing compliant with modern TDD methodologies as a high priority. The test plan reflects this.

**Test pyramid:**

| Level | Tool | Coverage target | Notes |
|---|---|---|---|
| Unit | pytest | ≥90% on `tulip.core.*` | The accounting engine, allocation pools, period closing — anything money-touching |
| Property-based | hypothesis | All accounting invariants | `forall transactions, sum(postings.amount) == 0`. Catches edge cases regular fixtures miss. |
| Integration | pytest + sqlite-on-disk | All API endpoints | Each endpoint has happy-path + permission-violation + validation-failure tests |
| Contract | schemathesis | API surface | Auto-generates tests from OpenAPI spec; runs in CI |
| Architecture tests | `pytest-archtest` or custom | Module boundaries | E.g., "no module outside `tulip.core.accounting` may insert into `postings`" |
| End-to-end | pytest + spawned-server fixture | Critical flows | Login → import OFX → reconcile → close period → export journal |

**TDD discipline:**
- Every feature ships with tests written *before* the implementation (red → green → refactor).
- PRs that don't include tests for new code paths are rejected by CI policy.
- Coverage gate in CI: PR cannot lower coverage below the project floor (currently 85%, target 90%).
- Mutation testing (`mutmut`) run weekly on `tulip.core.*` to surface tests that don't actually constrain behavior.

**Fixtures:**
- `polyfactory` for Pydantic-model-driven test factories.
- A `household_factory` builds a complete household with users, accounts, and starter data; used as a base fixture.
- Hypothesis strategies for `Money`, `Date`, account-tree, and balanced-transaction generation are first-class shared fixtures in `tests/strategies.py`.

### 7.4 Encryption at Rest

**Layer 1: SQLCipher** (full-database encryption).
- Database file is encrypted with a household master key.
- Master key is derived from a passphrase entered at API server startup (interactive prompt, environment variable, or external secret manager — three configured sources).
- All ORM operations are transparent to encryption; SQLCipher decrypts in-memory.

**Layer 2: Field-level encryption** for the most sensitive fields:
- Account numbers (`accounts.external_account_number_encrypted`)
- Free-text notes that may contain PII (`notes_encrypted` on accounts and transactions)
- TOTP secrets (`users.totp_secret_encrypted`)
- API keys (provider keys for AI)
- Encryption: AES-256-GCM with a per-field DEK (data encryption key). DEK is wrapped by the household master key. Nonces are random per write.

**Layer 3: Attachments** (separate file-level encryption):
- Each attachment gets a fresh AES-256-GCM data key.
- Data key is wrapped by the household master key.
- Filesystem path: `<storage_root>/<household_id>/<attachment_id>.enc`.
- A storage backend interface (`AttachmentStore`) abstracts local-filesystem vs S3/MinIO. v1 ships `LocalFilesystemStore`.

**Key management:**
- v1: master key passphrase entered at server startup, held in process memory only. Never written to disk in plaintext. Server cannot serve requests until unlocked.
- v1.x: optional integration with `keyring` (OS-level secret storage) for unattended start.
- Cloud phase: integrate with KMS (AWS KMS / GCP KMS / HashiCorp Vault).

### 7.5 Backup & Restore

- Scheduled backup job (configurable cadence; default daily at 03:00 local).
- Backup is an encrypted archive containing: the SQLCipher DB file, the attachment store, a manifest with versions and timestamps.
- Backup encryption: AES-256-GCM with a backup-specific key derived from the master key + a backup salt. Restore requires the master key.
- Configurable retention (default 30 daily, 12 monthly, 5 yearly).
- CLI: `tulip admin backup [--output PATH]`, `tulip admin restore --from PATH`. Restore is destructive; requires `--confirm` flag.

### 7.6 Rate Limiting

- Per-IP at the reverse-proxy layer (in production deployments).
- Per-user/tenant at the application layer using `slowapi`:
  - **Auth endpoints (shipped — Phase 8 Wave-1, #219):** a single module-level `slowapi.Limiter` keyed on client IP guards the four most abuse-exposed surfaces — `POST /v1/auth/login` (`10/minute`), `/login/mfa` (`10/minute`), `/login/recover` (`10/minute`), `/refresh` (`30/minute`). Exceedance is an RFC 9457 `auth.rate_limited` (429) with a `Retry-After` header. Storage backend is in-memory (fine for single-process SQLite; switch to Redis when multi-replica). Per-email keying was considered and deferred — `slowapi`'s key function runs before the body is parsed; the per-IP gate alone defeats the bulk credential-stuffing case.
  - AI endpoints: per-user (60/hour default) and per-household ($10/month default) — enforced server-side in `tulip_ai.cost.enforce_pre_call`.
  - Bulk endpoints (import, export): limits scaled by payload size.

### 7.7 Notifications

- Pluggable `Notifier` interface.
- v1 implementations: `ConsoleNotifier`, `EmailNotifier` (SMTP).
- Triggers: scheduled tx requires approval, AI invocation awaiting approval, anomaly detected, period close completed, backup failed.
- Future: webhook-based (`WebhookNotifier`) for Slack/Discord/etc.

### 7.8 Error Reporting & Recovery

This is a first-class design principle (see §1.1 #7 and #8); the rules below are how it shows up at each layer.

#### 7.8.1 Two distinct concerns

| Concern | What it means | Where it lives |
|---|---|---|
| **Actionable error messages** (UX) | The user reads the message and knows what failed, why, and how to fix it. No "an error occurred" placeholders, no UUIDs in user copy, no stack traces on response bodies. | API responses, CLI output, validation errors, exception messages that bubble up. |
| **Error recovery / graceful degradation** (system) | Transient failures retry with backoff; hard failures degrade to a known-good fallback rather than fail the request entirely. | AI adapters, attachment store, importers, scheduler runner, backup job, notifier. |

These overlap (a degraded-mode response is still an error message that needs to be actionable), but they are separately enforceable and tested separately.

#### 7.8.2 API error response standard — RFC 9457 (Problem Details)

Every non-2xx response from `tulip-api` is a `application/problem+json` body shaped per **RFC 9457 — *Problem Details for HTTP APIs*** (the successor to RFC 7807).

Required fields on every response:

```json
{
  "type":     "https://tulip.example/errors/transaction-unbalanced",
  "title":    "Transaction does not balance",
  "status":   400,
  "detail":   "The USD postings sum to $1.00 instead of $0. Add an offsetting USD posting of -$1.00 (or correct an existing amount) and resubmit.",
  "instance": "/v1/transactions",
  "code":     "transaction.unbalanced",
  "request_id": "8c0f...-..."
}
```

- **`type`** — URI identifying the problem class. Stable across versions; clients may dispatch on it. Default: a URL under `/.well-known/errors/<code>`. Defaults to `about:blank` only when nothing more specific applies.
- **`title`** — short human-readable summary. Stable per `type`. Suitable for display as a heading.
- **`status`** — HTTP status code, mirrored in the body so clients reading the body alone don't need the response object.
- **`detail`** — long human-readable explanation tailored to *this* occurrence. **This is the field that holds the recovery hint** when one is computable. Always plain English, never identifiers, never raw exception text.
- **`instance`** — URI of the specific failing request. Useful for support tickets.
- **`code`** — machine-readable error code (dotted segments). The contract clients program against; never reused for a different meaning. Examples: `transaction.unbalanced`, `account.unknown`, `auth.invalid_credentials`, `period.closed`.
- **`request_id`** — the same UUID stamped on the response by `RequestIdMiddleware` and bound to the structlog context. Lets a user paste it into a support request and have a sysadmin find the matching log line.

Extension fields per error class (always under additional named keys, never crammed into `detail`):

- `errors: [{loc, code, message}, ...]` for validation failures (one entry per failing field).
- `retry_after_seconds: <int>` for rate limits and transient AI provider failures.
- `closed_period_override_url: <uri>` for soft-close violations (where the recovery is "ask an admin to override").
- `provider_status: {name, last_seen_ok}` when an upstream AI provider is the proximate cause.

#### 7.8.3 Required content for the `detail` field

Every `detail` string must answer:

1. **What** failed in domain terms. Not "validation failed" — say "the transaction's postings don't balance," "no period covers 2024-06-01," "this account is in use by 12 transactions and can't be deleted."
2. **Why**, when the cause is non-obvious from "what."
3. **How to fix it**, when the system has enough information to suggest a fix. Examples:
   - Unbalanced transaction → which currency is off and by how much.
   - Closed-period write → "Period closed 2025-12-31. Ask an admin to reopen, or post the transaction with `override_closed_period=true` if you have admin role."
   - Stale refresh token → "Sign in again." (Not "401 Unauthorized.")
   - Account-in-use on delete → "Deactivate the account instead, or reassign its 12 transactions to a different account first."

If `detail` would just restate `title`, leave it equal to `title` rather than padding with filler.

#### 7.8.4 Anti-patterns the API must never produce

- ❌ `{"detail": "an error occurred"}`
- ❌ `{"detail": "Internal Server Error"}` on a path the application should have anticipated
- ❌ Raw stack traces in any response body (logged at ERROR level, never returned)
- ❌ Naked UUIDs in user copy (use the entity's name where one exists; UUIDs go in logs)
- ❌ Two routes returning the same `code` for substantively different failures
- ❌ Validation errors without `loc` — the user can't tell which field is wrong

#### 7.8.5 CLI error output

The CLI mirrors the same shape:

- A leading bold red title (e.g., **"Transaction does not balance"**).
- An indented `detail` paragraph in plain English (no JSON dump).
- A trailing line: `Code: transaction.unbalanced  ·  Request: 8c0f...`
- Exit codes per category (`0` success, `1` user error, `2` auth, `3` server, `4` network, `5` configuration). The mapping is documented in `docs/CLI.md`.
- `--json` flag emits the raw Problem Details body for scripting.

#### 7.8.6 Exception-message style (server-side internals)

In code, raised exceptions follow the same discipline because they often surface verbatim into log lines and into `detail` after sanitization:

```python
raise UnbalancedTransactionError(
    f"USD postings sum to {balance} instead of zero; "
    f"add an offsetting USD posting of {-balance} or correct an existing amount"
)
```

Never:

```python
raise ValueError("invalid")             # ❌ what's invalid?
raise RuntimeError(f"tx {tx.id} bad")   # ❌ leaks UUID, says nothing useful
```

#### 7.8.7 Recovery patterns

For each class of failure the system has a defined recovery path:

| Failure | Recovery |
|---|---|
| AI provider 5xx / rate-limit | Retry with exponential backoff (`tulip-ai.adapters` decorator). After max retries, fall back to the configured `fallback_provider` (typically Ollama). After fallback also fails, degrade to the rule-based path (auto-categorization → user-defined regex rules) and surface a `provider_unavailable` notification. |
| AI cost cap reached | Capability returns the rule-based result with `reason: "monthly cost cap reached"` and a `notifications` row. Never silently no-op. |
| Importer parse error on row N of M | Persist the import batch as `partial`, report `imported_count`, `skipped_count`, `error_count`, and an `errors` list keyed by source row number. The user reviews and either re-uploads a fixed file or accepts the partial. |
| Network failure during attachment upload | Local staging directory holds the bytes; a background retry (max 5 attempts, 30s/2m/10m/1h/24h backoff) completes the upload. CLI exits with a "queued for retry" message. |
| Backup target unreachable | Backup writes to a local fallback path and the `BACKUP_DEGRADED` notification fires. Subsequent runs prefer the configured target again. |
| Soft-close period write | Reject by default with the explicit recovery hint that an admin can `--override-closed-period` (audit-logged). |
| MFA required but not enrolled | Return 403 with `code: auth.mfa_required` and a recovery `enrollment_url` extension field pointing at `/v1/auth/mfa/enroll`. |

#### 7.8.8 Where this is enforced

- **Tests** — every error path test asserts the response is a Problem Details body, not just the status code. A reusable `assert_problem(resp, code=..., status=...)` helper lives in `packages/tulip-api/tests/_problem_details.py`.
- **OpenAPI spec** — every operation's `responses` block lists the Problem Details schemas it can return; schemathesis (Phase 2.x) drives this and fails on undeclared error responses.
- **Architecture test** — `tests/test_architecture_no_http_exception.py` AST-scans `tulip_api/src/` and rejects any reference to FastAPI's plain `HTTPException`. Re-introducing the legacy pattern is a CI failure.

#### 7.8.9 Status of the current code

✅ Shipped end to end (Phase 2.x.1 – 2.x.4).

1. Project-wide `TulipProblem` base + FastAPI exception handlers (`install_problem_handlers` in `tulip_api.errors`).
2. Typed exception classes whose `code`, `title`, `status`, and `detail` are constructed from a single registry; routers raise these instead of `HTTPException`.
3. `assert_problem` test helper at `packages/tulip-api/tests/_problem_details.py`; every error-path test uses it.
4. `/.well-known/errors/{code}` HTML pages auto-published from `TulipProblem.__subclasses__()`; index at `/.well-known/errors/`.
5. Schemathesis contract tests fuzz every documented operation (`tests/test_openapi_contract.py`); every non-2xx response is `application/problem+json`, including `RequestValidationError` (422), Starlette framework errors (400/404/405/415), and unhandled exceptions (catch-all → `server.internal_error` 500).

---

## 8. Reporting (Toner-Friendly)

User has called out that all printable artifacts must be toner-friendly. This is a first-class design constraint, not a stylesheet afterthought.

**Stylesheet rules (enforced in print stylesheet `tulip-print.css` and PDF templates):**
- No full-page background color. Page background is always white.
- No background fills behind content. Tables use thin black rules (0.5pt) between rows/columns rather than alternating-row shading.
- Color is reserved for **emphasis only**, used sparingly: warning callouts, negative balances. Body text and structural elements are pure black.
- Where shading would aid scanning, use **patterns** (cross-hatch, dots) at low density rather than gray fills.
- Sans-serif body font for legibility (Inter or system-default sans).
- All text contrast meets WCAG AA against white.
- Headers in bold black, never in colored fills.
- Charts: line/bar charts in black with patterned fills; pie charts use distinct line patterns rather than colors. A second monochrome stylesheet is also available for laser printers that render fine patterns poorly (uses heavier rules and explicit numeric labels instead of pattern keys).

**Output formats:**
- PDF (via `weasyprint` — HTML-to-PDF; the same templates render for screen HTML, with the print stylesheet applied for PDF output)
- HTML (for screen review)
- CSV (raw tabular data, no styling applies)

**v1 reports:**
- Trial balance
- Income statement (period vs. period)
- Balance sheet (point-in-time)
- Cash flow (period)
- Envelope status (current period)
- Sinking-fund progress
- Reconciliation summary
- Audit log report (filtered by date range and actor)
- Custom query report (run a saved query, render as table)

---

## 9. Project Layout

**Monorepo with uv workspaces.** Single repository, multiple installable packages, single CI, easy cross-cutting refactors.

```
tulip-accounting/
├── README.md
├── pyproject.toml             # workspace root
├── uv.lock                    # single lockfile for all packages
├── .pre-commit-config.yaml
├── .github/workflows/         # CI: lint, type-check, test, build
│
├── packages/
│   ├── tulip-core/            # pure domain logic; no I/O, no framework deps
│   │   ├── pyproject.toml
│   │   ├── src/tulip_core/
│   │   │   ├── accounting/    # double-entry engine, invariant enforcement
│   │   │   ├── allocation/    # envelopes, sinking funds, refill rules
│   │   │   ├── periods/       # close/reopen logic
│   │   │   ├── money/         # Decimal, Currency, Money value object
│   │   │   └── audit/         # audit log writer interface
│   │   └── tests/
│   │
│   ├── tulip-storage/         # storage abstraction + SQLite implementation
│   │   ├── src/tulip_storage/
│   │   │   ├── models/        # SQLAlchemy models
│   │   │   ├── repositories/  # one per aggregate root
│   │   │   ├── encryption/    # SQLCipher wiring + field-level helpers
│   │   │   ├── attachments/   # AttachmentStore interface + LocalFS impl
│   │   │   └── migrations/    # alembic migrations
│   │   └── tests/
│   │
│   ├── tulip-api/             # FastAPI server
│   │   ├── src/tulip_api/
│   │   │   ├── routers/       # one per resource
│   │   │   ├── schemas/       # Pydantic request/response models
│   │   │   ├── auth/          # JWT, sessions, MFA
│   │   │   ├── middleware/    # request_id, logging, rate limit
│   │   │   ├── scheduler/     # scheduled-tx runner
│   │   │   └── main.py
│   │   └── tests/
│   │
│   ├── tulip-ai/              # AI adapter layer
│   │   ├── src/tulip_ai/
│   │   │   ├── adapters/      # provider-specific (via litellm)
│   │   │   ├── capabilities/  # categorization, nl_query, forecasting, agentic
│   │   │   ├── policy/        # tenant + user policy resolution
│   │   │   └── audit/         # ai_invocations writer
│   │   └── tests/
│   │
│   ├── tulip-importers/       # file-format importers (callable from CLI)
│   │   ├── src/tulip_importers/
│   │   │   ├── ofx/
│   │   │   ├── qif/
│   │   │   ├── csv/
│   │   │   └── journal/       # hledger-format read/write
│   │   └── tests/
│   │
│   ├── tulip-reports/         # report generators
│   │   ├── src/tulip_reports/
│   │   │   ├── trial_balance/
│   │   │   ├── income_statement/
│   │   │   ├── balance_sheet/
│   │   │   ├── cash_flow/
│   │   │   ├── envelope_status/
│   │   │   ├── sinking_fund_progress/
│   │   │   ├── reconciliation_summary/
│   │   │   ├── audit_log/
│   │   │   └── templates/     # Jinja2 + tulip-print.css
│   │   └── tests/
│   │
│   └── tulip-cli/             # CLI client (Typer)
│       ├── src/tulip_cli/
│       │   ├── commands/      # add, register, balance, accounts, import, export, ...
│       │   ├── auth/          # token storage via keyring
│       │   └── main.py
│       └── tests/
│
├── deploy/
│   ├── docker/                # Dockerfile + docker-compose.yml for home-server install
│   ├── systemd/               # systemd unit for non-Docker installs
│   └── scripts/
│
└── docs/
    ├── ARCHITECTURE.md        # this document
    ├── DATA_MODEL.md          # detailed schema reference
    ├── API.md                 # link to auto-generated OpenAPI viewer
    ├── CLI.md                 # CLI reference
    ├── DEPLOYMENT.md
    ├── BACKUP_RESTORE.md
    ├── SECURITY.md            # threat model + key management
    ├── AI.md                  # AI integration details
    └── adrs/                  # Architecture Decision Records (see 0001-envelope-shadow-ledger.md)
```

**Module boundary rules (enforced by architecture tests):**
- `tulip-core` may not import any I/O package (no `tulip-storage`, no `tulip-api`, no third-party DB/HTTP libs).
- `tulip-storage` may import `tulip-core`. The reverse is forbidden.
- `tulip-api` orchestrates `tulip-core`, `tulip-storage`, `tulip-ai`. It is the only layer that knows about HTTP.
- `tulip-cli` talks to `tulip-api` over HTTP. It does not import `tulip-storage` or `tulip-core` directly. (This is the same contract a future web client will follow.)
- `tulip-importers` and `tulip-reports` are CLI tools / libraries that call the API.

---

## 10. Development Phases

The roadmap below is suggested; Claude Code can sequence within each phase as the work clarifies. Per-slice progress lives in [PHASE_STATUS.md](PHASE_STATUS.md); this section is the long-form definition of each phase.

### Phase 0 — Project bootstrap (1–2 sessions) ✅ complete
- uv workspace skeleton; CI pipeline (lint, type-check, test, coverage gate)
- `tulip-core` skeleton with `Money`, `Currency`, `Account` value objects
- Property-based tests for `Money` arithmetic (the foundation invariant)
- Pre-commit hooks (ruff, mypy, secrets-detection)

### Phase 1 — Storage + accounting engine ✅ complete
- SQLAlchemy models for households, users, accounts, transactions, postings
- ~~SQLCipher wiring with master-key passphrase prompt~~ — *deferred; field-level only in v1*
- Field-level encryption helpers (AES-256-GCM via `cryptography`)
- Alembic migration #1: initial schema + balance-enforcement triggers
- Accounting engine: post transaction, balanced-postings invariant, period-aware writes
- Property-based tests over `Money`; example-based + hypothesis-strategies on the engine

### Phase 2 — API surface (auth + accounts + transactions) ✅ complete
- FastAPI app with structured logging and request_id middleware
- Auth endpoints — register, login, refresh, logout
- CRUD for accounts and transactions with permission enforcement
- OpenAPI spec rendered
- Audit log writer wired into every mutation

### Phase 2.x — Cleanup before Phase 3 ✅ complete
- **P2.x.1** ✅ — MFA (TOTP) enrollment, login challenge, hashed recovery codes
- **P2.x.2** ✅ — RFC 9457 Problem Details migration (see §7.8)
- **P2.x.3** ✅ — schemathesis contract tests in CI
- **P2.x.4** ✅ — catch-all unhandled-exception handler so even uncaught exceptions emit `application/problem+json`

### Phase 3 — CLI (Typer) + first useful flows ✅ complete
- ✅ `tulip register`, `tulip auth {login,logout,status}` (login handles MFA + recovery branches)
- ✅ `tulip accounts {list,show,add}` with parent nesting; `tulip add` (transactions) in flag and `--edit` editor modes
- ✅ `tulip balance` (single account or trial-balance summary)
- ✅ Token storage via OS keyring (file-backed fallback under `TULIP_TOKEN_STORE` for tests / CI)
- ✅ End-to-end tests of CLI against a spawned uvicorn (`live_api` fixture)
- 🟡 Toner-friendly print stylesheet — deferred to Phase 8 (#22), where it lands alongside actual reports

#### Phase 3 follow-ups also shipped

These weren't in the original Phase 3 list but landed before Phase 4 because the CLI's full ergonomic loop wanted them:

- Balance + trial-balance API endpoints (#31, PR #37) — `GET /v1/accounts/{id}/balance` and `GET /v1/reports/trial-balance`, both with `?as_of=YYYY-MM-DD`.
- Account nesting end-to-end (#42) — `parent_account_id` consistency rules (type / currency / visibility / no-cycle), reparenting via PATCH, CLI `--parent` flag, tree rendering, parent-name surfacing on `show`. Multi-currency parents deliberately rejected for now (#44 is the holding pen).
- Interactive `tulip add --edit` (#43) — opens `$EDITOR` with a hledger-subset template; reopens with a banner on parse / balance / unknown-account errors.

### Phase 4 — Envelopes + sinking funds ✅ complete
- ✅ Allocation pool models + CRUD; pool transfers; budget-inflow command; per-currency Inflow / Unallocated / Spent system pools per [ADR-0001](adrs/0001-envelope-shadow-ledger.md)
- ✅ Refill rules implementation (`refill_rule` JSON schema, fixed / target-balance / pct-of-inflow shapes; refill-schedule CRUD endpoints + CLI)
- ✅ Scheduled-tx runner — in-process per [ADR-0002](adrs/0002-scheduler-primitive.md); polls `scheduled_jobs` + writes `scheduled_job_runs` audit rows; runs in the FastAPI lifespan with deliberate test-time disable via `enable_runner=False`
- ✅ Envelope + sinking-fund CLI surface (`tulip envelopes`, `tulip sinking-funds`, `tulip refills`, `tulip refill`, `tulip transfer`, `tulip budget-inflow`)

### Phase 5 — Importers + reconciliation ✅ complete

Per [ADR-0004](adrs/0004-reconciliation.md). Closed 2026-05-07 across nine sub-slices (P5.0 → P5.4.d) plus three follow-up cleanup PRs.

- ✅ **P5.0** — Transaction void / PENDING-only edit / hard delete (#55). The un-reconcile dependency for the rest of Phase 5; ships before importers so reconciliation has a working revert path.
- ✅ **P5.1** — Storage layer for imports + reconciliation: `attachments`, `attachment_links`, `import_batches`, `statement_lines`, `reconciliations`, `reconciliation_matches`, `csv_profiles` tables; SQLite-trigger drop-and-recreate dance for `transactions` denorms.
- ✅ **P5.2.a/b/c** — OFX (`ofxtools`, XXE-safe), QIF (hand-rolled line parser), CSV (per-household profiles, YAML round-trip via `yaml.safe_load`) importers + `POST /v1/imports`.
- ✅ **P5.3** — Reconciliation matcher (pure-`tulip-core`, `find_candidates` with bucketed `MatchConfidence` per ADR §Q2) + `Categorizer` Protocol DI seam for Phase 6.
- ✅ **P5.4.a** — Apply / promote endpoints + CLI: statement lines → PENDING ledger transactions via the registered `Categorizer` (currently `NullCategorizer` → `Imbalance:Unknown`).
- ✅ **P5.4.b** — Reconciliation envelope + auto-match: `POST/GET/DELETE /v1/reconciliations`, `/auto-match`, `/complete`, `/matches/{id}/reject`. One IN_PROGRESS reconciliation per account at a time.
- ✅ **P5.4.c** — Manual match (`POST /matches`) + carry-forward (`/carry-forward` CRUD). `/complete` balance equation extended: `sum(matched) + sum(carry_forward) == ending - starting`.
- ✅ **P5.4.d** — `tulip reconcile` CLI (10 subcommands wrapping the endpoints) + new `GET /v1/reconciliations` list endpoint.
- ✅ **Cleanup**: PR #129 (#127 — inbox surfacing prior-completed-recon lines), PR #130 (#114 — relax `import_batch` idempotency index, wire `?force=true`); #118 closed wontfix.

### Phase 6 — AI integration ✅ complete
- ✅ **P6.0** — Privacy audit / data-flow contract: [ADR-0005](adrs/0005-ai-integration.md). Closes #102.
- ✅ **P6.1** — `tulip-ai` package skeleton: `LitellmAdapter`, `PromptRedactor`, `AIInvocationWriter`, `AICategorizer` plugging into the `Categorizer` DI seam (P5.3). Migration for `ai_invocations`, `households.{ai_policy, ai_keys_encrypted}`, `users.ai_keys_encrypted`. CLI: `tulip ai {set-key, forget-key, list-keys, status, preview}`. API: `POST /v1/ai/preview`.
- ✅ **P6.2** — NL query: read-only AI view + two-turn (SQL emission → summarisation) flow with `sqlglot`-validated SQL. `tulip ai ask`, `POST /v1/ai/ask`.
- ✅ **P6.3** — Daily-insights scheduler + anomaly detector + `notifications` inbox (PR #156). `tulip notifications list/dismiss`.
- ✅ **P6.3.b** — `AIForecastCapability` + forecaster wiring slot on the daily-insights handler (PR #157).
- ✅ **P6.4** — Agentic proposals: `pending_proposals` table, `tulip ai propose/approve/reject`, `actor_kind=ai_agent` audit rows on approve (PR #158).
- ✅ **P6.4.b** — AI-driven proposal generation: `AIProposalCapability` + `tulip ai suggest-budget` (PR #159).
- ✅ **P6.5.a** — Pre-call cost-cap + per-user rate-limit chokepoint + `degrade`/`hard_fail` cost_cap_behaviour with explicit-audited fallback swap (PR #161).
- ✅ **P6.5.b** — `tulip ai config` editor + `log_prompts` toggle + `tulip ai status` fallback-semantics callout. `GET/PUT /v1/ai/config` + `PUT /v1/ai/config/capabilities/{capability}` admin surface (PR #163).
- ✅ **P6.5.c** — Sinking-fund forecast extension to the daily-insights handler via a single `ForecastRequest` dataclass (PR #171). Phase 6 closes.

### Phase 7 — Reports + journal export/import ✅ complete
- ✅ **P7.1** — `tulip-reports` package skeleton + all nine reports rendered in HTML via a shared Jinja2 layout (trial-balance, balance-sheet, income-statement, cash-flow, envelope-status, sinking-fund-progress, reconciliation-summary, audit-log, custom-query) (PR #180).
- ✅ **P7.2** — PDF rendering via weasyprint (pydyf + pango), one engine reused across every report (PR #183).
- ✅ **P7.3** — CSV output for every report via the Python `csv` stdlib + a per-report row-flattening adapter (PR #184).
- ✅ **P7.4** — hledger-compatible `GET /v1/journal/export[?start=...&end=...]`; output round-trips through hledger / ledger-cli (PR #186).
- ✅ **P7.5** — hledger-compatible `POST /v1/journal/import`; account paths resolve by code first then `(type, name)`; imported transactions land in PENDING for review — same convention as the OFX / QIF / CSV importers (PR #188). Phase 7 closes.
- ✅ **P7.1.b** — CLI surface: `tulip reports <name>` (9 subcommands, `--format json|html|pdf|csv`, `--output PATH`) and `tulip journal {export,import}` over the existing endpoints (PR #190).

### Phase 8 — Operations + hardening 🔄 in progress
- ✅ **Deep security audit** — shipped 2026-05-12 as [docs/audits/2026-05-12-deep-security-audit.md](audits/2026-05-12-deep-security-audit.md). Multi-agent static review across seven streams (auth/session, authz/tenancy, crypto, input-validation, secrets/logging/deps, audit-log/ops, AI/backup); `pip-audit` clean. **0 Critical · 8 High · 25 Medium · 24 Low.** Document-only; findings tracked as follow-up issues. Explicitly *not* a pen test — that stays deferred to the pre-cloud phase (Phase 10; the audit document, dated before the Phase 9 TUI insertion, refers to it as "Phase 9").
- ✅ **Deep privacy audit** — shipped 2026-05-13 as [docs/audits/2026-05-13-deep-privacy-audit.md](audits/2026-05-13-deep-privacy-audit.md). Full-system GDPR / CCPA framing — personal-data inventory, data flows, retention/deletion, user-rights infrastructure, multi-user-within-household boundaries. **1 Critical · 17 High · 28 Medium · 22 Low.**
- ✅ **Security Wave-1 follow-ups** — the highest-severity security findings: `slowapi` rate limiting on the four `/v1/auth/*` abuse surfaces (#219), single-use MFA-challenge JWTs via the `used_mfa_challenges` table (#219), 80-bit recovery-code entropy floor (#219), constant-time login path closing the user-enumeration timing oracle, structlog email/IP redaction on by default, `AICategorizer` session-deadlock fix (#199, #200), and the proposal `actor_kind` spoofing fix.
- ✅ **Privacy Wave-1 follow-ups** — the Critical + High privacy findings: `local_only` AI profile pins to Ollama regardless of `fallback_provider` (#233); GDPR Art. 17 right-to-erasure for users (`DELETE /v1/users/{user_id}`, #235) and households (`POST /v1/households/me/erase-request` → `DELETE /v1/households/me`, #235) with attachment-ciphertext GC and audit-log PII redaction (#234).
- ✅ **Post-audit CLI + importers usability bundle** — surfaced during user testing, batched as friction-fixers: account resolution by unique name or hierarchical colon-path (#197) plus an interactive selectable-list picker when a UUID-required command runs without one (#273), `GET /v1/imports` + `tulip imports list` (#272), `tulip imports show` exposes `BATCH_ID` (#203), `--pending` / `include_pending` folds PENDING transactions into balances with clear labelling (#274), multi-account QIF import via `--account-map` (#195a) with cross-account transfer pairing (#195b) and non-transaction-section skipping (#198), QIF split-posting fidelity (#270), `tulip imports apply --posted` (#210) and `--no-categorize` (#202) for bulk migrations, Rich `Console` honouring `COLUMNS` for stable non-TTY rendering (#285), right-aligned numeric columns for financial legibility (#289), and a paper-statement (no-OFX) reconciliation flow (#275).
- ✅ **Docker compose for home server** — shipped 2026-05-10 (#134, in the pre-internal-beta hardening umbrella #121); `deploy/docker/` holds the `compose.yml`, `Dockerfile`, `entrypoint.sh`, and a deployment `README.md`.
- ✅ **Backup/restore CLI** — `tulip backup` / `tulip restore` shipped 2026-05-10 (#133); the restore-path-traversal hardening the deep security audit flagged as H-1 landed in Security Wave-1 (#217).
- 🔄 **Documentation pass** — ARCHITECTURE, THREAT_MODEL, PHASE_STATUS, README, QUICKSTART brought current through Phase 8. DEPLOYMENT, SECURITY, AI docs still pending.
- Performance pass on common queries *(pending)*

### Phase 9 — Terminal UI (TUI)

Per [ADR-0007](adrs/0007-terminal-ui.md). A [Textual](https://textual.textualize.io/)
terminal UI as an **additive client** — the CLI stays as the scriptable
/ automation surface and test-bed, the TUI is the comfortable human
review surface. Same HTTP API, no backend changes, no new attack
surface.

- New workspace package `tulip-tui` — an API client like `tulip-cli`;
  the architecture-boundary test extends to it (no server / storage
  imports).
- **v1 TUI scope is read / browse only** — navigable account browser,
  transaction register, reports, reconciliation status, import
  batches. No mutations in the first cut; editing / posting /
  reconcile actions land in later Phase 9 slices once the navigation
  shell + read surfaces are proven.
- Tested headlessly via Textual's pilot mode; a `Test (tulip-tui)`
  shard joins the per-package CI matrix (ADR-0006).
- The CLI is **not** deprecated — the README's "scriptable CLI client"
  value proposition stands.

### Phase 10 — Pre-cloud preparation (optional, before multi-tenant rollout)
- Postgres backend implementation against the existing storage abstraction
- KMS integration (master key no longer interactive)
- Worker-process separation for scheduler
- Object-storage attachment backend (S3 / MinIO)
- **Pre-cloud security re-audit** — multi-tenant blast-radius review, KMS / key-rotation, network exposure, the full tenant-isolation listener (currently deferred from Phase 1), rate limiting, and any cloud-provider-specific surface. Re-runs the Phase 8 audit against the new threat model that comes with multi-tenant + network-exposed.

### Audit cadence — quick reference

| Audit | When | Why then |
|---|---|---|
| **Lightweight threat-model checkpoint** | Between Phase 3 and Phase 4 — ✅ shipped 2026-05-01 | Captures trust boundaries, data classifications, deferred mitigations, and the constraints Phase 4–6 work must not violate. See [docs/THREAT_MODEL.md](THREAT_MODEL.md). |
| **Privacy audit (AI)** | Before Phase 6 implementation begins — ✅ shipped 2026-05-11 as [ADR-0005](adrs/0005-ai-integration.md) | Household financial data starts leaving the local boundary at AI integration; the audit shapes the design rather than reviewing it after. |
| **Deep security audit** | Phase 8 (operations + hardening) — ✅ shipped 2026-05-12 as [docs/audits/2026-05-12-deep-security-audit.md](audits/2026-05-12-deep-security-audit.md) | First point where the system has a real deployment story (Docker, backup/restore) and a stable feature set; before any real-user rollout. |
| **Deep privacy audit** | Phase 8, alongside the security audit — ✅ shipped 2026-05-13 as [docs/audits/2026-05-13-deep-privacy-audit.md](audits/2026-05-13-deep-privacy-audit.md) | Verifies the shipped state of the ADR-0005 AI privacy posture and broadens to a full-system GDPR / CCPA review before any real-user rollout. |
| **Pre-cloud security re-audit** | Phase 10, before multi-tenant cutover | Multi-tenant + network exposure is a new threat model; re-validates Phase 8 findings under the new constraints. |

---

## 11. Hand-off to Claude Code

When kicking off Claude Code, provide it with:
1. This document (`docs/ARCHITECTURE.md` in the repo, or pasted at session start).
2. A clear "start with Phase 0, here's the desired uv workspace layout" instruction.
3. The decision that **TDD is mandatory** — every change starts with a failing test.

A useful first prompt for Claude Code:

> Read `docs/ARCHITECTURE.md`. Begin Phase 0: bootstrap the uv workspace per §9, set up `tulip-core` with `Money`, `Currency`, and `Account` value objects, and write hypothesis-based property tests for `Money` arithmetic before any implementation. Use ruff + mypy --strict + pytest. Stop after Phase 0 is green in CI and propose the Phase 1 plan.

Claude Code documentation: https://docs.claude.com/en/docs/claude-code/overview

---

## 12. Open Questions Deferred

These were considered and intentionally not blocking v1. Capture as ADRs when revisited:

- **WebAuthn / passkeys** as an MFA option (in addition to TOTP).
- **Postgres backend** — schema designed for it; engineering work deferred.
- **Multi-household membership per user** — common in extended families with shared elders.
- **Receipt OCR** — scan a receipt image, extract line items, propose a transaction.
- **Mobile/web front-end clients** — out of v1 scope; same OpenAPI contract used.
- **Investment lot tracking** — basic asset accounts work, but cost basis / lot-level tracking is its own design problem.
- **Tax export packages** — Schedule C / Schedule E friendly outputs for side-hustle households.

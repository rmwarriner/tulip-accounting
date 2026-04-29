# Tulip Accounting — Architectural Specification (v1)

**Status:** Ready for handoff to Claude Code
**Document version:** 1.0
**Date:** 2026-04-29

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

allocation_pools (base for envelopes + sinking_funds)
  id (uuid pk)
  household_id (fk)
  pool_type (enum: envelope, sinking_fund) — discriminator
  name
  visibility (enum)
  current_balance (Numeric)
  currency (ISO 4217)
  is_active (bool)
  created_by_user_id, created_at, updated_at

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

### 5.2 Envelope Budgeting

- Envelopes are funded from income or from accounts (depending on user model preference — both supported).
- Spending against an envelope is achieved by including a `pool_id` reference on an expense-account posting. The accounting engine reduces the envelope balance accordingly.
- Refill happens on a schedule (typically monthly), driven by the scheduled-tx runner reading each envelope's `refill_rule`.
- Overspend is permitted but flagged on reports.

### 5.3 Refill Rules (envelope `refill_rule` JSON)

Three supported strategies:

```json
{ "strategy": "fixed", "amount": "500.00", "currency": "USD" }

{ "strategy": "percentage_of_income",
  "percentage": "10.0",
  "income_account_id": "...",
  "lookback_period": "month" }

{ "strategy": "fill_to_target",
  "target_balance": "500.00",
  "currency": "USD" }
```

### 5.4 Sinking Funds

- Each sinking fund has `target_amount` and `target_date`.
- Recommended monthly contribution = `(target_amount - current_balance) / months_until_target`. Reported live; not auto-applied unless `contribution_strategy` says so.
- Spending from a sinking fund follows the same `pool_id`-on-posting mechanic as envelopes.
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

1. **Manual flag.** Users can mark transactions as `cleared` (matched against their own records) and `reconciled` (matched against an actual statement). A reconciliation summary report shows uncleared/unreconciled.
2. **Statement-driven matching.** User imports a statement (OFX or CSV) for an account. The reconciliation matcher compares statement entries against existing transactions and creates a `reconciliation` record. Matching algorithm: amount + date (±3 days configurable) + fuzzy description match. Unmatched statement lines become `pending` transactions awaiting categorization. Unmatched ledger transactions are flagged for review.

### 5.9 Importers (v1)

| Format | Parser library | Notes |
|---|---|---|
| OFX | `ofxparse` (tested baseline) — verify maintenance status at implementation time | Most banks/credit cards |
| QIF | Custom parser (format is small, public domain) | Older Quicken format |
| CSV | Custom parser with column-mapping config | Per-institution mapping profiles savable as YAML |

Each importer:
- Reads the source file → produces a `proposed_import` (in-memory, with proposed transactions and postings).
- Runs the auto-categorization AI capability if enabled (or rule-based fallback).
- Presents the proposed batch to the user for review.
- On user `apply`, posts transactions atomically and records an `import_batch`.
- Original file is stored as an attachment for audit trail.

### 5.10 Journal Format Export/Import (Level 3)

- Export: `tulip export journal --account=<code> --from=<date> --to=<date> > out.journal`. Output is hledger-compatible. Lossy fields (envelope linkage, audit metadata, attachment refs) are emitted as comments where standard format allows.
- Import: `tulip import journal in.journal`. v1 supports postings + standard directives (account, commodity, P, ~). Envelope/sinking-fund metadata in comments is parsed if present (Tulip-specific extension); standard journals import as plain transactions.

---

## 6. AI Integration

### 6.1 Adapter Layer

All AI calls go through `tulip.ai.adapters`. Per-tenant configuration selects a provider; `litellm` provides a uniform call surface. Supported providers in v1:

- Anthropic (Claude family)
- OpenAI (GPT family)
- Google (Gemini family)
- Ollama (local models)
- Generic OpenAI-compatible endpoint (LM Studio, vLLM, etc.)

API keys are stored encrypted (field-level) per household. Per-user keys are also supported (override household keys).

### 6.2 Capabilities (all four, first-class)

1. **Auto-categorization.** Classifier sees payee + amount + date + (optional) memo. Returns suggested account code and (optional) envelope. User reviews on import. Fallback: rule-based regex matcher tracked per household (also useful for offline/local-only mode).
2. **Natural-language query.** User asks a question; system constructs a SQL query against a read-only view of their data; AI summarizes results. Query is logged; raw rows are returned to the user alongside the summary so they can verify.
3. **Forecasting & anomaly detection.** Periodic job (daily) scans for anomalies (spending >2σ above rolling mean per envelope) and generates forecasts (envelope-runout dates, sinking-fund-on-track flags). Results are stored as `notifications`, not auto-acted-upon.
4. **Agentic workflows.** AI may *propose* journal entries, restated budgets, or sinking-fund plans. Proposals are stored as `pending_proposals` and require explicit user approval. Once approved, they execute through the same accounting engine path as user-initiated actions, with the audit log noting `actor_kind=ai_agent` and the originating proposal id.

### 6.3 Privacy Posture

Three policies per capability, applied at tenant level (admin sets):

- `permissive` (default for v1) — capability runs against the configured cloud provider without per-action approval.
- `requires_approval` — every invocation pauses for explicit user approval before sending data off-host.
- `disabled` — capability is unavailable.
- Additionally, each capability can be configured to use a `local_only` provider (Ollama), bypassing the off-host question entirely.

Users can ratchet their own restriction *up* (more cautious than tenant policy) but not *down*.

### 6.4 Audit & Cost Controls

- Every AI invocation produces an `ai_invocations` row with provider, model, token counts, cost estimate, and outcome.
- Per-household monthly cost cap (default $10/mo, configurable). Cap reached → capability degrades to local-only or disabled until reset.
- Per-user rate limit (default 60 invocations/hour) to limit blast radius of a runaway loop or compromised credential.

### 6.5 Tenant AI Policy (`households.ai_policy` JSON shape)

```json
{
  "default_provider": "anthropic",
  "default_model": "claude-opus-4-7",
  "fallback_provider": "ollama",
  "fallback_model": "llama3.1:70b",
  "monthly_cost_cap_usd": "10.00",
  "capabilities": {
    "categorization":   { "policy": "permissive",        "provider": null, "model": null },
    "nl_query":         { "policy": "permissive",        "provider": null, "model": null },
    "forecasting":      { "policy": "permissive",        "provider": null, "model": null },
    "agentic":          { "policy": "requires_approval", "provider": null, "model": null }
  }
}
```

`provider`/`model` null means "use household default." Per-capability override allows e.g. agentic to always use a local model.

---

## 7. Cross-Cutting Concerns

### 7.1 Authentication

- **Password hashing:** argon2id (via `argon2-cffi`), with sensible memory/time parameters reviewed annually.
- **Session model for first-party clients (CLI):** API issues a JWT access token (15 min) + opaque refresh token (30 days, rotating, stored hashed in DB). CLI persists tokens in OS keyring (via `keyring` library) — never plaintext on disk.
- **API tokens for non-interactive clients:** scoped, revocable, optionally with expiry. Stored hashed.
- **MFA:** TOTP (RFC 6238) via `pyotp`. Recovery codes generated at enrollment, hashed at rest. MFA challenge required at login when enabled. Default tenant policy: required for admins, optional for members.

### 7.2 Logging & Observability

**Structured JSON logging via structlog.**

- Each request gets a `request_id` (UUID) attached to the structlog context. The request_id propagates through every log line and into the `audit_log` and `ai_invocations` rows for the duration of the request.
- Standard fields on every log line: `timestamp`, `level`, `event`, `request_id`, `household_id` (when in tenant scope), `user_id` (when authenticated), `module`.
- Default sink: stdout (works directly with Docker, journald, syslog, or direct file capture). Configurable to file with rotation (`logging.handlers.RotatingFileHandler`).
- **PII redaction:** structlog processor redacts known-sensitive fields (account numbers, password fields, TOTP secrets, API keys) before serialization. Redaction is whitelist-based on field names; unknown fields are emitted as-is. A test enforces redaction on every known-sensitive field.
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
  - Auth endpoints: aggressive limits to deter brute force.
  - AI endpoints: per-user (60/hour default) and per-household ($10/month default).
  - Bulk endpoints (import, export): limits scaled by payload size.

### 7.7 Notifications

- Pluggable `Notifier` interface.
- v1 implementations: `ConsoleNotifier`, `EmailNotifier` (SMTP).
- Triggers: scheduled tx requires approval, AI invocation awaiting approval, anomaly detected, period close completed, backup failed.
- Future: webhook-based (`WebhookNotifier`) for Slack/Discord/etc.

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
    └── ADRs/                  # Architecture Decision Records
```

**Module boundary rules (enforced by architecture tests):**
- `tulip-core` may not import any I/O package (no `tulip-storage`, no `tulip-api`, no third-party DB/HTTP libs).
- `tulip-storage` may import `tulip-core`. The reverse is forbidden.
- `tulip-api` orchestrates `tulip-core`, `tulip-storage`, `tulip-ai`. It is the only layer that knows about HTTP.
- `tulip-cli` talks to `tulip-api` over HTTP. It does not import `tulip-storage` or `tulip-core` directly. (This is the same contract a future web client will follow.)
- `tulip-importers` and `tulip-reports` are CLI tools / libraries that call the API.

---

## 10. Development Phases

The roadmap below is suggested; Claude Code can sequence within each phase as the work clarifies.

### Phase 0 — Project bootstrap (1–2 sessions)
- uv workspace skeleton; CI pipeline (lint, type-check, test, coverage gate)
- `tulip-core` skeleton with `Money`, `Currency`, `Account` value objects
- Property-based tests for `Money` arithmetic (the foundation invariant)
- Pre-commit hooks (ruff, mypy, secrets-detection)

### Phase 1 — Storage + accounting engine
- SQLAlchemy models for households, users, accounts, transactions, postings
- SQLCipher wiring with master-key passphrase prompt
- Field-level encryption helpers
- Alembic migration #1: initial schema
- Accounting engine: post transaction, balanced-postings invariant, period-aware writes
- Property-based tests over the engine

### Phase 2 — API surface (auth + accounts + transactions)
- FastAPI app with structured logging and request_id middleware
- Auth endpoints (register, login, MFA enrollment, refresh)
- CRUD for accounts and transactions with permission enforcement
- OpenAPI spec rendered + schemathesis tests in CI
- Audit log writer wired into every mutation

### Phase 3 — CLI (Typer) + first useful flows
- `tulip auth login`, `tulip add`, `tulip register`, `tulip balance`, `tulip accounts`
- Token storage via keyring
- End-to-end tests of CLI against a spawned API server
- Toner-friendly print stylesheet finalized

### Phase 4 — Envelopes + sinking funds
- Allocation pool models and CRUD
- Refill rules implementation
- Scheduled-tx runner (in-process)
- Reports: envelope status, sinking-fund progress

### Phase 5 — Importers + reconciliation
- OFX, QIF, CSV importers
- Statement-driven reconciliation matcher
- Manual cleared/reconciled flow
- Import batches and rollback

### Phase 6 — AI integration
- `tulip-ai` package with litellm adapter
- Capabilities: categorization (used in importers) → NL query → forecasting → agentic
- Tenant + user policy resolution
- Cost cap and rate limiting
- AI invocation audit log

### Phase 7 — Reports + journal export/import
- All v1 reports rendered as HTML and PDF (weasyprint)
- Journal export (hledger-compatible)
- Basic journal import

### Phase 8 — Operations + hardening
- Docker compose for home server
- Backup/restore CLI commands
- Documentation pass: ARCHITECTURE, DEPLOYMENT, SECURITY, AI
- Threat model review
- Performance pass on common queries

### Phase 9 — Pre-cloud preparation (optional, before multi-tenant rollout)
- Postgres backend implementation against the existing storage abstraction
- KMS integration (master key no longer interactive)
- Worker-process separation for scheduler
- Object-storage attachment backend (S3 / MinIO)

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

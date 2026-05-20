# Tulip Accounting

[![CI](https://github.com/rmwarriner/tulip-accounting/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/rmwarriner/tulip-accounting/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](pyproject.toml)

Household-focused, double-entry accounting system with first-class envelope budgeting, sinking funds, and statement-driven reconciliation. Self-hosted, encrypted at rest, scriptable from the CLI.

> **First time here?** Jump to [docs/QUICKSTART.md](docs/QUICKSTART.md) — a 20-minute, copy-paste path from empty machine to imported + reconciled + backed-up statement.

---

## For users

Tulip is a personal-accounting stack you run on your own machine. The current target is **internal beta** — a small group of trusted, technical self-hosters; not a hosted service, not a SaaS, not yet built for non-technical users.

### What it does for you

- **Double-entry ledger.** Every transaction has at least two postings that sum to zero; balances are derived, not stored, and the storage layer rejects unbalanced writes. You can't accidentally make the books not balance.
- **Envelope budgeting + sinking funds.** First-class features, not tags on transactions. Envelopes for monthly cash flow (groceries, rent), sinking funds for goal-based savings (vacation, emergency fund). Scheduled refills land automatically on the dates you pick.
- **Statement-driven reconciliation.** Import OFX, QIF, or CSV statements from your bank; the auto-matcher pairs each line with the right ledger transaction; carry-forward handles unmatched items into the next cycle. The 4-section review pane is the same flow your accountant would run, just faster.

### Install in 60 seconds

You need Docker + Compose v2 and `git`. Everything else (the Python toolchain, the API, the CLI) lives in the container.

```bash
git clone https://github.com/rmwarriner/tulip-accounting.git
cd tulip-accounting

mkdir -p deploy/docker/secrets
python3 -c 'import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())' \
  > deploy/docker/secrets/master-key
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' \
  > deploy/docker/secrets/jwt-secret
chmod 0400 deploy/docker/secrets/*

docker compose -f deploy/docker/compose.yml up --build --wait
```

When the command returns, the API is live on `http://127.0.0.1:8000`. The full walkthrough — register, seed accounts, import a statement, reconcile, close the month, back it up — is in [docs/QUICKSTART.md](docs/QUICKSTART.md).

> **Before you log five years of data into it:** read [docs/RECOVERY.md](docs/RECOVERY.md). It covers what to store outside the host so a successor can recover your books if you're unavailable, and the annual dry-run cadence. Bus-factor of one is the biggest practical risk for a household-scope tool.

### What's not in v1 yet

Honest expectations matter for internal-beta. The following are deliberately deferred:

- **Web / mobile UI.** Tulip is API-first with two clients today: the Typer CLI (the scriptable / automation surface) and `tulip-tui`, a Textual terminal UI for interactive browsing *and* the common write flows — apply imports inline, reconcile actioning, add/edit/void transactions — as of P9.6. A web / mobile client lands as a separate phase.
- **Multi-tenant cloud hosting.** Tulip is single-machine, single-tenant SQLite for internal beta. Postgres + KMS + multi-tenant scaling is a future phase.
- **Reverse-proxy / TLS tutorial.** Run behind Caddy or Tailscale Funnel; we don't ship a TLS setup guide.
- **Full-DB encryption at rest (SQLCipher).** Field-level AES-256-GCM protects the most sensitive columns + attachments today; whole-database SQLCipher is a Phase 8 hardening item still in flight.

**Opt-in AI features** (auto-categorisation, NL queries, forecasts, agentic proposals) are now wired and shipped — disabled by default; bring your own provider key (Anthropic / OpenAI / local Ollama) via `tulip ai set-key` to enable. Per-household policy, per-user rate limits, monthly cost cap, and a `tulip ai status` summary all surface from the CLI. See the AI cookbook in [docs/QUICKSTART.md](docs/QUICKSTART.md) for the enablement walkthrough.

The full deferred-features list and the ordered roadmap is the contributor-side concern; see *For contributors* below if you want it.

---

## For contributors

> **Status:** Internal beta. Phases 0–7 complete (project bootstrap, storage + accounting engine, API surface, scriptable CLI, envelopes + sinking funds + scheduled refills, OFX/QIF/CSV importers + statement-driven reconciliation, opt-in AI integration with BYOK provider keys + cost-cap + rate limit + audited proposals, nine reports in HTML/PDF/CSV + hledger journal export/import + `tulip reports` and `tulip journal` CLI). **Phase 8 (operations + hardening) is in progress:** the deep security and deep privacy audits have shipped ([docs/audits/](docs/audits/)), along with their highest-severity Wave-1 follow-ups (auth rate limiting, single-use MFA-challenge tokens, GDPR right-to-erasure) and a post-audit CLI/importers usability bundle. **Phase 9 (terminal UI) v1 + P9.6 mutation wave shipped** — a Textual TUI (`tulip-tui`) as an additive client alongside the CLI: read-only browsers (P9.0–P9.5) plus the daily-driver write flows (P9.6 — apply imports inline, reconcile actioning, add/edit/void transactions). The CLI remains the scripting surface per [ADR-0007](docs/adrs/0007-terminal-ui.md). See [docs/PHASE_STATUS.md](docs/PHASE_STATUS.md) for the full picture and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

### Architecture at a glance

Python 3.14 + FastAPI + SQLAlchemy 2.0 + Pydantic v2, as a `uv` workspace monorepo of eight packages, with TDD-mandatory development discipline. Full details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

```
tulip-accounting/
├── packages/
│   ├── tulip-core/         # pure domain logic; no I/O, no framework deps
│   ├── tulip-storage/      # storage abstraction + SQLite/SQLCipher impl
│   ├── tulip-api/          # FastAPI server
│   ├── tulip-ai/           # AI adapter layer (litellm)
│   ├── tulip-importers/    # OFX, QIF, CSV, journal-format
│   ├── tulip-reports/      # toner-friendly PDF, HTML, CSV
│   ├── tulip-cli/          # Typer-based CLI client
│   └── tulip-tui/          # Textual terminal UI client (read-only; reuses tulip-cli HTTP layer)
├── deploy/                 # Docker, systemd, deployment scripts
└── docs/                   # ARCHITECTURE, QUICKSTART, THREAT_MODEL, ADRs
```

### Dev environment

Prerequisites:

- **Python 3.14 or newer.**
- **[uv](https://docs.astral.sh/uv/)** — fast Python package and workspace manager (replaces pip + venv + pip-tools).
- **SQLCipher** development headers — *only required once full-DB SQLCipher encryption lands (post-Phase 5).* Field-level AES-256-GCM (the layer that protects TOTP secrets, attachments) uses the pure-Python `cryptography` library and needs no native deps.

```bash
git clone https://github.com/rmwarriner/tulip-accounting.git
cd tulip-accounting
uv sync --all-packages --dev     # installs all workspace packages + dev deps
uv run pre-commit install        # enable pre-commit hooks
just test                        # confirm tests pass (~1830 tests, ~4 min with xdist)
```

> **Note for committers:** `main` is branch-protected and **requires every commit to be signed**. Configure SSH or GPG commit signing before your first push (`git config --global commit.gpgsign true` plus a signing key); see [CONTRIBUTING.md](CONTRIBUTING.md#branch-protection-on-main) for the diagnostic checklist when a push is rejected as unsigned.

### Common dev commands

The [`justfile`](justfile) wraps the standard loops; `just --list` shows everything available.

```bash
just test                                    # full test suite, parallel
just lint                                    # ruff check
just typecheck                               # mypy --strict
just coverage                                # coverage report (gate is 85% project, 90% tulip-core)
just bench                                   # pytest-benchmark (sequential — incompatible with xdist)
just ci                                      # everything CI runs, locally
just quickstart-smoke                        # replay docs/QUICKSTART.md end-to-end (#138)
uv run pytest packages/tulip-core            # tests for a single package
uv run pytest -m property                    # only property-based (hypothesis) tests
uv run pytest -m integration                 # only integration tests (CLI subprocess + live API)
```

### Running the API locally

For development against a local SQLite file (the production deployment is the docker compose stack documented in QUICKSTART):

```bash
# Migrate.
TULIP_DATABASE_URL=sqlite:///./tulip.db \
  uv run alembic -c packages/tulip-storage/alembic.ini upgrade head

# Run.
TULIP_DATABASE_URL=sqlite:///./tulip.db \
TULIP_JWT_SECRET="$(uv run python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  uv run uvicorn tulip_api.main:create_app --factory --host 127.0.0.1 --port 8000
```

Then `curl http://127.0.0.1:8000/health` for a smoke check, or `curl http://127.0.0.1:8000/openapi.json` for the OpenAPI spec. Endpoint surface (Phases 0–8):

- **Auth:** `POST /v1/auth/{register,login,login/mfa,login/recover,refresh,logout}`, `POST /v1/auth/mfa/{enroll,verify,recovery-codes/regenerate}`, `GET /v1/auth/mfa/recovery-codes/status`. The four abuse-exposed endpoints (`login`, `login/mfa`, `login/recover`, `refresh`) are behind per-IP `slowapi` quotas — `auth.rate_limited` (429) on exceedance.
- **Users + households (right-to-erasure, GDPR Art. 17):** `DELETE /v1/users/{user_id}` (cascades the user's sessions + recovery codes, redacts their audit-log PII), `POST /v1/households/me/erase-request` → `DELETE /v1/households/me` (two-step token-gated household erasure with attachment-ciphertext GC)
- **Accounts + transactions:** `GET/POST/PATCH/DELETE /v1/accounts[/{id}]`, `GET /v1/accounts/{id}/balance[?include_pending=&as_of=]`, `GET/POST/PATCH/DELETE /v1/transactions[/{id}]`, `POST /v1/transactions/{id}/void`
- **Periods:** `GET /v1/periods`, `POST /v1/periods/{id}/{close,reopen}`
- **Envelopes + sinking funds + pools:** `GET/POST/PATCH/DELETE /v1/envelopes[/{id}]`, `GET/POST/PATCH/DELETE /v1/sinking-funds[/{id}]`, `GET /v1/pools/{id}/balance`, `POST /v1/pools/{id}/{refill,transfer,budget-inflow}`, `POST /v1/pools/balances` (batched), `GET/POST/PATCH/DELETE /v1/refill-schedules[/{id}]`
- **Importers + reconciliation:** `POST /v1/imports[?force=true]`, `POST /v1/imports/multi-account` (whole-file multi-account QIF + `account_map`), `GET /v1/imports` (list, filterable by `status` / `account_id`), `GET /v1/imports/{id}`, `POST /v1/imports/{id}/{apply,lines/{line_id}/promote}`, `GET/POST/PATCH/DELETE /v1/imports/profiles[/{id_or_name}]` (CSV column-mapping profiles, YAML round-trip), `GET/POST/DELETE /v1/reconciliations[/{id}][?cascade=true]`, `POST /v1/reconciliations/{id}/{auto-match,complete,matches,carry-forward}`, `POST /v1/reconciliations/{id}/matches/{id}/reject`, `DELETE /v1/reconciliations/{id}/carry-forward/{tx_id}`
- **AI (Phase 6, BYOK, admin-only configuration):** `POST/DELETE /v1/ai/keys/{provider}`, `GET /v1/ai/keys`, `GET/PUT /v1/ai/config`, `PUT /v1/ai/config/capabilities/{capability}`, `GET /v1/ai/status`, `POST /v1/ai/preview`, `POST /v1/ai/ask` (two-turn NL query), `GET/POST /v1/ai/proposals[?status=...]`, `GET /v1/ai/proposals/kinds`, `POST /v1/ai/proposals/{id}/{approve,reject}`, `POST /v1/ai/proposals/suggest/budget`
- **Reports (Phase 7, JSON/HTML/PDF/CSV via `?format=`):** `GET /v1/reports/{trial-balance,balance-sheet,income-statement,cash-flow,envelope-status,sinking-fund-progress,reconciliation-summary,audit-log,custom-query}`
- **Journal (Phase 7, hledger-format round-trip):** `GET /v1/journal/export[?start=...&end=...]`, `POST /v1/journal/import` (text/plain body → PENDING transactions)
- **Notifications:** `GET /v1/notifications[?status=...]`, `POST /v1/notifications/{id}/dismiss`
- **Ops:** `GET /health`, `GET /v1/system/diagnostics` (consumed by `tulip doctor`)

Every non-2xx response is `application/problem+json` per RFC 9457. In production, supply `TULIP_JWT_SECRET` and the master key from a secret store rather than generating fresh on every start (existing tokens and field-encrypted columns won't validate after a restart with new secrets). The master key can come from one of two sources, in this order of precedence:

1. **`TULIP_MASTER_KEY`** — base64-encoded 32 bytes, inline in the environment. Convenient for `docker run -e ...` and CI.
2. **`TULIP_KEY_FILE`** — path to a file containing the base64-encoded 32 bytes. The file **must** be `chmod 0600` (owner-only RW); any group or other access bit refuses boot with a typed error. This is the recommended path for self-hosted internal-beta deploys via Docker secrets (#132).

If neither is set, an ephemeral key is generated and a warning is logged — fine for tests and dev, fatal for production. `tulip doctor` flags ephemeral keys as a hard failure.

### Running the CLI

A short tour through Phase 5's end-to-end loop (register → import → reconcile). The full walkthrough is in [docs/QUICKSTART.md](docs/QUICKSTART.md):

```bash
# Set up.
uv run tulip register --email me@example.com --display-name Me --household Mine
uv run tulip auth login --email me@example.com
uv run tulip accounts add --name Checking --type asset --currency USD --code 1110
uv run tulip accounts add --name Food --type expense --currency USD --code 5100

# Or, hledger / Quicken style — colon-path in --name, no codes:
uv run tulip accounts add --name "Assets:Current Assets:Checking" --type asset --currency USD --create-parents
uv run tulip accounts add --name "Expenses:Groceries" --type expense --currency USD --create-parents

# A manual transaction.
uv run tulip add --date 2026-05-12 --description 'Grocery store' \
  --post 5100=87.42 \
  --post 1110=-87.42
uv run tulip balance

# Importer + reconciliation flow.
BATCH_ID=$(uv run tulip --json import ofx ./statement.ofx --account 1110 | jq -r .id)
uv run tulip imports apply "$BATCH_ID"            # promotes lines to PENDING ledger txs
RECON_ID=$(uv run tulip --json reconcile create \
  --account 1110 --batch "$BATCH_ID" \
  --period 2026-05-01..2026-05-31 \
  --starting 0.00 --ending 1234.56 | jq -r .id)
uv run tulip reconcile auto-match "$RECON_ID"     # bucketed-confidence matcher (P5.3)
uv run tulip reconcile show "$RECON_ID"           # 4-section review pane
uv run tulip reconcile complete "$RECON_ID"       # strict balance check, denormalises reconciled_at
```

`tulip add --edit` opens `$EDITOR` with a hledger-style template instead of taking flags. `tulip accounts list` renders a Rich tree when nesting exists, a flat table otherwise (`--flat` to force the table for scripting). Tokens persist in the OS keyring; the CLI exit-code map and full RFC 9457 error rendering are documented in [docs/ARCHITECTURE.md §7.8.5](docs/ARCHITECTURE.md).

Other top-level commands: `tulip envelopes`, `tulip sinking-funds`, `tulip refills`, `tulip periods`, `tulip transfer`, `tulip refill`, `tulip budget-inflow`, `tulip transactions {show,edit,void,delete}`, `tulip imports {ofx,csv,qif,list,show,apply,profiles}`, `tulip reconcile {create,start,list,show,auto-match,match,walk,interactive,complete,carry-forward,reject,delete}`, `tulip notifications {list,dismiss}`, `tulip backup`, `tulip restore`, `tulip doctor`. Where a command needs a UUID it also accepts an account code / name / hierarchical path, and an interactive selectable-list picker is offered when the argument is omitted. Phase 8 usability touches: `tulip imports qif --account-map <map.json>` imports a multi-account Banktivity-style QIF in one POST (run `--account` on a multi-account file to get a starter map); `tulip imports apply --posted` lands a bulk historical migration straight to POSTED; `tulip balance --pending` folds PENDING transactions into the figure (clearly labelled, default `--no-pending`); `tulip reconcile start` opens a paper-statement reconciliation with no import batch. The `tulip ai` group (Phase 6) covers BYOK + policy editing + the four capabilities: `tulip ai {set-key, forget-key, list-keys, status, preview, config {show,set,clear,set-capability,log-prompts}, ask, propose, proposals, approve, reject, suggest-budget}`. The `tulip reports` group (Phase 7) has one subcommand per report (`trial-balance`, `balance-sheet`, `income-statement`, `cash-flow`, `envelope-status`, `sinking-fund-progress`, `reconciliation-summary`, `audit-log`, `custom-query`); each accepts `--format json|html|pdf|csv` (default json, JSON/HTML default to stdout, PDF/CSV need `--output PATH`). `tulip journal {export,import}` round-trips the household ledger as hledger-format text. Each takes `--help`; the surface mirrors the API endpoints listed above.

### Development discipline

This project follows test-driven development. Every feature ships with tests written **before** the implementation (red → green → refactor). PRs that don't include tests for new code paths are rejected by CI policy.

- **Coverage floor:** 85% (target 90% on shipping packages, ≥90% on `tulip-core`)
- **Property-based tests** required for all `tulip-core` invariants (using [hypothesis](https://hypothesis.readthedocs.io/))
- **Module boundary rules** are enforced by architecture tests — `tulip-core` is pure domain code and must not import any I/O package; see [docs/ARCHITECTURE.md §9](docs/ARCHITECTURE.md)
- **No `float` ever touches money** — `decimal.Decimal` only, with a `Money` value object that pairs amount with currency
- **Audit log** writes accompany every mutation; the discipline of "if it changes data, it's audited" is enforced in code review

### Documentation

- [QUICKSTART](docs/QUICKSTART.md) — runnable end-to-end walkthrough (the user entry point; pin this for first-time setup questions)
- [Architecture](docs/ARCHITECTURE.md) — full system design, data model, phase roadmap, error-handling standard (§7.8)
- [Phase Status](docs/PHASE_STATUS.md) — what's shipped, what's queued
- [Threat Model](docs/THREAT_MODEL.md) — security checkpoint, kept current with the Phase 8 audits
- [User Rights](docs/USER_RIGHTS.md) — operator-facing map from GDPR / CCPA data-subject rights to the Tulip commands that honour them
- [Recovery](docs/RECOVERY.md) — bus-factor / successor recovery procedure: what the Recovery Packet contains, how to restore from a destroyed host, the annual dry-run cadence
- [Audits](docs/audits/) — the Phase 8 deep security audit (2026-05-12) and deep privacy audit (2026-05-13), finding-by-finding
- [ADRs](docs/adrs/) — architectural decision records (envelope shadow ledger, scheduler primitive, mutation testing, reconciliation, AI integration / privacy contract)
- [CONTRIBUTING.md](CONTRIBUTING.md) — TDD discipline, coverage gates, signed commits, manual smoke test format
- [CLAUDE.md](CLAUDE.md) — operational notes for AI-assisted development on this repo
- The OpenAPI spec at `/openapi.json` is the live contract for `tulip-api` until `docs/API.md` exists.

### Security & privacy

- **Defense-in-depth encryption at rest:** SQLCipher for the whole DB (Phase 8, in flight), AES-256-GCM field-level for the most sensitive columns (TOTP secrets, attachments) today. No single key compromise leaks everything.
- **Master key** is sourced from `TULIP_MASTER_KEY` (env var) or `TULIP_KEY_FILE` (0600 file); never written to disk by Tulip itself.
- **MFA (TOTP)** required for admin users by default; optional for household members. TOTP secrets are encrypted at rest; recovery codes are argon2id-hashed with an 80-bit entropy floor; the MFA-login flow uses a single-use challenge token that can't be replayed.
- **Online-auth hardening (Phase 8 Wave-1):** per-IP rate limiting on the four `/v1/auth/*` abuse surfaces, a constant-time login path that closes the user-enumeration timing oracle, and email/IP redaction in structured logs by default.
- **Right to erasure (GDPR Art. 17 / CCPA):** `DELETE /v1/users/{id}` and the two-step household-erasure flow cascade-delete the subject's data, garbage-collect attachment ciphertext from disk, and redact PII from audit-log snapshots. See [docs/USER_RIGHTS.md](docs/USER_RIGHTS.md) for the full subject-rights map (access, rectification, erasure, restriction, portability, objection, consent withdrawal).
- **AI privacy posture** is per-tenant + per-user policy, with audit logging of every model invocation, a server-enforced monthly cost cap, and a per-user sliding-window rate limit. Defaults to permissive but a fresh household has no provider key — no provider is contacted until an admin opts in via `tulip ai set-key`. Users can dial up restrictions (per-capability `disabled` / `requires_approval`, `strict` / `local_only` redaction profiles) or switch to local-only models (Ollama). Prompt bodies are never stored by default; only metadata (model, latency, cost, success/fail) lands in `ai_invocations`. Full forensic prompt logging is opt-in via `tulip ai config log-prompts on`.

The threat model is in [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md); the Phase 8 deep security and deep privacy audits ([docs/audits/](docs/audits/)) are document-only multi-agent reviews — their highest-severity findings have shipped as Wave-1 follow-ups, with the remainder tracked as issues.

### License

This project is licensed under the **GNU Affero General Public License v3.0 or later** (AGPL-3.0-or-later). See [LICENSE](LICENSE) for the full text.

The AGPL is a strong copyleft license. In practical terms:

- You can use, modify, and redistribute this software freely.
- If you distribute modified versions, you must release your modifications under AGPL-3.0-or-later as well.
- **Importantly:** if you run a modified version as a network service (e.g., host it as a SaaS), you must offer the source of your modified version to the users of that service. This is the AGPL's network clause (§13).

This license was chosen as a forward-looking hedge: it preserves the option for the project owner to offer a hosted version commercially in the future without others undercutting that with closed-source forks. For purely self-hosted use (the v1 target), AGPL is no different from GPL or any other copyleft license — your local install is yours.

#### Source file headers

Each source file should start with an SPDX-style header:

```python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 <Your Name>
```

This is recognized by `licensee`, [REUSE](https://reuse.software/), and other compliance tools, and is far more compact than the full GNU recommended header (which is also acceptable; see the bottom of the `LICENSE` file).

### Project name

"Tulip" is a working name. The 17th-century Dutch tulip mania is, on reflection, an unfortunate naming reference for a financial-discipline tool — it may get renamed before v1.0.

---

**New here?** [docs/QUICKSTART.md](docs/QUICKSTART.md) is the place to start.

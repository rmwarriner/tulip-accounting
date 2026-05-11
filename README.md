# Tulip Accounting

Household-focused, double-entry accounting system with first-class envelope budgeting and sinking-fund support.

> **First time here?** Start with [docs/QUICKSTART.md](docs/QUICKSTART.md) — a 20-minute, copy-paste path from empty machine to imported + reconciled + backed-up statement.

> **Status:** Pre-alpha — Phases 0–5 complete (project bootstrap, storage + accounting engine, API surface, scriptable CLI, envelopes + sinking funds + scheduled refills, OFX/QIF/CSV importers + statement-driven reconciliation). Phase 6 (AI integration) is the next phase; pre-internal-beta hardening (#121) is in scope before then. See [docs/PHASE_STATUS.md](docs/PHASE_STATUS.md) for the full picture, or [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

---

## What is Tulip?

Tulip is a self-hosted accounting system designed for households, not businesses. It provides:

- **Double-entry accounting** with strict balance invariants enforced at the storage layer
- **Envelope budgeting** as a first-class feature, not a tag on transactions
- **Sinking funds** for goal-based savings, modeled distinctly from envelopes
- **Multi-user** support within a household, with shared and private accounts
- **Pluggable AI** across major providers (Anthropic, OpenAI, Google, local via Ollama) — fully optional; the system works with all AI features disabled
- **Encrypted at rest** with defense-in-depth (full-DB encryption + field-level + per-attachment)
- **Plain-text portability** via hledger-format import/export

v1 ships an API server (Python + FastAPI) and a scriptable CLI client (Typer). Web and mobile clients are planned for a later release; they will consume the same OpenAPI contract.

For a runnable end-to-end walkthrough (install → register → import → reconcile → close → backup), see [docs/QUICKSTART.md](docs/QUICKSTART.md).

## Architecture at a glance

Python 3.12 + FastAPI + SQLAlchemy 2.0 + Pydantic v2 + SQLCipher, as a `uv` workspace monorepo of seven packages, with TDD-mandatory development discipline. Full details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

```
tulip-accounting/
├── packages/
│   ├── tulip-core/         # pure domain logic; no I/O, no framework deps
│   ├── tulip-storage/      # storage abstraction + SQLite/SQLCipher impl
│   ├── tulip-api/          # FastAPI server
│   ├── tulip-ai/           # AI adapter layer (litellm)
│   ├── tulip-importers/    # OFX, QIF, CSV, journal-format
│   ├── tulip-reports/      # toner-friendly PDF, HTML, CSV
│   └── tulip-cli/          # Typer-based CLI client
├── deploy/                 # Docker, systemd, deployment scripts
└── docs/                   # ARCHITECTURE, DATA_MODEL, SECURITY, AI, ADRs
```

## Quick start for developers

### Prerequisites

- **Python 3.12 or newer**
- **[uv](https://docs.astral.sh/uv/)** — fast Python package and workspace manager (replaces pip + venv + pip-tools workflows; if you're returning to Python from an earlier era, this is the modern toolchain).
- **SQLCipher** development headers — *only required once full-DB SQLCipher encryption lands (Phase 1.x)*. Field-level AES-256-GCM (the layer that protects account numbers, TOTP secrets, etc.) uses the pure-Python `cryptography` library and needs no native deps.
  - Debian/Ubuntu: `sudo apt install libsqlcipher-dev sqlcipher`
  - macOS (Homebrew): `brew install sqlcipher`
  - Other platforms: see https://www.zetetic.net/sqlcipher/

### Setup

```bash
git clone https://github.com/<your-org>/tulip-accounting
cd tulip-accounting
uv sync --all-packages --dev     # installs all workspace packages + dev deps
uv run pre-commit install        # enable pre-commit hooks
just test                        # confirm tests pass (~1130 tests, ~4–5 min with xdist on 4 workers)
```

> **Note for committers:** `main` is branch-protected and **requires every commit to be signed**. Configure SSH or GPG commit signing before your first push (`git config --global commit.gpgsign true` plus a signing key); see [CONTRIBUTING.md](CONTRIBUTING.md#branch-protection-on-main) for the details, the most common failure modes, and the diagnostic checklist if a push is rejected as unsigned.

### Initialize a database

```bash
# From the repo root, against a local SQLite file:
TULIP_DATABASE_URL=sqlite:///./tulip.db \
  uv run alembic -c packages/tulip-storage/alembic.ini upgrade head
```

### Common commands

The [`justfile`](justfile) wraps the standard loops; see `just --list` for the full surface.

```bash
just test                                    # full test suite, parallel (~1130 tests)
just lint                                    # ruff check
just typecheck                               # mypy --strict
just coverage                                # coverage report (gate is 85% project, 90% tulip-core)
just bench                                   # pytest-benchmark (sequential — incompatible with xdist)
just ci                                      # everything CI runs, locally
uv run pytest packages/tulip-core            # tests for a single package
uv run pytest -m property                    # only property-based (hypothesis) tests
uv run pytest -m integration                 # only integration tests (CLI subprocess + live API)
```

### Running the API server

```bash
TULIP_DATABASE_URL=sqlite:///./tulip.db \
TULIP_JWT_SECRET="$(uv run python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  uv run uvicorn tulip_api.main:create_app --factory --host 127.0.0.1 --port 8000
```

Then `curl http://127.0.0.1:8000/health` for a smoke check, or `curl http://127.0.0.1:8000/openapi.json` for the OpenAPI spec. Endpoint surface (Phases 0–5):

- **Auth (Phase 2 / 2.x):** `POST /v1/auth/{register,login,login/mfa,login/recover,refresh,logout}`, `POST /v1/auth/mfa/{enroll,verify,recovery-codes/regenerate}`, `GET /v1/auth/mfa/recovery-codes/status`
- **Accounts + transactions (Phase 2):** `GET/POST/PATCH/DELETE /v1/accounts[/{id}]`, `GET /v1/accounts/{id}/balance`, `GET/POST/PATCH/DELETE /v1/transactions[/{id}]`, `POST /v1/transactions/{id}/void`, `GET /v1/reports/trial-balance`
- **Envelopes + sinking funds + pools (Phase 4):** `GET/POST/PATCH/DELETE /v1/envelopes[/{id}]`, `GET/POST/PATCH/DELETE /v1/sinking-funds[/{id}]`, `GET /v1/pools/{id}/balance`, `POST /v1/pools/{id}/{refill,transfer,budget-inflow}`, `GET/POST/PATCH/DELETE /v1/refill-schedules[/{id}]`
- **Importers + reconciliation (Phase 5):** `POST /v1/imports[?force=true]`, `GET /v1/imports/{id}`, `POST /v1/imports/{id}/{apply,lines/{line_id}/promote}`, `GET/POST/PATCH/DELETE /v1/imports/profiles[/{id_or_name}]` (CSV column-mapping profiles, YAML round-trip), `GET/POST/DELETE /v1/reconciliations[/{id}][?cascade=true]`, `POST /v1/reconciliations/{id}/{auto-match,complete,matches,carry-forward}`, `POST /v1/reconciliations/{id}/matches/{id}/reject`, `DELETE /v1/reconciliations/{id}/carry-forward/{tx_id}`

Every non-2xx response is `application/problem+json` per RFC 9457. In production, supply `TULIP_JWT_SECRET` and the master key from a secret store rather than generating fresh on every start (existing tokens and field-encrypted columns won't validate after a restart with new secrets). The master key can come from one of two sources, in this order of precedence:

1. **`TULIP_MASTER_KEY`** — base64-encoded 32 bytes, inline in the environment. Convenient for `docker run -e ...` and CI.
2. **`TULIP_KEY_FILE`** — path to a file containing the base64-encoded 32 bytes. The file **must** be `chmod 0600` (owner-only RW); any group or other access bit refuses boot with a typed error. This is the recommended path for self-hosted internal-beta deploys via Docker secrets (#132).

If neither is set, an ephemeral key is generated and a warning is logged — fine for tests and dev, fatal for production.

### Running the CLI

A short tour through Phase 5's end-to-end loop (register → import → reconcile):

```bash
# Set up.
uv run tulip register --email me@example.com --display-name Me --household Mine
uv run tulip auth login --email me@example.com
uv run tulip accounts add --name Checking --type asset --currency USD --code 1110
uv run tulip accounts add --name Food --type expense --currency USD --code 5100

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

Other top-level commands: `tulip envelopes`, `tulip sinking-funds`, `tulip refills`, `tulip transfer`, `tulip refill`, `tulip budget-inflow`, `tulip transactions {show,edit,void,delete}`, `tulip imports {profiles,apply}`. Each takes `--help`; the surface mirrors the API endpoints listed above.

`tulip doctor` runs five smoke checks against the configured API (reachability, master-key loaded, migration head, attachment-root writable, token store reachable) and exits 0 / 1 / 2 for all-good / warnings / hard failure. Run it first when something looks off; full QUICKSTART integration lands with [#138](https://github.com/rmwarriner/tulip-accounting/issues/138).

## Development discipline

This project follows test-driven development. Every feature ships with tests written **before** the implementation (red → green → refactor). PRs that don't include tests for new code paths are rejected by CI policy.

- **Coverage floor:** 85% (target 90% on shipping packages, ≥90% on `tulip-core`)
- **Property-based tests** required for all `tulip-core` invariants (using [hypothesis](https://hypothesis.readthedocs.io/))
- **Module boundary rules** are enforced by architecture tests — `tulip-core` is pure domain code and must not import any I/O package; see [docs/ARCHITECTURE.md §9](docs/ARCHITECTURE.md)
- **No `float` ever touches money** — `decimal.Decimal` only, with a `Money` value object that pairs amount with currency
- **Audit log** writes accompany every mutation; the discipline of "if it changes data, it's audited" is enforced in code review

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — full system design, data model, phase roadmap, error-handling standard (§7.8)
- [Phase Status](docs/PHASE_STATUS.md) — what's shipped, what's queued
- [Threat Model](docs/THREAT_MODEL.md) — lightweight security checkpoint (deep audit deferred to Phase 8)
- [ADRs](docs/adrs/) — architectural decision records (envelope shadow ledger, scheduler primitive, mutation testing, reconciliation)
- [Phase 0 Checklist](docs/PHASE_0_CHECKLIST.md) — original bootstrap checklist (historical)
- [CONTRIBUTING.md](CONTRIBUTING.md) — TDD discipline, coverage gates, signed commits, manual smoke test format
- [CLAUDE.md](CLAUDE.md) — operational notes for AI-assisted development on this repo
- Additional docs (QUICKSTART, DEPLOYMENT, BACKUP_RESTORE, AI) land as their respective phases are built. The OpenAPI spec at `/openapi.json` is the live contract for `tulip-api` until `docs/API.md` exists.

## Security & privacy

- **Defense-in-depth encryption at rest:** SQLCipher for the whole DB, AES-256-GCM field-level for the most sensitive columns (account numbers, TOTP secrets, AI API keys), separate AES-256-GCM for attachments. No single key compromise leaks everything.
- **Master key** is derived from a passphrase entered at API startup; never written to disk in plaintext.
- **MFA (TOTP)** required for admin users by default; optional for household members.
- **AI privacy posture** is per-tenant + per-user policy, with audit logging of every model invocation. Defaults to permissive in v1; users can dial up restrictions or switch to local-only models (Ollama).

A formal threat model lands in `docs/SECURITY.md` during Phase 8.

## License

This project is licensed under the **GNU Affero General Public License v3.0 or later** (AGPL-3.0-or-later). See [LICENSE](LICENSE) for the full text.

The AGPL is a strong copyleft license. In practical terms:

- You can use, modify, and redistribute this software freely.
- If you distribute modified versions, you must release your modifications under AGPL-3.0-or-later as well.
- **Importantly:** if you run a modified version as a network service (e.g., host it as a SaaS), you must offer the source of your modified version to the users of that service. This is the AGPL's network clause (§13).

This license was chosen as a forward-looking hedge: it preserves the option for the project owner to offer a hosted version commercially in the future without others undercutting that with closed-source forks. For purely self-hosted use (the v1 target), AGPL is no different from GPL or any other copyleft license — your local install is yours.

### Source file headers

Each source file should start with an SPDX-style header:

```python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 <Your Name>
```

This is recognized by `licensee`, [REUSE](https://reuse.software/), and other compliance tools, and is far more compact than the full GNU recommended header (which is also acceptable; see the bottom of the `LICENSE` file).

## Project name

"Tulip" is a working name. The 17th-century Dutch tulip mania is, on reflection, an unfortunate naming reference for a financial-discipline tool — it may get renamed before v1.0.

# Tulip Accounting

Household-focused, double-entry accounting system with first-class envelope budgeting and sinking-fund support.

> **Status:** Pre-alpha — Phases 0, 1, and 2 complete (project bootstrap, storage + accounting engine, API surface for auth + accounts + transactions). See [docs/PHASE_STATUS.md](docs/PHASE_STATUS.md) for current progress and the queued Phase 2.x work, or [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design.

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
uv sync                          # installs all workspace packages + dev deps
uv run pre-commit install        # enable pre-commit hooks
uv run pytest                    # confirm tests pass (184 tests, ~5s)
```

> **Note for committers:** `main` is branch-protected and **requires every commit to be signed**. Configure SSH or GPG commit signing before your first push (`git config --global commit.gpgsign true` plus a signing key); see [CONTRIBUTING.md](CONTRIBUTING.md#branch-protection-on-main) for the details, the most common failure modes, and the diagnostic checklist if a push is rejected as unsigned.

### Initialize a database

```bash
# From the repo root, against a local SQLite file:
TULIP_DATABASE_URL=sqlite:///./tulip.db \
  uv run alembic -c packages/tulip-storage/alembic.ini upgrade head
```

### Common commands

```bash
uv run pytest                                # full test suite (all packages)
uv run pytest packages/tulip-core            # tests for a single package
uv run pytest -m property                    # only property-based (hypothesis) tests
uv run ruff check                            # lint
uv run ruff format                           # autoformat
uv run mypy                                  # type check (strict)
uv run pre-commit run --all-files            # run all pre-commit hooks
```

### Running the API server

```bash
TULIP_DATABASE_URL=sqlite:///./tulip.db \
TULIP_JWT_SECRET="$(uv run python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  uv run uvicorn tulip_api.main:create_app --factory --host 127.0.0.1 --port 8000
```

Then `curl http://127.0.0.1:8000/health` for a smoke check, or `curl http://127.0.0.1:8000/openapi.json` for the OpenAPI spec. Available endpoints:

- `POST /v1/auth/{register,login,refresh,logout}`
- `GET/POST/PATCH/DELETE /v1/accounts[/{id}]`
- `GET/POST /v1/transactions[/{id}]`

In production, supply `TULIP_JWT_SECRET` from a secret store rather than generating fresh on every start (existing tokens won't validate after a restart with a new secret).

### Running the CLI (once Phase 3 lands)

```bash
uv run tulip auth login
uv run tulip accounts list
uv run tulip add 2026-04-29 'Grocery store' \
  --debit 'Expenses:Food:Groceries' 87.42 \
  --credit 'Assets:Checking' 87.42
```

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
- [Phase 0 Checklist](docs/PHASE_0_CHECKLIST.md) — original bootstrap checklist (Phase 0 complete)
- Additional docs (DATA_MODEL, API, CLI, DEPLOYMENT, BACKUP_RESTORE, SECURITY, AI) land as their respective phases are built. The OpenAPI spec at `/openapi.json` is the live contract for `tulip-api` until `docs/API.md` exists.

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

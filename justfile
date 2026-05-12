# Tulip Accounting — task runner
#
# Recipes mirror the jobs that CI enforces (.github/workflows/ci.yml). When CI
# changes, update this file in the same PR — it's meant to be a single source
# of truth for "what command do I run locally?".
#
# Run `just` (no args) or `just help` to see the recipe list.

set shell := ["bash", "-cu"]

# Default recipe: list everything available.
default:
    @just --list

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

# Install all workspace packages and dev dependencies.
sync:
    uv sync --all-packages --dev

# Install pre-commit hooks into .git/hooks/.
precommit-install:
    uv run pre-commit install

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# Worker cap for pytest-xdist. `auto` resolves to os.cpu_count(), which on
# typical dev machines (8-12 cores) saturates the box and makes the rest
# of the system unusable mid-run. Cap to 4 — leaves headroom for browser /
# editor / Slack and matches CI's 4-vCPU runner so local + CI stay aligned.
# Override per-invocation via `XDIST_WORKERS=8 just test`.
export XDIST_WORKERS := env_var_or_default("XDIST_WORKERS", "4")

# Run the full test suite, parallelised via pytest-xdist (matches CI).
test:
    uv run pytest -n auto --maxprocesses {{XDIST_WORKERS}}

# Fast loop — skip slow / integration markers for quick local feedback.
test-fast:
    uv run pytest -n auto --maxprocesses {{XDIST_WORKERS}} -m "not slow and not integration"

# Run tests with coverage and the 85% gate (mirrors CI exactly).
coverage:
    uv run pytest -n auto --maxprocesses {{XDIST_WORKERS}} \
        --cov \
        --cov-report=term \
        --cov-report=html \
        --cov-fail-under=85

# ---------------------------------------------------------------------------
# Lint / format / type-check
# ---------------------------------------------------------------------------

# Lint with ruff.
lint:
    uv run ruff check

# Apply ruff's autofixes and reformat.
format:
    uv run ruff check --fix
    uv run ruff format

# Verify formatting without writing (CI variant — fails if anything would change).
format-check:
    uv run ruff format --check

# Strict static type checking.
typecheck:
    uv run mypy

# Run all pre-commit hooks across the full tree.
precommit:
    uv run pre-commit run --all-files

# Audit installed third-party deps for known CVEs (mirrors CI).
audit:
    uv run pip-audit --skip-editable

# Run pytest-benchmark performance baselines (excluded from default test loop).
# Sequential — pytest-benchmark is incompatible with xdist parallelism.
bench:
    uv run pytest -m benchmark --benchmark-only

# Run mutation testing on tulip-core. SLOW — expect roughly an hour of
# wall-clock on a default machine. Don't run this in the inner dev loop;
# CI fires it weekly via .github/workflows/mutation.yml.
mutate:
    uv run mutmut run

# Show mutation testing results (run after `just mutate` completes).
mutate-results:
    uv run mutmut results

# Replay the docs/QUICKSTART.md flow end-to-end against a fresh stack
# (#138). Intent is to surface drift between the doc and the code:
# if any of the commands in the walkthrough stop returning a 0 exit
# code, this recipe fails. NOT a CI gate today (docker-in-docker on
# the runners is its own can of worms); run locally before merging
# anything that touches the import, reconcile, periods, or backup
# CLI surfaces.
#
# Assumes:
#   - Docker + Compose v2 on the host.
#   - Existing deploy/docker/secrets/{master-key,jwt-secret}. If you
#     don't have them, the recipe will bail with the same generation
#     command the QUICKSTART documents.
#   - Port 8000 free.
#
# The recipe writes to a sibling tmp dir so it doesn't clobber the
# real ./tulip.db a developer might be using. Sets TULIP_TOKEN_STORE
# to a temp file so it never touches the OS keyring.
quickstart-smoke:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -s deploy/docker/secrets/master-key ] || [ ! -s deploy/docker/secrets/jwt-secret ]; then
        echo "quickstart-smoke: deploy/docker/secrets/{master-key,jwt-secret} not found." >&2
        echo "Generate them as docs/QUICKSTART.md §2 documents, then re-run." >&2
        exit 2
    fi
    SCRATCH="$(mktemp -d)"
    trap 'docker compose -f deploy/docker/compose.yml down -v >/dev/null 2>&1 || true; rm -rf "$SCRATCH"' EXIT
    export TULIP_API_URL=http://127.0.0.1:8000
    export TULIP_TOKEN_STORE="$SCRATCH/tokens.json"
    PASSWORD="quickstart-smoke-password-not-a-real-one"
    docker compose -f deploy/docker/compose.yml up --build --wait
    uv run tulip doctor || echo "doctor reported warnings/failures (expected before login)"
    uv run tulip register --email me@example.com --display-name Me \
        --household "QS House" --password-stdin <<< "$PASSWORD"
    uv run tulip auth login --email me@example.com --password-stdin <<< "$PASSWORD"
    uv run tulip accounts add --code 1010 --name Checking  --type asset   --currency USD
    uv run tulip accounts add --code 5100 --name Groceries --type expense --currency USD
    uv run tulip accounts add --code 5200 --name Rent      --type expense --currency USD
    uv run tulip accounts add --code 5300 --name Fuel      --type expense --currency USD
    uv run tulip accounts add --code 5400 --name Dining    --type expense --currency USD
    uv run tulip accounts add --code 4000 --name Salary    --type income  --currency USD
    BATCH_ID=$(uv run tulip --json imports ofx docs/quickstart-fixtures/sample-statement.ofx --account 1010 | jq -r .id)
    uv run tulip imports apply "$BATCH_ID"
    RECON_ID=$(uv run tulip --json reconcile create --account 1010 --batch "$BATCH_ID" \
        --period 2026-05-01..2026-05-31 --starting 0.00 --ending 3611.88 | jq -r .id)
    uv run tulip reconcile auto-match "$RECON_ID"
    uv run tulip reconcile complete "$RECON_ID"
    PERIOD_ID=$(uv run tulip --json periods list | jq -r '.[0].id')
    uv run tulip periods close "$PERIOD_ID"
    # §8 — Reports + journal export/import (Phase 7 surface).
    uv run tulip reports trial-balance > "$SCRATCH/tb.json"
    test -s "$SCRATCH/tb.json"
    uv run tulip reports trial-balance --format pdf --output "$SCRATCH/tb.pdf"
    test -s "$SCRATCH/tb.pdf"
    uv run tulip journal export --output "$SCRATCH/ledger.journal"
    test -s "$SCRATCH/ledger.journal"
    uv run tulip journal import "$SCRATCH/ledger.journal"
    docker compose -f deploy/docker/compose.yml exec -T api \
        tulip backup --out - > "$SCRATCH/backup.tar.gz"
    test -s "$SCRATCH/backup.tar.gz"
    uv run tulip backup-inspect "$SCRATCH/backup.tar.gz"
    echo "quickstart-smoke: all steps passed."

# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

# Run every check CI runs, in roughly the same order. Use before pushing.
ci: lint format-check typecheck coverage audit

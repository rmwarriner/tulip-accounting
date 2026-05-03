# Tulip Accounting — task runner
#
# Recipes mirror the jobs that CI enforces (.github/workflows/ci.yml). When CI
# changes, update this file in the same PR — it's meant to be a single source
# of truth for "what command do I run locally?".
#
# Run `just` (no args) or `just help` to see the recipe list.

set shell := ["bash", "-cu"]

# Raise the file-descriptor soft limit before running pytest. Default macOS
# soft limit is 256, which is too low for parallel xdist + per-test
# SQLAlchemy engine pools across 700+ tests; symptom is `OSError: [Errno
# 24] Too many open files` mid-run. CI Linux runners default to 1024+ and
# don't need this. The redirect silences the error if the platform's hard
# limit caps below our request — tests still run with whatever soft limit
# was in place. See #90 for the underlying engine-disposal cleanup.
fd_bump := "ulimit -n 8192 2>/dev/null || true"

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

# Run the full test suite, parallelised via pytest-xdist (matches CI).
test:
    {{fd_bump}}; uv run pytest -n auto

# Fast loop — skip slow / integration markers for quick local feedback.
test-fast:
    {{fd_bump}}; uv run pytest -n auto -m "not slow and not integration"

# Run tests with coverage and the 85% gate (mirrors CI exactly).
coverage:
    {{fd_bump}}; uv run pytest -n auto \
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
    {{fd_bump}}; uv run pytest -m benchmark --benchmark-only

# Run mutation testing on tulip-core. SLOW — expect roughly an hour of
# wall-clock on a default machine. Don't run this in the inner dev loop;
# CI fires it weekly via .github/workflows/mutation.yml.
mutate:
    uv run mutmut run

# Show mutation testing results (run after `just mutate` completes).
mutate-results:
    uv run mutmut results

# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

# Run every check CI runs, in roughly the same order. Use before pushing.
ci: lint format-check typecheck coverage audit

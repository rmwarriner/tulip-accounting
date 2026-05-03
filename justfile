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

# Run the full test suite (matches CI's default loop).
test:
    uv run pytest

# Fast loop — skip slow / integration markers for quick local feedback.
test-fast:
    uv run pytest -m "not slow and not integration"

# Run tests with coverage and the 85% gate (mirrors CI exactly).
coverage:
    uv run pytest \
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

# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

# Run every check CI runs, in roughly the same order. Use before pushing.
ci: lint format-check typecheck coverage

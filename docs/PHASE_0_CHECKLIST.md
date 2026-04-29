# Tulip Accounting — Phase 0 Checklist (Project Bootstrap)

This checklist drives the first development session. Hand it (along with `docs/ARCHITECTURE.md`) to Claude Code as the bootstrap brief.

**Goal of Phase 0:** Land a uv workspace skeleton, working CI pipeline, pre-commit hooks, and the `tulip-core` package containing the `Money`, `Currency`, and `Account` value objects — each developed test-first with property-based tests in place.

**Done criteria for Phase 0:**

- [ ] CI pipeline is green on `main`
- [ ] All seven workspace packages exist with valid `pyproject.toml` files (most as empty stubs)
- [ ] `tulip-core` ships `Money`, `Currency`, and `Account` value objects with ≥90% line coverage
- [ ] Property-based tests (hypothesis) exercise all `Money` arithmetic invariants
- [ ] Pre-commit hooks installed and passing on `pre-commit run --all-files`
- [ ] `README.md`, `docs/ARCHITECTURE.md`, and this checklist are committed

---

## TDD discipline reminder

Every step that introduces production code follows red → green → refactor:

1. **Red.** Write a failing test that captures the intended behavior. Run it. Confirm it fails for the *right* reason (not a syntax error or import problem).
2. **Green.** Write the simplest possible implementation that makes the test pass. Resist the urge to write more than that.
3. **Refactor.** With tests passing, improve the code without changing behavior. Run tests after each refactor. Tests stay green.

When in doubt, write another test first. Tests are the spec; production code is the consequence.

---

## Step 1 — Repository initialization

- [ ] `git init`; create `.gitignore` with sensible Python defaults (`__pycache__/`, `.venv/`, `*.egg-info/`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `htmlcov/`, `coverage.xml`, `.env`, `*.db`, `*.db-journal`)
- [ ] Add `.editorconfig` for consistent whitespace across editors
- [ ] Commit `README.md`, `docs/ARCHITECTURE.md`, `docs/PHASE_0_CHECKLIST.md` (this file)

## Step 2 — uv workspace skeleton

> *Modern context note: `uv` (https://docs.astral.sh/uv/) is the package manager and workspace tool. A "workspace" is a single repo with multiple installable Python packages sharing a lockfile — analogous to a Cargo workspace or a Yarn/pnpm workspace.*

- [ ] Root `pyproject.toml` declares the workspace and shared dev dependencies:
  - `[tool.uv.workspace]` with `members = ["packages/*"]`
  - Shared dev deps: `pytest`, `pytest-asyncio`, `pytest-cov`, `hypothesis`, `mypy`, `ruff`, `pre-commit`, `polyfactory`
- [ ] Create the seven package directories with stub `pyproject.toml` files (most empty for now):
  - `packages/tulip-core/`
  - `packages/tulip-storage/`
  - `packages/tulip-api/`
  - `packages/tulip-ai/`
  - `packages/tulip-importers/`
  - `packages/tulip-reports/`
  - `packages/tulip-cli/`
- [ ] Each package gets `src/<package_name>/__init__.py` (minimal, with package docstring) and a `tests/` directory
- [ ] Run `uv sync --all-packages --dev` and confirm a clean lockfile is produced
- [ ] Commit the lockfile (`uv.lock`)

## Step 3 — Tooling configuration

### ruff

- [ ] Add `[tool.ruff]` section to root `pyproject.toml`:
  - `target-version = "py312"`
  - `line-length = 100` (or your preference; 88 is also common)
  - Enable rule sets: `E`, `F`, `W`, `I`, `B`, `UP`, `RUF`, `S` (security), `D` (docstrings), `ANN` (type annotations)
  - Per-file ignores for tests (allow `S101` `assert`, drop docstring requirements)
- [ ] Run `uv run ruff check` — should pass with empty workspace
- [ ] Run `uv run ruff format --check` — should pass

### mypy

- [ ] Add `[tool.mypy]` section to root `pyproject.toml`:
  - `python_version = "3.12"`
  - `strict = true`
  - `warn_unreachable = true`
  - `enable_error_code = ["redundant-expr", "truthy-bool", "ignore-without-code"]`
- [ ] Confirm `uv run mypy` runs (will pass on empty packages)

### pytest

- [ ] Add `[tool.pytest.ini_options]` section to root `pyproject.toml`:
  - `testpaths = ["packages/*/tests"]`
  - `addopts = "-ra --strict-markers --strict-config"`
  - Register markers: `property`, `integration`, `slow`
- [ ] Add `[tool.coverage.run]` section: `source = ["packages"]`, `branch = true`
- [ ] Add `[tool.coverage.report]` section: `fail_under = 85`, `show_missing = true`, `skip_covered = false`

## Step 4 — Pre-commit hooks

- [ ] Create `.pre-commit-config.yaml` with hooks:
  - `ruff` (lint)
  - `ruff-format`
  - `check-added-large-files`
  - `check-merge-conflict`
  - `check-toml`
  - `check-yaml`
  - `detect-secrets` or `gitleaks` (optional but recommended)
  - `trailing-whitespace`
  - `end-of-file-fixer`
- [ ] Install: `uv run pre-commit install`
- [ ] Run on full tree: `uv run pre-commit run --all-files` — should pass

## Step 5 — CI pipeline

- [ ] Place `ci.yml` (provided alongside this checklist) at `.github/workflows/ci.yml`
- [ ] Push branch; confirm CI runs and all four jobs (`lint`, `type-check`, `test`, `secrets-scan`) succeed on the empty workspace
- [ ] Configure branch protection on `main`: require the `all-checks-passed` job

## Step 6 — `tulip-core`: `Money` value object (TDD)

> *This is the foundation invariant of the whole system. Do not skip the property tests — they will catch real bugs when arithmetic gets composed in transactions later.*

### 6a. Red — write failing tests

Create `packages/tulip-core/tests/test_money.py` with these tests *before* writing any implementation:

- [ ] `Money` constructs from a `Decimal` amount and an ISO 4217 currency code
- [ ] Constructing with a `float` amount **raises** (floats forbidden anywhere near money)
- [ ] Constructing with an unknown currency code **raises**
- [ ] `Money` is immutable (frozen dataclass — assignment to fields raises `FrozenInstanceError`)
- [ ] Two `Money` values with the same amount and currency are equal
- [ ] Two `Money` values with different currencies are **not** equal (even if amounts match)
- [ ] Adding two `Money` values of the same currency returns a `Money` with the sum
- [ ] Adding two `Money` values of different currencies **raises** `CurrencyMismatchError`
- [ ] Subtracting works analogously (same currency only)
- [ ] Negation returns a `Money` with negated amount and same currency
- [ ] Multiplying `Money` by a `Decimal` or `int` returns a `Money`
- [ ] Multiplying `Money` by another `Money` **raises** (you can't multiply money by money)
- [ ] `Money.zero("USD")` returns a zero-amount `Money` in USD
- [ ] `repr(money)` produces a useful debug string

Run: `uv run pytest packages/tulip-core/tests/test_money.py` — should report many failures (no `Money` class exists yet). **Verify failures are import errors, not test logic errors.**

### 6b. Property-based tests — write before implementation finishes

Create `packages/tulip-core/tests/test_money_properties.py` and mark with `@pytest.mark.property`:

- [ ] **Commutativity:** `forall a, b in Money[USD]: a + b == b + a`
- [ ] **Associativity:** `forall a, b, c in Money[USD]: (a + b) + c == a + (b + c)`
- [ ] **Identity:** `forall a in Money[USD]: a + Money.zero("USD") == a`
- [ ] **Inverse:** `forall a in Money[USD]: a + (-a) == Money.zero("USD")`
- [ ] **No precision loss:** `forall a in Money[USD], n in int: (a * n) / n == a` (round-trip test using `Decimal` only)

Use a hypothesis strategy `decimals_in_money_range()` that generates `Decimal` values within sensible bounds (e.g., -1e12 to 1e12, with up to 8 fractional digits — matching the schema in ARCHITECTURE §4).

### 6c. Green — minimal implementation

Create `packages/tulip-core/src/tulip_core/money/money.py`:

- [ ] `@dataclass(frozen=True, slots=True) class Money` with fields `amount: Decimal` and `currency: str`
- [ ] `__post_init__` validates: amount is `Decimal` (not `float`), currency is in known set
- [ ] `__add__`, `__sub__`, `__neg__`, `__mul__` implemented per the test contract
- [ ] `Money.zero(currency)` classmethod
- [ ] `__repr__` returns e.g. `Money('87.42', 'USD')`

Run tests until all green. **Resist any feature creep beyond what the tests require.**

### 6d. Refactor

- [ ] Extract currency validation into a `Currency` value object (Step 7 below) — but only after all tests are green
- [ ] Add docstrings to all public methods (ruff `D` rules will require these)
- [ ] Verify coverage with `uv run pytest --cov=packages/tulip-core --cov-report=term-missing`

## Step 7 — `tulip-core`: `Currency` value object (TDD)

Same red-green-refactor cycle. Tests first:

- [ ] `Currency` is constructed from an ISO 4217 code (string, 3 uppercase letters)
- [ ] Invalid codes raise (e.g., `"USDX"`, `"usd"`, `"X"`, `""`)
- [ ] Each currency has known `minor_units` (e.g., USD = 2, JPY = 0, BHD = 3)
- [ ] `Currency.from_code("USD")` returns the canonical instance (consider caching/interning)
- [ ] Two `Currency` instances with the same code are equal

Implementation follows. The currency table can be a small constant module-level `dict`; expand later from a data source if needed.

## Step 8 — `tulip-core`: `Account` value object (TDD, lighter)

`Account` is a structural value object at the core layer (the persistence-aware `Account` model lives in `tulip-storage`). At core level, it just holds:

- `id`, `code` (optional), `name`, `type` (enum: asset/liability/equity/income/expense), `currency`, `parent_id` (optional)
- Tests cover: type validation (must be valid enum), code format if provided, equality based on id

Same TDD cycle.

## Step 9 — Architecture test scaffolding

> *Architecture tests assert that the module-boundary rules described in ARCHITECTURE.md §9 are not violated by future code. Setting them up empty now is much cheaper than back-filling later.*

- [ ] Create `packages/tulip-core/tests/test_architecture.py`
- [ ] Add a single failing assertion that confirms the test framework runs (e.g., import the module and assert truthy)
- [ ] Note in a comment: real architecture rules (e.g., `tulip-core may not import tulip-storage`) will be added in Phase 1 when the boundary surface exists

## Step 10 — Wrap up Phase 0

- [ ] All Phase 0 done-criteria checked off at the top of this document
- [ ] CI green on `main` after merge
- [ ] Commit message for the wrap-up PR includes the line: `Phase 0 complete; ready for Phase 1.`
- [ ] Open a brief PR description summarizing what's now in place and proposing the Phase 1 plan (per ARCHITECTURE.md §10)

---

## Anti-patterns to avoid in Phase 0

- ❌ Writing implementation code before the failing test — this is the most common slip in TDD; do not normalize it
- ❌ Adding fields, methods, or features "because we'll need them in Phase 1" — wait until Phase 1's tests demand them
- ❌ Suppressing mypy errors with `# type: ignore` without a comment explaining *why* — every ignore needs a justification
- ❌ Letting CI go red on `main` for more than the time it takes to revert — green main is a covenant
- ❌ Importing anything from `tulip-storage`, `tulip-api`, etc. into `tulip-core` — this is the boundary that protects the whole architecture
- ❌ Using `float` anywhere a `Decimal` would be correct — once a `float` enters monetary code, the bug surface is permanent

# Claude Code — project guide

Operational notes for Claude Code (and any other AI assistant) working in this
repository. The substantive project conventions live in
[`CONTRIBUTING.md`](CONTRIBUTING.md) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md);
this file points at them and adds the few things that aren't covered there.

## First read

1. [`docs/PHASE_STATUS.md`](docs/PHASE_STATUS.md) — what's shipped, what's in flight, what's next. Always check before proposing work.
2. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the *why* behind the shape of the code. §1.1 (design principles), §7 (cross-cutting concerns), §9 (project layout), §10 (phase plan).
3. [`CONTRIBUTING.md`](CONTRIBUTING.md) — TDD, coverage, ruff/mypy, signed commits, branch protection, manual smoke test format. The rules in there are non-negotiable.

## Toolchain

- `uv` is the package manager. Use `uv sync --all-packages --dev` to install, `uv run <cmd>` to invoke. Never `pip`, never `python -m venv` — `uv` manages the workspace.
- The [`justfile`](justfile) wraps the common loops: `just test`, `just lint`, `just typecheck`, `just coverage`, `just audit`, `just bench`, `just mutate`, `just ci` (run-all). `just --list` shows everything.
- Recipes mirror `.github/workflows/ci.yml`. If you change CI, update recipes in the same PR (and vice versa).
- Editor: [`.vscode/`](.vscode/) is committed (extensions, settings, debug launch configs). Open the repo in VS Code, accept the recommended-extensions prompt, and the editor's lint/format/type-check loop matches `just ci`.
- MCP: [`.mcp.json`](.mcp.json) configures a project-scoped `sqlite` MCP at `./tulip.db`. Restart your Claude Code session if you've just changed `.mcp.json` — MCP servers are launched at session start.

## Workflow

### Slice-per-PR rhythm

One GitHub issue → one branch → one PR → squash-merge to `main` → sync → next slice. Recent commit history (`git log --oneline -20`) shows the cadence — each PR closes one issue and lands a coherent step.

### Branch and PR titles

- Phase work: `P<phase>.<slice>[.<sub>]` prefix (e.g. `P4.3.c — refill-schedule API + CLI surface`).
- Tooling, CI, docs: conventional-commit type (`chore:`, `ci:`, `docs:`, `test:`, etc.).
- Always include the issue number: `(#76)` or `closes #76`. Multiple issues are fine: `(#69, closes Phase 4)`.

### PR body

Use the structure already established in recent PRs:

```
## Summary
…three-to-five bullet points of what changed and why…

## Test plan
- [x] commands you ran locally and what they showed
- [ ] CI green on this PR
```

Manual smoke tests **must** include the full runnable steps inline (env vars, requests with bodies, expected outcomes, cleanup). The bar is "a reviewer can paste the block and reproduce" — see [CONTRIBUTING.md → Manual smoke tests](CONTRIBUTING.md).

### After opening a PR

Watch CI with the **`Monitor`** tool, not `Bash run_in_background` — Monitor emits one event per check transition; bash watch goes silent until the very end. The poll script in [`feedback_auto_pr_workflow.md` user memory](../../.claude/projects/-Users-robert-Projects-tulip-accounting/memory/feedback_auto_pr_workflow.md) is the canonical shape.

When the Monitor reports `ALL_GREEN`, squash-merge with `gh pr merge <N> --squash --delete-branch`, sync `main` locally (`git checkout main && git pull --ff-only && git branch -D <branch>`), then surface the next slice.

**Do not** enable repo-level auto-merge or pass `--auto` to `gh pr merge`. Past experience is that auto-merge fired before required checks gated the merge — a manual squash-merge from this side after `ALL_GREEN` is the safer pattern.

## CI surface

Three workflows live under `.github/workflows/`:

- **`ci.yml`** runs on every PR and every push to `main`. Jobs are path-conditional via a top-level `changes` job (#92):
  - `lint` and `secrets-scan` always run.
  - `type-check` runs when Python source or `pyproject.toml` changed.
  - `test` and `benchmarks` run when Python source / `pyproject.toml` / `uv.lock` / `.github/workflows/**` changed.
  - `dependency-audit` runs when `pyproject.toml` / `uv.lock` / `.github/workflows/**` changed.
  - `push` to `main` (post-merge) always runs all jobs as a final gate, regardless of paths.
  - The aggregate `All checks passed` job accepts `success` *or* `skipped`.
  - Net effect: docs/tooling-only PRs run in ~1-2 min; code PRs run in ~6-7 min.

- **`mutation.yml`** runs `mutmut` against `tulip-core` weekly (Sunday 06:00 UTC) and on `workflow_dispatch`. Surviving mutants are reported to a tracking issue, not a PR gate. Don't add per-PR mutation testing — a full run is 1-2h on a 4-vCPU runner. ADR-0003 covers the reasoning.

- **`labeler.yml`** auto-applies `area:<package>` and `area:{ci,docs,tooling}` labels to PRs based on changed paths. Path mapping in `.github/labeler.yml` mirrors the `changes` job's filters.

When editing any `.github/workflows/*.yml`, the security pre-commit hook fires informationally. Don't reference untrusted PR-controlled context (`github.event.issue.title`, `github.event.pull_request.body`, etc.) inside `run:` blocks; bind them through `env:` variables when needed.

## What goes where

- **Bugs and ideas**: GitHub Issues on `rmwarriner/tulip-accounting`. Don't invent parallel TODO files in the repo. The repo has issue templates (`.github/ISSUE_TEMPLATE/{bug,feature}.yml`) for human use; when filing programmatically, follow the same body shape (Why / Scope / Out of scope / Acceptance) recent issues use.
- **Vulnerability reports**: private security advisory per [`SECURITY.md`](SECURITY.md), not a regular issue. The engineering threat model lives separately in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).
- **Architectural decisions** (new dependency, deviation from established pattern, design tradeoff): an ADR in [`docs/adrs/`](docs/adrs). Look at existing ones for the format.
- **Phase tracking**: update [`docs/PHASE_STATUS.md`](docs/PHASE_STATUS.md) at the end of a slice — it's the source of truth for project state.

## Hard project rules (worth reading even though they live elsewhere)

These are the easiest things for an AI assistant to inadvertently break. The full statements are in `CONTRIBUTING.md`; capturing the headlines here so they aren't missed:

- **TDD is mandatory.** Failing test first, then minimal code to pass, then refactor. No production code lands without a corresponding test.
- **No `float` on money.** Always `decimal.Decimal`, always paired with a currency in the `Money` value object.
- **Module boundaries are enforced.** `tulip-core` is pure domain logic — no I/O, no SQLAlchemy, no FastAPI. The architecture tests fail PRs that violate this. See [`docs/ARCHITECTURE.md` §9](docs/ARCHITECTURE.md).
- **Coverage gate at 85%** (90% inside `tulip-core`). Don't argue with the gate — write more tests.
- **`ruff` + `mypy --strict`** with the `S`/`D`/`ANN` selects on. Pre-commit runs them; CI checks them. `# type: ignore` requires a comment.
- **Signed commits to `main`.** Branch protection rejects unsigned pushes — `CONTRIBUTING.md → Branch protection on main` covers the diagnosis when this surprises you.

## Test fixture and marker patterns

These are conventions that aren't enforced by the type system or pre-commit but matter under parallel test execution.

- **Engine fixtures must dispose.** When adding a fixture that creates a SQLAlchemy `Engine`, return `Iterator[T]` and dispose on teardown (`try / yield / finally: eng.dispose()`). Without this, the connection pool retains FDs until process exit; under xdist parallelism on macOS this exhausts the default 256-fd limit. See #90.
- **Module-level engines use `NullPool`.** The OpenAPI contract test is the only example today (`packages/tulip-api/tests/test_openapi_contract.py`). Module-level engines can't yield + dispose without a refactor; `poolclass=NullPool` closes connections on check-in instead of pooling, eliminating FD residency.
- **`benchmark` marker is excluded from the default loop.** `pyproject.toml [tool.pytest.ini_options].addopts` includes `-m "not benchmark"`. Run benchmarks via `just bench` (sequential — pytest-benchmark is incompatible with xdist).
- **Marker registry.** Existing markers in `pyproject.toml`: `property` (hypothesis), `integration` (DB / network / spawned process), `slow` (excluded from the default fast loop), `benchmark` (perf baselines, excluded from the default loop). Register any new marker — `--strict-markers` is on, so unknown markers fail collection.

## When in doubt

Open an issue and discuss before writing code, especially for anything beyond a small bugfix or doc edit. The maintainer is solo and prefers a five-minute conversation to a 200-line PR that has to be unwound.

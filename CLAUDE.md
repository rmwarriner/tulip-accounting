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
- The [`justfile`](justfile) wraps the common loops: `just test`, `just lint`, `just typecheck`, `just coverage`, `just audit`, `just ci` (run-all). `just --list` shows everything.
- Recipes mirror `.github/workflows/ci.yml`. If you change CI, update recipes in the same PR (and vice versa).

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

Watch CI. When all required checks are green, squash-merge with `gh pr merge <N> --squash --delete-branch`, sync `main` locally, then surface the next slice. Don't enable repo-level auto-merge or use `gh pr merge --auto` — past experience is that it fires before required checks gate the merge.

## What goes where

- **Bugs and ideas**: GitHub Issues on `rmwarriner/tulip-accounting`. Don't invent parallel TODO files in the repo.
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

## When in doubt

Open an issue and discuss before writing code, especially for anything beyond a small bugfix or doc edit. The maintainer is solo and prefers a five-minute conversation to a 200-line PR that has to be unwound.

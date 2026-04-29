# Contributing to Tulip Accounting

Thanks for taking a look. Tulip is currently a solo, hobby-paced project, but contributions are genuinely welcome — especially bug reports, documentation fixes, and small focused PRs.

This file describes how to file issues, how to submit changes, and the project conventions that any change is expected to follow.

## Before you start

- Read the [Architecture document](docs/ARCHITECTURE.md). It's long, but it explains *why* the project is shaped the way it is. Most "why isn't this simpler?" questions are answered there.
- Check the [issues list](../../issues) — your bug or idea may already be tracked.
- For significant changes (anything beyond a small bugfix or doc edit), open an issue first to discuss the approach. This avoids the disappointment of a PR that conflicts with planned direction.

## Filing issues

Good bug reports are gold. A good one includes:

- **What you did** — minimal commands or steps to reproduce
- **What you expected to happen**
- **What actually happened** — error message, unexpected output, etc.
- **Environment** — OS, Python version (`python --version`), uv version (`uv --version`), Tulip version or commit SHA
- **Logs if relevant** — Tulip's structured JSON logs are usually informative; redact any sensitive data first

Feature requests should include the use case, not just the proposed solution. "I want to track shared expenses with a roommate without merging households" is more useful than "add expense splitting" because it lets us think about the right shape of the solution.

## Setting up a development environment

See [README.md](README.md) for the standard setup. The short version:

```bash
git clone https://github.com/<your-fork>/tulip-accounting
cd tulip-accounting
uv sync                          # all workspace packages + dev deps
uv run pre-commit install
uv run pytest                    # confirm green
```

SQLCipher development headers are *only* required once full-DB SQLCipher encryption lands (Phase 1.x). The current Phase 1 / Phase 2 codebase uses field-level AES-256-GCM (via the pure-Python `cryptography` library) and needs no native sqlcipher install to develop or test against.

## Project conventions

These are not optional. Mention them in your PR description so reviewers know you've thought about them.

### TDD is mandatory

Every change that introduces or modifies production code starts with a failing test. The cycle is **red → green → refactor**:

1. Write a test that captures the new behavior. Run it. Confirm it fails for the *right* reason.
2. Write the simplest implementation that makes the test pass.
3. With tests passing, improve the code without changing behavior.

PRs that add code without corresponding tests will be asked to add them before review proceeds. This is the single most important rule.

### Coverage gate

CI fails if the project line coverage drops below **85%**. The `tulip-core` package has a higher floor of **90%**. If your change reduces coverage, the CI failure is real — write more tests, don't argue with the gate.

### Property-based tests for `tulip-core`

Anything in `tulip-core` that has algebraic structure (money arithmetic, balance invariants, period boundaries, allocation pool math) gets a property-based test using [hypothesis](https://hypothesis.readthedocs.io/) in addition to example-based tests. Property tests find edge cases regular fixtures miss.

### Module boundary rules

The `tulip-core` package is **pure domain logic** — no I/O, no framework dependencies, no SQLAlchemy, no FastAPI. The architecture tests enforce this. Adding an import from `tulip-storage` into `tulip-core` is not a refactor; it's a violation. See [ARCHITECTURE.md §9](docs/ARCHITECTURE.md) for the full list of boundary rules.

### No `float` ever touches money

Always `decimal.Decimal`. Always paired with a currency in the `Money` value object. If you're tempted to multiply by `1.10` for a tax rate, write `Decimal("1.10")` instead. PRs that introduce `float` arithmetic on monetary values will be rejected.

### Decimal handling for division

When dividing money (e.g., splitting a bill three ways), be explicit about the rounding mode and the residual. Tulip uses banker's rounding (`ROUND_HALF_EVEN`) and assigns any residual cent to a designated party (typically the first posting). Look at `tulip_core.money.split` for the canonical pattern.

### Code style

- Linting and formatting: **ruff** (no debate, no manual formatting). Pre-commit runs it; CI checks it.
- Type checking: **mypy --strict**. Every public function has type annotations. `# type: ignore` requires a comment explaining why.
- Imports: stdlib, then third-party, then first-party. ruff's `I` rules enforce this.
- Docstrings: required on public functions and classes (ruff's `D` rules). Use Google or NumPy style consistently within a module.

### Commit messages

Conventional Commits format:

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`, `perf`, `build`. Scope is the package or module (`core`, `api`, `storage`, etc.). Subject is imperative ("add envelope refill", not "added envelope refill" or "adds envelope refill").

For changes to behavior, the body should explain *why*, not just *what*. The diff explains what.

### Architecture Decision Records (ADRs)

If your change involves a non-trivial architectural decision — choosing between two reasonable approaches, introducing a new dependency, changing a project convention — add an ADR in `docs/ADRs/`. The format is simple: context, decision, consequences. Look at existing ADRs for examples.

## Submitting a pull request

1. Fork the repo and create a feature branch from `main`.
2. Make your changes following the conventions above. Commit incrementally.
3. Ensure `uv run pytest`, `uv run ruff check`, `uv run mypy`, and `uv run pre-commit run --all-files` all pass locally.
4. Push to your fork and open a PR against `main`.
5. The PR description should:
   - Reference any related issue(s).
   - Briefly explain the change and the rationale.
   - Note any user-visible impact.
   - Confirm that tests were written first (the TDD rule).
6. CI runs automatically. All required checks must pass before review.
7. A reviewer (currently the maintainer; eventually anyone designated) will review and either request changes or merge.

## What gets rejected (so you don't waste your time)

- PRs that add features beyond the v1 scope defined in [ARCHITECTURE.md §1.2](docs/ARCHITECTURE.md). Open an issue first to discuss whether the feature fits.
- PRs that violate module boundary rules.
- PRs that introduce `float` arithmetic on money.
- PRs without tests.
- PRs that lower coverage.
- PRs that significantly broaden the dependency footprint without a corresponding ADR.

## Code of Conduct

Participation in this project is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). Please read it.

## Licensing of contributions

This project is licensed under **AGPL-3.0-or-later** (see [LICENSE](LICENSE)). By submitting a pull request, you agree that your contribution is licensed under the same terms — this is the standard "inbound = outbound" model that GitHub's Terms of Service apply by default ([§D.6](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service#6-contributions-under-repository-license)), and is what almost every open-source project relies on.

If you copy code from another source into a contribution, that source must be license-compatible with AGPL-3.0-or-later (most permissive licenses are; many copyleft licenses are not). Cite the source in your PR description and confirm the license. When in doubt, ask before submitting.

Each new source file you add should start with an SPDX header:

```python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) <year> <Your Name>
```

## Questions

Open an issue with the `question` label, or start a discussion on the repo's Discussions tab. There's no Slack, no Discord, no mailing list — keeping the communication footprint small.

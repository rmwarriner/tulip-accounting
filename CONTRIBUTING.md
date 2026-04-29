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

## Branch protection on `main`

`main` is protected via GitHub branch protection. The rules below apply to *every* push, including the maintainer's. They guard against catastrophes (force-push, accidental delete, unsigned commits sneaking in from a misconfigured worktree) without adding PR-review friction that doesn't fit a solo / small-team project.

### Active rules

| Rule | Setting |
|---|---|
| Block force pushes | ✅ rejected |
| Block branch deletion | ✅ rejected |
| Require linear history | ✅ no merge commits — rebase or squash to land |
| Require signed commits | ✅ every commit on `main` must be signed (SSH or GPG) |
| Allow admins to bypass | ✅ — emergency escape hatch only |
| Required status checks | ❌ off (classic protection only enforces these on PR merges; using rulesets to apply them to direct pushes too is on the future-improvements list) |
| Required PR reviews | ❌ off (solo maintainer) |

### What this means in practice

- `git push origin main` works as long as your commit is signed.
- `git push --force` and `git push --force-with-lease` to `main` are rejected. If you really need a force-push (rebase mishap on `main`), see the recovery section below.
- A merge commit on `main` is rejected. Integrate by **rebase** or **squash**:
  ```bash
  git checkout main
  git pull --rebase
  git checkout my-branch
  git rebase main
  git checkout main
  git merge --ff-only my-branch
  git push
  ```
- An unsigned commit is rejected. The fix is *almost always* a missing or misconfigured local Git config, not a missing key — see the next section.

### Gotcha: unsigned commit rejected on push

By far the most common surprise. Symptom: `git push` succeeds locally but GitHub rejects it with:

> `! [remote rejected] main -> main (commit ... is not signed)`

Diagnose with the three relevant config keys (run from inside the worktree that pushed):

```bash
git config --get commit.gpgsign     # must be 'true'
git config --get gpg.format         # must be 'ssh' (or 'openpgp' if you use GPG)
git config --get user.signingkey    # must point at your signing key
```

Common causes and fixes:

- **A new clone in a new IDE / temp worktree didn't inherit your global config.** Run the three `git config` checks above. If they're missing, your global `~/.gitconfig` is fine — the worktree is reading a *different* user's config (e.g., a Docker image, a sandbox container, a Codespace). Either set them in the worktree or update the environment.
- **An automated tool (a Claude Code agent, a CI helper) created a commit and bypassed signing.** Look for `--no-gpg-sign` in your reflog (`git reflog`). The fix is usually `git commit --amend -S --no-edit && git push`. (Amending is fine here because the commit hasn't been published.)
- **`ssh-agent` doesn't have your signing key loaded.** `ssh-add -l` should list it. If not, `ssh-add ~/.ssh/your_signing_key`. (This is a "git asks for your key passphrase forever" kind of failure too; the agent is the fix.)

If a single bad commit needs to be rewritten *after* it's been amended, you'll need a force-push, which is blocked — see recovery below.

### Recovery: when you genuinely need a force-push

Branch protection is reversible. If you've truly mangled `main` and a force-push is the right answer (rare, and worth a sanity check from a second pair of eyes when possible):

```bash
# 1. Temporarily relax the rules.
gh api --method DELETE /repos/rmwarriner/tulip-accounting/branches/main/protection

# 2. Do the surgery.
git push --force-with-lease origin main

# 3. Restore the rules. (Same commands you used to set them up the first time.)
gh api --method PUT /repos/rmwarriner/tulip-accounting/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": null,
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": false,
  "lock_branch": false,
  "allow_fork_syncing": true
}
JSON

gh api --method POST \
  /repos/rmwarriner/tulip-accounting/branches/main/protection/required_signatures
```

Always re-enable protection in the same session. Leaving `main` unprotected is the kind of mistake the rules exist to prevent.

### Future: tighter CI gating via rulesets

Classic branch protection only enforces required status checks on PR merges, not on direct pushes to `main`. If we ever want "CI must be green before any commit lands on `main`," that's a one-time conversion to GitHub Rulesets. The conversion is non-destructive (rulesets coexist with branch protection) but does change the daily-push workflow — every change goes through a feature branch and PR. We're not doing this today; capturing the option here so it's discoverable.

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

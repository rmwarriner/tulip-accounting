# ADR 0003 — Mutation testing on `tulip-core`

**Status:** Accepted (2026-05-03) — adopted on the slice that closes #79.
**Phase:** Cross-phase (testing infrastructure).
**Supersedes:** None.

---

## Context

Tulip enforces a strict test discipline: TDD per [CONTRIBUTING.md](../../CONTRIBUTING.md), 85% line-coverage gate (90% inside `tulip-core`), property-based tests with hypothesis where the math has algebraic structure, schemathesis contract tests against the OpenAPI spec, and architecture tests for module boundaries.

What that stack proves: code runs, common shapes pass, contracts hold. What it doesn't prove: the tests would *catch a bug if one were introduced*. A test that reads a value but never asserts on it counts toward coverage. A property that's tautological under hypothesis still passes. The most expensive failure mode for an accounting engine — "tests pass, math is silently wrong" — is precisely the failure mode coverage and property tests don't directly defend against.

Mutation testing closes that gap: deliberately introduce small bugs (mutants) and verify that at least one test fails. Surviving mutants name the gaps in test sensitivity.

## Decision

### 1. Tool: `mutmut`

Selected `mutmut` over `cosmic-ray`. Reasons:

- **Maturity and ergonomics.** mutmut's output is friendlier; the `mutmut results` / `mutmut show <id>` workflow is straightforward.
- **Configuration in `pyproject.toml`.** Single source of truth; no separate config file.
- **Active maintenance.** mutmut 3.x is current; cosmic-ray is more flexible but has a steeper config surface.

`cosmic-ray` remains a credible alternative if mutmut's mutator set proves insufficient. Switching cost is bounded: the `[tool.mutmut]` config translates to a `cosmic-ray.toml` largely line-for-line.

### 2. Scope: `tulip-core` only (initially)

Configured to mutate `packages/tulip-core/src/` only. `tulip-storage` is the obvious next target — the SQLAlchemy repositories are also load-bearing for correctness — but a single mutation run against `tulip-core` already takes 1-2 hours on a 4-vCPU runner. Adding `tulip-storage` (3.3x the LOC, longer per-test wall-clock due to DB I/O) would extend that to most of a workday. We'll add it as a separate weekly job once a `tulip-core` baseline is established and the workflow's overhead is well-understood.

The other packages (`tulip-api`, `tulip-cli`, `tulip-ai`, `tulip-importers`, `tulip-reports`) are explicitly out of scope. Their correctness is largely about plumbing — request/response shapes, command parsing, prompt construction, file format handling. Mutation testing returns less leverage there than against the math/posting code.

### 3. Cadence: weekly, not per-PR

`.github/workflows/mutation.yml` runs on a Sunday-morning cron and is also `workflow_dispatch`-able. Per-PR mutation testing is rejected: 1-2h CI runs per PR would dominate the GitHub Actions allotment and slow the slice-per-PR rhythm to a halt.

Surviving mutants do not fail the workflow. The job's purpose is to *report*, not gate — surviving mutants are a maintenance signal, not a release blocker. (Per-PR gating may revisit when/if mutmut's incremental cache makes cheap re-runs feasible.)

### 4. Reporting: tracking issue + artifact

Each weekly run:

1. Uploads the report as a workflow artifact (90-day retention, GitHub default).
2. Comments on the open *Mutation testing — surviving mutants* tracking issue, or opens one if none exists.

A single tracking issue keeps the history of weekly runs in one place and makes "regressed mutation score" visible without having to dig through workflow runs.

### 5. Initial baseline

The first run on `main` after this ADR lands establishes the baseline. The expectation is **non-zero surviving mutants** on the first run. Surviving mutants will be triaged in two buckets:

- **Real test gaps** — file follow-up issues, address them.
- **Equivalent mutants** — semantically identical to the original (e.g. `range(n)` vs `range(0, n)`); document and accept. mutmut doesn't have a built-in suppression mechanism, but a comment in the corresponding test or source file documents the rationale.

We don't pre-commit to a numeric mutation-score gate. Whether to introduce one (and where to set it) is a question for after we see the first three or four weekly reports.

## Consequences

### Positive

- Tests that would silently pass against a flipped sign or off-by-one in `Money` arithmetic, period boundary checks, balance invariants, or allocation pool math will now surface as surviving mutants.
- The weekly cadence makes regressed test sensitivity visible without slowing PR review.
- ADR exists, so the next contributor doesn't have to reverse-engineer why mutation testing isn't gating PRs.

### Negative

- The first few weekly runs will surface surviving mutants that are real test gaps. Closing them is work.
- Adding `tulip-storage` later requires a second workflow file (or a matrixed job) and a separate baseline.
- The 1-2h runtime is non-trivial GitHub Actions minutes — single-digit-percent of a free-tier monthly allotment per run.

### Neutral

- mutmut's mutator set is fixed; we accept what it produces. If the mutator set ever proves insufficient (rare), the cosmic-ray escape hatch is open.

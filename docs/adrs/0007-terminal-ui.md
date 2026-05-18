# ADR-0007: Terminal UI (TUI) as Phase 9 — an additive Textual client

**Status:** Accepted (2026-05-14). **Phase 9 v1 shipped 2026-05-17 → 2026-05-18.**

## Phase 9 v1 implementation note (2026-05-18)

The read-only scope this ADR proposes is now in tree on `main`. Five
slices merged against umbrella [#309](https://github.com/rmwarriner/tulip-accounting/issues/309):

- **P9.0** — `packages/tulip-tui/` workspace skeleton + pilot-mode
  smoke test + architecture-boundary test (PR #382 predecessor;
  P9.0 itself landed earlier as the skeleton commit).
- **P9.1** — accounts browser (PR #383 → `7bb52a0`).
- **P9.2** — transactions register with account drill-in
  (PR #384 → `164424a`).
- **P9.3** — reports viewer over the eight `/v1/reports/*` reports
  (PR #385 → `32e456b`).
- **P9.4** — reconciliations + import-batches browse
  (PR #386 → `0bd5d32`).

App-wide bindings as shipped: `q` quit · `p` reports · `c` reconcile ·
`i` imports · `enter` (on account row) → transactions · `escape` pop ·
`r` refresh. Mutation surfaces (categorize / split / edit /
reconcile-action / apply-import) stay on the CLI for v1 as scoped; a
follow-up phase will pull them into the TUI once the read surfaces
have soaked. The ADR's "rejected for now: web / desktop GUI" stance is
unchanged.

**Date:** 2026-05-14

## Context

The Typer CLI (`tulip-cli`, Phase 3) was built as two things at once: the
first useful interface *and* a test-bed that forced every API endpoint to
be exercised from outside the server process. It did that job. By Phase 7
close the API surface is complete and proven; Phase 8's deep security and
privacy audits reviewed a stable system.

What surfaced during real use — a maintainer migrating their own data in
from another accounting tool — is that the CLI is a good *scripting*
surface and a clumsy *human review* surface. The friction was concrete:
UUIDs in argument positions and table cells, no interactive pickers,
multi-step flows that want to be one. Most of those specific issues were
fixed in the Phase 8 post-audit usability bundle (#197, #213, #214, #273,
#205, …) — but the fixes are presentation patches on an interface whose
shape is fundamentally "one command, one round-trip, print and exit."
Reviewing 128 imported transactions, or working a reconciliation, wants a
*navigable* surface, not a sequence of `list` / `show` / `edit`
invocations.

The question raised: build a GUI? Two things were conflated — "a pretty
graphical app" (web/desktop, design-heavy, a solo-maintainer tar pit,
and a new network attack surface that would have to wait behind Phase 8/
the Phase 10 pre-cloud re-audit) versus "a less clumsy interface." The
second doesn't require the first.

## Decision

Build a **terminal UI** as **Phase 9**, using [Textual](https://textual.textualize.io/).
It is an **additive client**: the CLI stays exactly as it is — the
scriptable / automation surface and the test-bed, with its `--json`
contract intact. The TUI talks to the same HTTP API the CLI does. No
backend changes, no new endpoints required, no new attack surface.

Three scoping decisions, made explicitly:

1. **Sequencing — Phase 9, after Phase 8 wraps.** Phase 8 (hardening) is
   in flight: privacy Wave-1 (#236–#243), the docs pass, and the
   performance pass are still open. The TUI does not jump the queue;
   hardening finishes first. Cloud preparation, formerly Phase 9, becomes
   **Phase 10**. (The 2026-05-12 security audit predates this renumber
   and refers to cloud-prep as "Phase 9"; it is a dated record and is not
   rewritten — read its "Phase 9" as "the pre-cloud phase.")

2. **v1 TUI scope — read / browse only.** The first shippable TUI is
   navigable, *non-mutating* views: account browser, transaction
   register, reports, reconciliation status, import batches. No edits, no
   posting, no reconcile actions in the first cut. Mutations land in
   later Phase 9 slices once the navigation shell and the read surfaces
   are proven. This keeps the first slices low-risk and gets the
   review-fatigue relief — which is the actual pain — soonest.

3. **The CLI is not deprecated.** It remains the supported scriptable
   interface. The README's "scriptable CLI client" value proposition
   stands. The TUI is the comfortable *human* surface; the CLI is the
   *automation* surface. Both are first-class.

## Why Textual

- **Stays in the toolchain.** Pure-Python, installs through `uv`, no
  new language or build system. A new workspace package `tulip-tui`
  slots in next to the existing seven.
- **Testable with the existing discipline.** Textual ships a headless
  *pilot* mode — apps can be driven and asserted programmatically with
  no real terminal. TDD is mandatory here as everywhere; the pilot
  harness is how. CI gets a `tulip-tui` shard like every other package.
- **Right-sized for the deployment.** Tulip is a single-machine,
  self-hosted system the user runs in a terminal already. A
  terminal-native UI fits that without introducing a browser, a
  bundler, or in-browser auth.
- **No new attack surface.** The TUI is an API client. It does not
  change what is exposed; a web GUI would (browser auth, CORS, static
  asset serving) and would have to wait behind the Phase 10 pre-cloud
  re-audit. The TUI does not.

## Alternatives considered

- **A web / desktop GUI now.** Rejected for Phase 9. It is a larger
  build, a real design effort, and a new attack surface that the
  hardening track has not cleared. It is also the thing the CLI-as-
  forcing-function was deliberately built to avoid prematurely. Not
  rejected forever — it is a candidate for a post-Phase-10 phase, built
  on a hardened, Postgres-capable backend, if a non-technical-household-
  member use case actually materialises.
- **Raw `prompt-toolkit` / `curses`.** Rejected. Lower-level than the
  job needs; Textual's widget model, layout, and — decisively — its
  headless test harness are exactly what keeps a TUI inside the
  project's TDD discipline instead of becoming an untested corner.
- **Do nothing; keep patching the CLI.** Rejected. The presentation
  patches (#197, #213, #214, …) were worth doing and are done, but they
  do not change that the CLI's interaction model is request-print-exit.
  Review fatigue is a structural property of that model, not a bug in
  it.

## Consequences

- **New dependency:** `textual` and its dependency tree, scoped to the
  new `tulip-tui` package. `pip-audit` / `dependency-audit` CI coverage
  extends to it.
- **New workspace package:** `packages/tulip-tui/`, an API client like
  `tulip-cli`. The architecture-boundary tests extend: `tulip-tui` must
  not import server or storage internals — talking to the API is the
  only allowed channel, identical to the rule `tulip-cli` already lives
  under.
- **New CI shard:** `Test (tulip-tui)` joins the per-package matrix
  (ADR-0006). Pilot-mode smoke tests boot and quit the app headlessly.
- **Coverage gate** applies unchanged (85% project). The TUI's
  rendering layer is exercised through pilot-mode tests; the
  API-client layer is shared logic and tested directly.
- **Docs:** Phase 9 gets its own `PHASE_STATUS.md` section and
  `ARCHITECTURE.md §10` entry. A future ADR may be warranted if the
  TUI needs to drive the API in a way the current endpoints do not
  support — but the read-only v1 scope is specifically chosen so that
  is unlikely.
- **The renumber:** cloud preparation moves Phase 9 → Phase 10 in the
  living docs (`ARCHITECTURE.md`, `PHASE_STATUS.md`, `THREAT_MODEL.md`).
  The dated audit documents under `docs/audits/` are not rewritten.

## Status update mechanism

Update this ADR when:

- Phase 9 work begins (record the first slice + the `tulip-tui` package
  landing).
- The read-only scope is extended to mutations (record which actions,
  and the slice).
- A web/desktop GUI is ever scoped — that is a *new* ADR that
  supersedes the "rejected for now" stance here, not an edit to this
  one.

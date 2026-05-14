# ADR 0005 — AI integration design + data-flow contract

**Status:** Proposed (2026-05-11) — to be reviewed before any Phase 6 code lands.
**Phase:** 6 (AI integration).
**Closes:** [#102](https://github.com/rmwarriner/tulip-accounting/issues/102) (AI provider data-flow contract — privacy audit entry criterion).
**Supersedes:** None. Builds on [ADR-0004](0004-reconciliation.md) §Q2 ("learned categorizer is Phase 6 work plugging into the existing `Categorizer` DI seam") and on [ARCHITECTURE.md §6](../ARCHITECTURE.md) (AI section) + [THREAT_MODEL.md §5.3](../THREAT_MODEL.md) (Phase 6 constraints).

---

## Context

[ARCHITECTURE.md §6](../ARCHITECTURE.md) sketches Phase 6 in four bullets — adapter layer via litellm, four capabilities (categorize → NL query → forecast → agentic), three policies (permissive/requires_approval/disabled), and an `ai_invocations` audit table. [§10 audit cadence](../ARCHITECTURE.md) commits the project to a **privacy audit before Phase 6 implementation**, on the grounds that household financial data starts leaving the local boundary here and the audit shapes the design rather than reviewing it after. [THREAT_MODEL.md §5.3](../THREAT_MODEL.md) lists five Phase 6 constraints the audit must respect (prompt bodies not logged by default; redaction before the litellm call; no silent provider fallback; `actor_kind=ai_agent` rows for state-changing proposals; cost / rate caps enforced server-side, not in the prompt).

What the sketch and the threat-model checkpoint leave open:

1. **Module structure.** Where does `tulip-ai` live in the workspace, what depends on what, and what is pure (testable without a live provider)?
2. **Provider adapter + BYOK surface.** How are API keys supplied, scoped, and stored; what does the per-household / per-user precedence look like in practice?
3. **Per-capability data-flow contract.** For each capability, *exactly* what data class lands in the prompt body and what does not?
4. **Redaction profiles.** What does "default" send; what does "strict" suppress; what does "preview" show the user; how is the preview an honest representation of the live call?
5. **Policy resolution.** When a household sets `requires_approval` and a user sets `disabled` for the same capability, who wins? (Per ARCHITECTURE.md §6.3 the user ratchets up, never down — but the resolution function isn't written.)
6. **Audit-log shape.** `ai_invocations` columns: which are mandatory, which are nullable, what's stored on a redacted-prompt vs. a recorded-prompt run?
7. **Cost cap + rate limit enforcement.** Pre-call check vs. post-call charge; what happens at the cap (hard fail vs. degrade to local-only); per-user vs. per-household.
8. **Failure modes.** "No silent fallback" is locked. How does that interact with the configured `fallback_provider` in `households.ai_policy`? What is the user-visible behaviour when the cloud provider 5xxes?
9. **Slice ordering.** P6.1 ships *something*. What is it, and what's deliberately not in it?

ADR-0004 set a precedent of resolving these in an ADR before code; this ADR continues that discipline. It is opinionated — where a knob would otherwise sprout (per-field redaction, scalar policy weights, hot-swap provider fallback), the choice is "decide a default, leave it private to the AI module, revisit on the first real complaint," not a tunable.

Two pieces of existing infrastructure constrain the answers:

**The `Categorizer` Protocol is already in `tulip-core` (P5.3).** `packages/tulip-core/src/tulip_core/reconciliation/categorizer.py` defines `async def categorize(line, household_context) -> CategorizationResult`, the module-global `_REGISTERED` registry, and the `NullCategorizer` v1 default. Phase 6 *does not* invent a new categorizer API; it ships an `AICategorizer` that implements the existing Protocol and calls `register_categorizer(...)` at app startup.

**The encryption + audit surfaces are in place.** Field-level encryption via `tulip_storage.encryption.encrypt_field` (#132 master key flow) is the same one that protects TOTP secrets and is the natural home for storing per-household API keys. The `audit_log` table already has `actor_kind` (user / system / ai_agent / importer) so AI-as-actor is wired pending an `ai_agent` row.

## Decision

The structure below answers the nine questions in order. Where a worked example clarifies, one is given.

### Q1 — Module structure

A new `tulip-ai` workspace package. **It depends on `tulip-core` and `tulip-storage` only**, never on `tulip-api`. Adapter code and policy resolution are async-by-default; nothing in `tulip-ai` opens an HTTP connection without going through the central `provider_call` chokepoint (Q2).

```
packages/tulip-ai/
└── src/tulip_ai/
    ├── __init__.py
    ├── adapters/
    │   ├── __init__.py
    │   ├── base.py             # ProviderAdapter Protocol — single method `chat()`
    │   └── litellm_adapter.py  # the only adapter in v1; routes by provider name
    ├── capabilities/
    │   ├── __init__.py
    │   ├── categorize.py       # AICategorizer (implements tulip_core.Categorizer)
    │   ├── nl_query.py         # P6.2
    │   ├── forecast.py         # P6.3
    │   └── agentic.py          # P6.4
    ├── policy.py               # AIPolicy + resolve_policy(household, user, capability)
    ├── redaction.py            # PromptRedactor + redaction profiles
    ├── audit.py                # AIInvocationWriter — single chokepoint for ai_invocations rows
    ├── cost.py                 # cost-cap + rate-limit accounting
    ├── preview.py              # PreviewRequest / render_preview — "what would we send"
    └── errors.py               # AIProviderError, AICapDisabled, AIRateLimited, AICostCapped
```

What's deliberately *outside* `tulip-ai`:

- **The HTTP / API endpoints** that expose AI proposals to the CLI live in `tulip-api` (`tulip_api/routers/ai.py`, lands with P6.5). `tulip-ai` is invocation-style — given a categorize/query/forecast request, do it. It does not own HTTP.
- **The CLI surface** (`tulip ai preview`, `tulip ai status`, etc.) lives in `tulip-cli/commands/ai.py`. Same dependency direction.
- **The architecture test** banning AI access from `tulip-importers` (existing — see #126 work) stays in place. Importers call `get_categorizer()` from `tulip-core`; whether that returns `AICategorizer` or `NullCategorizer` is decided in `tulip-api`'s app factory at startup.

The dependency graph is one-direction and verifiable by the existing architecture tests:

```
tulip-core   ←── tulip-storage   ←── tulip-ai   ←── tulip-api / tulip-cli
              │                  ↑
              └──────── tulip-importers (no AI dep)
```

`tulip-ai` *may* import from `tulip-storage` because the policy resolver reads `households.ai_policy` and the audit writer writes `ai_invocations`. It *must not* import from `tulip-api` (test enforces).

### Q2 — Provider adapter (litellm) + BYOK surface

**One adapter in v1: `LitellmAdapter`.** litellm is the uniform call surface across Anthropic, OpenAI, Google, Ollama, and OpenAI-compatible endpoints. The `ProviderAdapter` Protocol exists so future direct-SDK adapters (e.g. an Anthropic-native one we'd add if litellm becomes a bottleneck) don't change the call site — they implement the Protocol.

```python
@runtime_checkable
class ProviderAdapter(Protocol):
    async def chat(
        self,
        *,
        provider: str,    # "anthropic", "openai", "google", "ollama", "openai-compatible"
        model: str,
        api_key: str | None,  # None for local-only (ollama)
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float = 0.0,
    ) -> ProviderResponse:
        """Issue one synchronous LLM call. Raises AIProviderError on any failure."""
```

`ProviderResponse` is a frozen dataclass: `text`, `tokens_in`, `tokens_out`, `latency_ms`, `cost_estimate_usd`, `provider_response_id` (for support correlation).

**Key storage.** Per-household API keys are stored on `households.ai_keys_encrypted` — a JSON blob keyed by provider, field-encrypted with the master key the same way `users.totp_secret_encrypted` is today. Per-user override is `users.ai_keys_encrypted` (same shape; same field-encryption). Resolution order at call time:

1. User's per-provider key (if set).
2. Household's per-provider key (if set).
3. Environment variable `TULIP_AI_KEY_{provider}` (if set; intended for dev/single-user installs).
4. **Refuse to boot the call** — no fallback to the default `provider`'s key from a *different* household. This is the locked "no key" path.

The CLI surface for managing keys:

- `tulip ai set-key --provider anthropic --scope household` reads the key from stdin (`getpass.getpass`-style; never on argv), writes to `households.ai_keys_encrypted`.
- `tulip ai set-key --provider anthropic --scope user` writes to `users.ai_keys_encrypted`.
- `tulip ai forget-key --provider anthropic --scope {household,user}` deletes.
- `tulip ai list-keys` shows providers that have a key set, without showing the keys themselves (just `set | not set | inherits from household`).

**Why not load keys from env in production?** The env path is convenient for tests and single-user installs; production multi-user deploys should use the DB so a household admin's key change propagates to every user without a service restart. Documenting this in QUICKSTART §key-management when P6.1 ships.

### Q3 — Per-capability data-flow contract

Each capability has a documented prompt body. This subsection is the **authoritative contract** — any code change that adds a field to a prompt body must update this table and bump the redaction tests.

**Capability: Categorize.** One statement line at a time.

| Field | Default sends | Strict sends | Notes |
|---|---|---|---|
| `description` (string from OFX `<NAME>`/`<MEMO>` or CSV row) | Yes — raw | Yes — token-redacted (see Q4) | The whole point of categorize. Strict bucketed-token redaction breaks vendor names but keeps category-discriminating tokens. |
| `amount` (Decimal as string) | Yes — exact | Yes — order-of-magnitude bucket (e.g. `$10–100`) | Amount disambiguates "Amazon books" from "Amazon car parts". Strict trades accuracy for privacy. |
| `posted_date` (ISO date) | Yes | Yes | Day-of-week / season helps for some categories; never identifies a person. |
| `currency` | Yes | Yes | Required for the model to choose USD-priced vs EUR-priced category. |
| `chart_of_accounts` (list of `{code, name, type}` from the household) | Yes — full | Yes — full | The model needs the menu to choose from. Strict can't redact this without breaking the capability. Households who don't want their account labels seen should use `disabled` (Q5). |
| `recent_examples` (5–10 prior categorized lines from the same household, payee + chosen code) | Yes | No | "Few-shot" examples improve accuracy. Strict suppresses them — model sees only the chart. |

**Categorize does NOT send:** transaction id, raw OFX fitid, account number, account name (only the chart-of-accounts menu), bank name, user email, household name, or any other transaction's amount/description except the explicit `recent_examples` set.

**Capability: NL query.** Phase P6.2.

| Field | Default sends | Strict sends | Notes |
|---|---|---|---|
| `question` (user's natural-language question) | Yes — raw | Yes — raw | The question is the input; can't be redacted. |
| `schema_card` (DDL summary of the read-only AI view) | Yes | Yes | Tables, columns, types. No values. |
| `sample_rows` (5 rows from each table for grounding) | Yes — redacted | No | Sample rows always run through the redactor; in `strict`, none ship. |
| `query_result_rows` (rows from the SQL the AI emitted, sent back for summarisation) | Yes — redacted | Yes — redacted | The AI's own output flows back as input for summarisation; the redactor runs on both directions. |

NL query is **two-shot**: turn 1 = AI emits SQL; turn 2 = AI summarises the query result. Both turns flow through the redactor. The SQL itself is logged to `ai_invocations` (no PII in the SQL; just structure).

**Capability: Forecast.** Phase P6.3.

| Field | Default sends | Strict sends | Notes |
|---|---|---|---|
| `envelope_id`, `envelope_name` | Yes | Yes — id only | Need a label to talk back to user; strict elides the name. |
| `time_series` (90 days of daily balances) | Yes — bucketed to 5% | Yes — bucketed to 25% | Trend matters; exact amounts don't. |
| `target_amount`, `target_date` | Yes | Yes | For sinking funds. |
| `recent_inflow_average` | Yes — bucketed | Yes — bucketed | |

**Capability: Agentic.** Phase P6.4.

| Field | Default sends | Strict sends | Notes |
|---|---|---|---|
| `proposal_context` (what the user asked the agent to do, in natural language) | Yes | Yes | User-supplied. |
| Affected entities (account / envelope / transaction shapes) | Yes — redacted | Yes — redacted | Same redactor; agentic always operates on redacted state and the user reviews the *concrete* proposal before approval. |
| Proposal result (the agent's emitted plan) | Yes — into the audit log | Yes — into the audit log | This is the state-changing part. Stored verbatim in `ai_invocations.proposal_json` for forensics, regardless of redaction profile. |

#### Worked example — categorize, default profile

Statement line:

```
2026-05-03  WHOLE FOODS MARKET   -87.42 USD
```

Chart: `1010 Checking (asset), 5100 Groceries (expense), 5200 Rent (expense), 5300 Fuel (expense), 5400 Dining (expense), 4000 Salary (income)`.

What goes to the provider, after JSON-encoding:

```json
{
  "task": "categorize",
  "line": {
    "description": "WHOLE FOODS MARKET",
    "amount": "-87.42",
    "currency": "USD",
    "posted_date": "2026-05-03"
  },
  "chart": [
    {"code": "5100", "name": "Groceries", "type": "expense"},
    {"code": "5200", "name": "Rent",      "type": "expense"},
    {"code": "5300", "name": "Fuel",      "type": "expense"},
    {"code": "5400", "name": "Dining",    "type": "expense"}
  ],
  "recent_examples": [
    {"description": "TRADER JOE'S", "code": "5100"},
    {"description": "SHELL GAS",    "code": "5300"}
  ]
}
```

Note the chart filters to expense accounts only (categorize never proposes posting an asset code); income / liability codes are out of the menu by construction. Account id, household name, email, and anything else identifying the user is absent.

### Q4 — Redaction profiles + preview surface

**Three profiles** — `default`, `strict`, `local_only`. The profile is chosen per-capability (in `households.ai_policy`); the `local_only` profile pins to an Ollama provider regardless of `default_provider`.

The redactor (`tulip_ai/redaction.py`) is a pure function operating on prompt-shaped dicts:

```python
class PromptRedactor:
    def __init__(self, profile: Literal["default", "strict", "local_only"]) -> None: ...
    def redact(self, prompt: PromptPayload) -> RedactedPromptPayload: ...
```

`PromptPayload` is a frozen dataclass per capability (`CategorizePromptPayload`, `NLQueryPromptPayload`, ...). The redactor strips / buckets fields per the contract in Q3. `local_only` is a special case: passes the payload through unchanged (no provider sees it unless Ollama is the configured local provider) and asserts the resolved provider's `is_local` flag is true.

**Preview surface.**

- `tulip ai preview categorize --line LINE_UUID` — fetches the line, builds the prompt payload, runs the redactor, prints the JSON the provider would receive. Does not call the provider. Exit 0.
- `tulip ai preview nl-query --question "..."` — same, for NL queries. The SQL the AI *would* emit isn't predictable from the CLI; the preview shows only the first-turn payload.
- `tulip --json ai preview ...` — emits the same JSON to stdout for piping into `jq` / external review.
- API: `POST /v1/ai/preview` returns the same shape. Admin-only — the preview shows the household's chart of accounts and recent examples.

The preview must be a **byte-faithful representation** of what the live call would send. A test (`tulip_ai/tests/test_preview_byte_faithful.py`) constructs a request, runs both `preview()` and the real `categorize()` against a recording adapter, and asserts the recorded prompt equals the preview output.

### Q5 — Policy resolution

`households.ai_policy` (JSON, per ARCHITECTURE.md §6.5) is the **floor**: it constrains what users in the household can do. Per-user settings can **ratchet up** (more cautious than household policy) but cannot ratchet down.

Resolution function:

```python
def resolve_policy(
    household_policy: HouseholdAIPolicy,
    user_policy: UserAIPolicy,
    capability: Capability,
) -> ResolvedPolicy:
    """Return the effective policy for ``capability`` for this user.

    Severity ordering: disabled > requires_approval > permissive.
    The resolved policy is the max() of household and user severity.
    """
```

Worked examples (`H = household`, `U = user`, `R = resolved`):

| H | U | R | Behaviour |
|---|---|---|---|
| `permissive` | (unset) | `permissive` | Capability runs without per-action approval. |
| `permissive` | `requires_approval` | `requires_approval` | User wants to confirm each call, even though household says it's fine. |
| `requires_approval` | `permissive` | `requires_approval` | **User cannot ratchet down.** Household's caution wins. |
| `disabled` | `permissive` | `disabled` | Capability is unavailable to this user. |
| `requires_approval` | `disabled` | `disabled` | User opts out of a capability the household uses. |

If `resolved == disabled`, `tulip-ai`'s capability functions raise `AICapDisabled` immediately — no preview, no audit row, no provider call. The CLI maps that exception to a stable exit code.

### Q6 — Audit log shape

A new table `ai_invocations`, written by `tulip_ai.audit.AIInvocationWriter`. Architecture test bans direct INSERTs anywhere else (mirrors the `shadow_postings` writer-chokepoint pattern from ADR-0001).

| Column | Type | Notes |
|---|---|---|
| `household_id` | UUID | PK leg, FK |
| `id` | UUID | PK leg |
| `created_at` | DateTime(tz=True) | Server clock |
| `actor_user_id` | UUID NULL | The user who triggered the call (null for scheduled / system) |
| `capability` | enum | `categorize` / `nl_query` / `forecast` / `agentic` |
| `policy_resolved` | enum | `permissive` / `requires_approval` / `disabled` (recorded post-resolution) |
| `profile` | enum | `default` / `strict` / `local_only` |
| `provider` | str | `anthropic` / `openai` / `google` / `ollama` / `openai-compatible` |
| `model` | str | e.g. `claude-opus-4-7`, `gpt-5`, `llama3.1:70b` |
| `tokens_in` | int | From provider response |
| `tokens_out` | int | From provider response |
| `cost_estimate_usd` | Decimal | Computed from a static rate table per provider; recomputed if rates change |
| `latency_ms` | int | |
| `outcome` | enum | `success` / `provider_error` / `redacted_only_preview` / `policy_disabled` / `rate_limited` / `cost_capped` |
| `provider_response_id` | str NULL | Provider-issued correlation id when present |
| `request_id` | UUID NULL | Tulip's request id (joins back to `audit_log` rows) |
| `prompt_hash` | bytes(32) | SHA-256 of the *redacted* prompt payload — survives a prompt-recording-disabled run |
| `prompt_json` | text NULL | The redacted prompt payload, **only stored if `households.ai_policy.log_prompts == true`** (default: false). Even when stored, runs through the same redactor — no opt-out of redaction itself |
| `response_text` | text NULL | The provider's textual response, same opt-in as `prompt_json` |
| `proposal_id` | UUID NULL | FK to `pending_proposals` when `capability == agentic`. Always populated for agentic; the proposal lifecycle joins on this |

**Default `log_prompts=false`** is the locked decision from THREAT_MODEL.md §5.3. Operators who want full forensic logs flip the flag knowing what they're opting into. `prompt_hash` is always populated — gives "did the same prompt go out twice" answerable without storing prompts.

### Q7 — Cost cap + rate limit enforcement

**Cost cap is pre-call.** Before the litellm `chat()` issues, `tulip_ai.cost.check_and_reserve(household_id, capability, estimated_cost_usd)` runs. If reservation would exceed `households.ai_policy.monthly_cost_cap_usd`, the capability raises `AICostCapped` and `ai_invocations.outcome == "cost_capped"`. No provider call happens.

Estimated cost is a heuristic from token counts × per-million rate. Post-call, the row's `cost_estimate_usd` is updated with the actual; the reservation is released and the actual is debited. Atomic via a single transaction that writes the `ai_invocations` row.

**Rate limit is per-user, sliding window.** Default 60 invocations/hour per user across all capabilities. Sliding window because monthly aggregates obscure runaway loops; the bound is "no more than a hot stove" not "no more than a budget."

**At cap.** Per `households.ai_policy.cost_cap_behaviour` (new field, default `degrade`):

- `degrade` — capability swaps to the resolved `fallback_provider` from §6.5 (typically Ollama). If that provider also fails or isn't configured, raises `AICostCapped`. **This is the only place a provider swap is permitted** — explicit, audited, and triggered by a budget signal the user set.
- `hard_fail` — raises `AICostCapped`. No fallback attempted. For households that want predictable spend.

The non-locked `fallback_provider` swap is *not* a "silent fallback" — it's an explicit cost-cap signal, logged with `outcome=success` but `provider=ollama` (not the configured cloud). The THREAT_MODEL §5.3 constraint ("no silent provider fallback") applies to *provider errors*, not cost caps. Q8 expands.

### Q8 — Failure modes

**Provider errors.** When the configured cloud provider returns a 5xx or times out:

- Default behaviour: the capability raises `AIProviderError`. The CLI renders it as `ai.provider_error` Problem Details (RFC 9457). `ai_invocations.outcome == "provider_error"`. **No fallback attempted.** This is the locked "no silent failover" rule.
- The user can retry. The user can switch provider via `tulip ai config set --capability categorize --provider openai`. Both paths are explicit.
- A future feature (deferred to Phase 7+): `auto_retry_on_5xx` with backoff against the *same* provider. Not in v1.

**Provider returns garbage.** A model hallucinates a category code that doesn't exist in the household's chart. The capability validates the response against a structured schema; on validation failure raises `AIProviderError` with `outcome="provider_error"` (model treated as service-level broken). User does not see the garbage; they see "provider returned an unparseable response."

**Network down.** Treated as a provider error.

**Local provider (Ollama) not running.** Treated as a provider error. The CLI's `tulip doctor` (#135) does not currently probe Ollama; an open question for whether it should is tracked as a Phase 6 follow-up.

### Q9 — Slice ordering

| Slice | What ships | Issue ref |
|---|---|---|
| **P6.0** | This ADR. Privacy audit / data-flow contract sections of `docs/THREAT_MODEL.md` updated to point at this ADR as the authoritative contract. No code. | #102 |
| **P6.1** | `tulip-ai` package skeleton with `LitellmAdapter`, `PromptRedactor`, `AIInvocationWriter`, `AICategorizer` implementing `tulip_core.Categorizer`. New migration for `ai_invocations`, `households.ai_keys_encrypted`, `users.ai_keys_encrypted`, `households.ai_policy` (column already sketched in §6.5 — actual migration here). `tulip ai {set-key, forget-key, list-keys, config, status, preview}` CLI surface and `POST /v1/ai/preview` API endpoint. End-to-end test: register household → set Anthropic key → import OFX → `imports apply` proposes categories from the AI categorizer → user accepts. | new |
| **P6.2** | NL query (`tulip ai ask "question"` and `POST /v1/ai/ask`). Read-only AI view (`ai_view_transactions`, `ai_view_envelopes`, `ai_view_accounts` — views over the canonical tables that omit encrypted columns). Two-turn flow per Q3. Sample rows redacted via the same `PromptRedactor`. | new |
| **P6.3** | Forecasting: nightly scheduler job (via the runner ADR-0002 primitive) that emits anomaly and runout notifications to a new `notifications` table. `tulip notifications list`. | new |
| **P6.4** | Agentic proposals: `pending_proposals` table, `tulip ai propose / approve / reject`, `actor_kind=ai_agent` audit rows on approve. | new |
| **P6.5** | `tulip ai` consolidation + cost-cap behaviours UI polish + an opt-in `log_prompts` toggle in the CLI. Closes Phase 6. | new |

P6.1 is the high-risk slice — it lays down the redactor, the audit writer, the cost-cap chokepoint, and the policy resolver. P6.2/3/4 add capabilities on top of stable scaffolding. P6.5 is polish.

## Consequences

### Positive

1. **The `Categorizer` Protocol from P5.3 doesn't change.** `AICategorizer.categorize(line, household_context) -> CategorizationResult` plugs into the existing seam; the importer code doesn't know AI exists.
2. **The redactor is pure.** `PromptRedactor.redact(payload) -> redacted_payload` is a hypothesis-property-test surface (round-trip identity for fields that pass through, byte-equality for the same input under the same profile, no PII fields present in `strict` output). No live provider needed.
3. **The audit-writer chokepoint mirrors the shadow-ledger pattern.** Same architecture-test discipline ensures every AI call produces an `ai_invocations` row.
4. **`tulip ai preview` is byte-faithful.** A user evaluating "should I enable AI for my household?" can see exactly what each capability sends *before* a single call is made. The byte-faithful test guarantees the preview doesn't lie.
5. **Policy resolution is one function.** Tests cover the matrix (Q5) exhaustively. UI just shows the resolved state; users don't have to mentally simulate the precedence.
6. **No silent provider fallback** is structurally enforced — the only fallback path (cost-cap `degrade`) explicitly records `provider=ollama` in the audit row, which is greppable.
7. **BYOK with field encryption** means a key compromise from a separate vector (DB leak) doesn't surface plaintext keys; the master-key story already documented in #132 covers them.

### Negative

1. **Five new tables / columns.** `ai_invocations`, `pending_proposals` (P6.4), and three column additions to existing tables (`households.ai_policy`, `households.ai_keys_encrypted`, `users.ai_keys_encrypted`). Migration cost is real but bounded.
2. **`recent_examples` in the categorize prompt leaks past categorisation decisions.** A household member whose AI privacy threshold is high should set the capability to `strict` (suppresses examples) or `disabled`. The contract makes this explicit; the architecture cost is one row in the contract table.
3. **`fallback_provider` is a non-obvious surface.** Users may set `fallback_provider=ollama` expecting it to kick in on provider 5xx (it doesn't — that's the locked rule) and only realising it kicks in on cost-cap when their monthly spend creeps up. P6.5 documents this explicitly in `tulip ai status`'s output.
4. **litellm is a real dep.** ~50 MB once provider SDKs are pulled. Acceptable for a Phase 6 inflection.
5. **Per-capability redaction tables** in the contract (Q3) need to be maintained as features evolve. The redaction byte-faithful test catches drift between the contract and the code; the contract itself is human-maintained.

### Neutral

1. **`PromptPayload` is in `tulip-ai`, not `tulip-core`.** The reverse-dependency direction matters — `tulip-core` is the pure domain layer; AI prompts are infrastructure. The `Categorizer.categorize` Protocol stays in `tulip-core` (it doesn't know about prompts); the `AICategorizer` implementation owns the prompt construction inside `tulip-ai`.
2. **No SQL-in-database for NL query yet.** P6.2 ships the read-only view + the AI-emitted-SQL execution path; we don't pre-define a query DSL because the model is plenty capable of writing SQL against a documented schema. Schema-cards are pinned in the prompt.
3. **`prompt_json` defaulting to NULL** means operators wanting full forensic logs have to opt in. The `prompt_hash` column gives "was the same prompt sent twice" answerable without prompt storage; the trade-off favours privacy by default.
4. **API keys live in two tables (household + user).** The two-place storage is the smallest expression of "users can override household for their own actions"; a per-key precedence helper resolves at call time. Tests cover the precedence.

### Retention of `ai_invocations` (added #243, deep privacy audit H-16)

`ai_invocations` is append-only at the *writer* — but not unbounded. Two lifecycle controls were added post-audit:

- **Consent-withdrawal scrub.** Flipping `households.ai_policy.log_prompts` from `true` to `false` via `PUT /v1/ai/config` runs a household-scoped `UPDATE` that nulls `prompt_json` + `response_text` on every row — atomically in the same commit as the policy change (GDPR Art. 17(1)(b)). The row, `prompt_hash`, and cost metadata survive for the audit chain; the scrub itself is recorded as an `audit_log` row (`action="ai.prompt_log_scrubbed"`).
- **TTL garbage collection.** The `ai_retention` scheduled handler deletes non-proposal-linked `ai_invocations` older than `AI_INVOCATION_RETENTION_DAYS` (90). A row is preserved while any `pending_proposals` row references it via `pending_proposals.ai_invocation_id`; once the proposal is gone (e.g. rejected + deleted per #240) the invocation becomes collectable. This also bounds the accumulation of pseudonymous `prompt_hash` rows even for households that never enable `log_prompts`. The policy is surfaced read-only in `GET /v1/ai/config` (`invocation_retention_days`) and `tulip ai config show`.

## Alternatives considered

### Q1 — `tulip-ai` lives inside `tulip-api`

Considered: skip the workspace package, put `tulip_api/ai/...` inside the API. Rejected for the same reason `tulip-importers` is its own package — the architecture test enforces "tulip-core has no I/O deps" cleanly when the AI layer is at the same arm's length as importers. Cross-package tests for the `Categorizer` DI seam also stay simpler when the AI package is independent.

### Q2 — Per-tenant API keys in env vars, no DB storage

Considered: documentation-only approach where each household admin sets `TULIP_AI_KEY_ANTHROPIC` in the docker-compose env and that's it. Rejected: multi-user households need per-user override (the architecture's locked feature). DB storage with field encryption is the smallest expression that supports both.

### Q3 — Single redaction profile, no `default` vs `strict` split

Considered: ship `default` only; users who want stricter can `disabled` the capability. Rejected: discards a real middle-ground use case (households OK with AI seeing their *category* labels but not their *amounts*). Two profiles cover the spectrum without becoming a per-field tuning surface.

### Q3 — Send the chart of accounts redacted (just `5100, 5200`, no names)

Considered: hide all label text from the model. Rejected: the model needs the labels to make a useful proposal (and an English-language "Groceries" is the entire reason the model is in the loop — a sufficiently-capable rule engine could do code-only matching). Users who don't want account labels seen should use `disabled` (or `local_only`).

### Q5 — User can ratchet down household policy

Considered: let a user explicitly override their household's `disabled` to `permissive` for themselves. Rejected: the household is the security boundary for AI policy; the admin's intent shouldn't be overridable per-user. The opposite asymmetry (user opts *out* of a capability the household uses) makes the household admin's life easier and is locked.

### Q6 — Single audit table for all AI calls including previews

Considered: log `tulip ai preview` invocations into `ai_invocations` with `outcome=redacted_only_preview`. Accepted, but logged with `provider=null, tokens_in=0, cost_estimate_usd=0` — the row exists for "did the user run a preview, when" forensics. Tests assert preview rows don't carry token / cost data.

### Q7 — Per-capability cost caps

Considered: each of the four capabilities gets its own monthly cap. Rejected for v1: the household-wide cap is simpler and the same dollar buys very different value across capabilities; users will run up against the cap on whichever capability they use most and adjust manually. Per-capability caps can be added in a later slice without changing the schema.

### Q8 — Auto-retry on provider 5xx

Considered: built-in exponential backoff retry on transient provider errors. Rejected for v1: the "no silent fallback" rule extends naturally to "no silent retry" — every provider call is one call. Retry is the user's choice. The cost is one extra command per transient failure; the gain is that a stuck provider doesn't quietly burn the user's monthly budget.

### Q9 — Categorization-first or NL-query-first

Considered: ship NL query first as the more visible feature. Rejected: categorize plugs into the existing P5.3 DI seam with zero new user-visible surface (the user already sees categorization proposals from the `NullCategorizer`), so P6.1 is end-to-end small. NL query introduces new commands, new endpoints, new audit shapes, and the read-only-view discipline; better to land it once the scaffolding (redactor, audit writer, cost cap) is proven in production.

## References

- [ARCHITECTURE.md §6](../ARCHITECTURE.md) — AI integration sketch.
- [ARCHITECTURE.md §10](../ARCHITECTURE.md) — audit cadence (privacy audit before Phase 6).
- [THREAT_MODEL.md §5.3](../THREAT_MODEL.md) — Phase 6 constraints.
- [ADR-0001](0001-envelope-shadow-ledger.md) — shadow-ledger writer-chokepoint pattern (the same shape the AI invocation writer uses).
- [ADR-0004 §Q2](0004-reconciliation.md) — the `Categorizer` DI seam this ADR consumes.
- `packages/tulip-core/src/tulip_core/reconciliation/categorizer.py` — the Protocol Phase 6 plugs into.
- Issue #102 — AI provider data-flow contract; closed by this ADR.
- Issue #106 — log redaction policy (PII in app logs); related, separately tracked.

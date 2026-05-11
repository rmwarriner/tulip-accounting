"""AI capabilities for Tulip Accounting — Phase 6 / P6.1 (ADR-0005)."""

from __future__ import annotations

from tulip_ai.adapters import (
    LitellmAdapter,
    ProviderAdapter,
    ProviderResponse,
    RecordingAdapter,
)
from tulip_ai.audit import AIInvocationRecord, AIInvocationWriter, hash_prompt_payload
from tulip_ai.categorize import AICategorizer, build_categorize_prompt
from tulip_ai.cost import (
    DEFAULT_RATE_LIMIT_PER_HOUR,
    CostDecision,
    RateDecision,
    check_cost_cap,
    check_rate_limit,
)
from tulip_ai.errors import (
    AICapDisabled,
    AICostCapped,
    AIError,
    AIProviderError,
    AIRateLimited,
)
from tulip_ai.forecast import (
    AIForecastCapability,
    ForecastPromptPayload,
    ForecastResult,
    bucket_time_series,
    build_forecast_prompt,
)
from tulip_ai.nl_query import AINLQueryCapability, NLAnswer
from tulip_ai.policy import ResolvedPolicy, resolve_policy
from tulip_ai.proposals import (
    AIProposalCapability,
    ProposedChange,
    SuggestionResult,
)
from tulip_ai.redaction import (
    CategorizeExample,
    CategorizePromptPayload,
    ChartEntry,
    PromptRedactor,
    RedactionProfile,
)
from tulip_ai.sql_safety import (
    AI_VIEWS,
    SafeSQL,
    UnsafeSQLError,
    schema_card,
    validate_and_rewrite,
)

__all__ = [
    "AI_VIEWS",
    "DEFAULT_RATE_LIMIT_PER_HOUR",
    "AICapDisabled",
    "AICategorizer",
    "AICostCapped",
    "AIError",
    "AIForecastCapability",
    "AIInvocationRecord",
    "AIInvocationWriter",
    "AINLQueryCapability",
    "AIProposalCapability",
    "AIProviderError",
    "AIRateLimited",
    "CategorizeExample",
    "CategorizePromptPayload",
    "ChartEntry",
    "CostDecision",
    "ForecastPromptPayload",
    "ForecastResult",
    "LitellmAdapter",
    "NLAnswer",
    "PromptRedactor",
    "ProposedChange",
    "ProviderAdapter",
    "ProviderResponse",
    "RateDecision",
    "RecordingAdapter",
    "RedactionProfile",
    "ResolvedPolicy",
    "SafeSQL",
    "SuggestionResult",
    "UnsafeSQLError",
    "bucket_time_series",
    "build_categorize_prompt",
    "build_forecast_prompt",
    "check_cost_cap",
    "check_rate_limit",
    "hash_prompt_payload",
    "resolve_policy",
    "schema_card",
    "validate_and_rewrite",
]

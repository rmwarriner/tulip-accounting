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
from tulip_ai.errors import (
    AICapDisabled,
    AICostCapped,
    AIError,
    AIProviderError,
    AIRateLimited,
)
from tulip_ai.nl_query import AINLQueryCapability, NLAnswer
from tulip_ai.policy import ResolvedPolicy, resolve_policy
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
    "AICapDisabled",
    "AICategorizer",
    "AICostCapped",
    "AIError",
    "AIInvocationRecord",
    "AIInvocationWriter",
    "AINLQueryCapability",
    "AIProviderError",
    "AIRateLimited",
    "CategorizeExample",
    "CategorizePromptPayload",
    "ChartEntry",
    "LitellmAdapter",
    "NLAnswer",
    "PromptRedactor",
    "ProviderAdapter",
    "ProviderResponse",
    "RecordingAdapter",
    "RedactionProfile",
    "ResolvedPolicy",
    "SafeSQL",
    "UnsafeSQLError",
    "build_categorize_prompt",
    "hash_prompt_payload",
    "resolve_policy",
    "schema_card",
    "validate_and_rewrite",
]

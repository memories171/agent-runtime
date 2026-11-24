"""Context Manager.

Builds a versioned, provenance-bearing ContextPack before every LLM call.

Design guarantees:
- Deterministic assembly logic lives here (pure functions); all non-deterministic
  work (LLM summary, DB, object storage) happens in Temporal activities.
- External content (web pages, MCP/tool output, retrieved memory) is always
  marked untrusted and never merged into the System Instruction.
- Context construction never depends on ``history[-16:]``; truncation follows
  an explicit, priority-aware budget policy.
"""

from agent_runtime.context.pack import (
    CONTEXT_PACK_SCHEMA_VERSION,
    BudgetContext,
    ContextItem,
    ContextMetadata,
    ContextPack,
    ExecutionContext,
    GoalContext,
    HistoricalContext,
    MemoryContext,
    Provenance,
    Sensitivity,
    SourceType,
    TrustLevel,
)
from agent_runtime.context.budget import BudgetAllocation, BudgetConfig, allocate_budget
from agent_runtime.context.tokenizer import TokenEstimate, TokenEstimator, get_estimator

__all__ = [
    "CONTEXT_PACK_SCHEMA_VERSION",
    "ContextPack",
    "ContextMetadata",
    "GoalContext",
    "ExecutionContext",
    "HistoricalContext",
    "MemoryContext",
    "BudgetContext",
    "ContextItem",
    "Provenance",
    "TrustLevel",
    "Sensitivity",
    "SourceType",
    "BudgetConfig",
    "BudgetAllocation",
    "allocate_budget",
    "TokenEstimator",
    "TokenEstimate",
    "get_estimator",
]

"""Configurable Token Budget allocation.

Available tokens = context limit - system instructions - reserved output -
safety margin. Remaining tokens are split by configurable priority ratios.
Important constraints may exceed their normal partition when needed; overflow
never silently truncates — the assembler raises or degrades explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BudgetConfig:
    goal_pct: float = 0.40
    summary_pct: float = 0.30
    memory_pct: float = 0.20
    tools_pct: float = 0.10
    reserved_output_tokens: int = 1_024
    safety_margin_tokens: int = 256
    context_window_override: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "BudgetConfig":
        if not raw:
            return cls()
        allowed = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**allowed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal_pct": self.goal_pct,
            "summary_pct": self.summary_pct,
            "memory_pct": self.memory_pct,
            "tools_pct": self.tools_pct,
            "reserved_output_tokens": self.reserved_output_tokens,
            "safety_margin_tokens": self.safety_margin_tokens,
            "context_window_override": self.context_window_override,
        }


@dataclass
class BudgetAllocation:
    context_limit: int
    system_instruction_tokens: int
    reserved_output_tokens: int
    safety_margin_tokens: int
    available_tokens: int
    partitions: dict[str, int] = field(default_factory=dict)

    @property
    def total_partition_tokens(self) -> int:
        return sum(self.partitions.values())


def allocate_budget(
    context_limit: int,
    system_instruction_tokens: int,
    config: BudgetConfig,
) -> BudgetAllocation:
    """Compute available tokens and per-partition budgets."""
    fixed = system_instruction_tokens + config.reserved_output_tokens + config.safety_margin_tokens
    available = max(0, context_limit - fixed)
    partitions = {
        "goal": int(available * config.goal_pct),
        "summary": int(available * config.summary_pct),
        "memory": int(available * config.memory_pct),
        "tools": int(available * config.tools_pct),
    }
    return BudgetAllocation(
        context_limit=context_limit,
        system_instruction_tokens=system_instruction_tokens,
        reserved_output_tokens=config.reserved_output_tokens,
        safety_margin_tokens=config.safety_margin_tokens,
        available_tokens=available,
        partitions=partitions,
    )

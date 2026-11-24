"""Unified Token Estimator interface.

Prefers a real provider tokenizer when available; otherwise uses a conservative
heuristic and marks the result ``estimated=True``. Never fabricates provider
usage — real usage is only recorded when the provider actually returns it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    estimated: bool
    tokenizer_name: str


class TokenEstimator(Protocol):
    name: str

    def count(self, value: Any) -> TokenEstimate: ...


class HeuristicEstimator:
    """Conservative char-based estimator (~3 chars/token for mixed CJK/code)."""

    name = "heuristic"

    def count(self, value: Any) -> TokenEstimate:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        # Conservative: CJK chars cost ~1 token each; ASCII ~4 chars/token.
        cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
        ascii_chars = len(text) - cjk
        tokens = int(cjk + ascii_chars / 3.5) + 1
        return TokenEstimate(tokens=max(1, tokens), estimated=True, tokenizer_name=self.name)


class TiktokenEstimator:
    """Real tokenizer via ``tiktoken`` when installed."""

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        import tiktoken  # type: ignore

        self._encoding = tiktoken.get_encoding(encoding_name)
        self.name = f"tiktoken:{encoding_name}"

    def count(self, value: Any) -> TokenEstimate:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        return TokenEstimate(tokens=len(self._encoding.encode(text)), estimated=False, tokenizer_name=self.name)


# Model-family -> preferred encoding. Conservative mapping; unknown models use heuristic.
_MODEL_ENCODINGS: dict[str, str] = {
    "gpt-4": "cl100k_base",
    "gpt-4.1": "cl100k_base",
    "gpt-3.5": "cl100k_base",
    "o1": "o200k_base",
    "o3": "o200k_base",
}


def get_estimator(model_name: str | None) -> TokenEstimator:
    """Return the best available estimator for a model, with safe fallback."""
    model = (model_name or "").lower()
    encoding = next((enc for prefix, enc in _MODEL_ENCODINGS.items() if model.startswith(prefix)), None)
    if encoding:
        try:
            return TiktokenEstimator(encoding)
        except Exception:
            pass
    return HeuristicEstimator()


# Known context windows; unknown models use a conservative default.
DEFAULT_CONTEXT_LIMIT = 8_192
MODEL_CONTEXT_LIMITS: dict[str, int] = {
    "gpt-4.1-mini": 128_000,
    "gpt-4.1": 128_000,
    "gpt-4o": 128_000,
    "gpt-4": 128_000,
    "gpt-3.5": 16_385,
    "o1": 200_000,
    "o3": 200_000,
}


def model_context_limit(model_name: str | None, override: int | None = None) -> int:
    if override and override > 0:
        return override
    model = (model_name or "").lower()
    for prefix, limit in MODEL_CONTEXT_LIMITS.items():
        if model.startswith(prefix):
            return limit
    return DEFAULT_CONTEXT_LIMIT

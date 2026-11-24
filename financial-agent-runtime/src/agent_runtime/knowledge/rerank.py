"""重排 provider：调用宿主机 BGE-M3 /rerank 服务做 late-interaction 重排。

未配置 RERANK_URL 时返回未启用状态，由调用方回退到 RRF 原始顺序。
"""
from __future__ import annotations

import os
from typing import Protocol


class RerankProvider(Protocol):
    def rerank(self, query: str, texts: list[str]) -> list[float]: ...


class HttpReranker:
    """HTTP 重排 provider。服务约定见 bge_m3_server.py 的 /rerank。"""

    def __init__(self, url: str | None = None, timeout: float | None = None):
        self.url = url or os.environ.get("RERANK_URL", "")
        self.timeout = timeout or float(os.environ.get("RERANK_TIMEOUT_SECONDS", "120"))
        self.enabled = bool(self.url)

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        if not self.enabled:
            raise RuntimeError("RERANK_URL is not configured")
        import httpx

        response = httpx.post(
            self.url,
            json={"query": query, "texts": texts},
            timeout=self.timeout,
        )
        response.raise_for_status()
        scores = response.json().get("scores") or []
        if len(scores) != len(texts):
            raise RuntimeError(
                f"rerank service returned {len(scores)} scores for {len(texts)} texts"
            )
        return [float(score) for score in scores]


def get_reranker() -> RerankProvider:
    return HttpReranker()


def get_rerank_min_score() -> float:
    """绝对相关度阈值；低于该值的候选不注入问答上下文。"""
    try:
        return float(os.environ.get("RERANK_MIN_SCORE", "0.80"))
    except ValueError:
        return 0.80

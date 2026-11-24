"""从零实现向量化：中文字符 bigram + 英文单词的 TF-IDF 哈希向量。

不依赖外部 embedding 服务。生产环境可实现同样的 provider 接口替换为
sentence-transformers / OpenAI embeddings，其余链路无需改动。
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Protocol

DIMENSION = 512
_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]: ...


class TfidfHashEmbedding:
    """确定性哈希向量 + 语料 IDF。fit 后可用于同一批文档与查询。"""

    def __init__(self, dimension: int = DIMENSION):
        self.dimension = dimension
        self.idf: dict[str, float] = {}
        self._doc_count = 0

    def _tokens(self, text: str) -> list[str]:
        text = text.lower()
        tokens: list[str] = []
        for word in _WORD_RE.findall(text):
            if len(word) >= 2:
                tokens.append(f"w:{word}")
        cjk = _CJK_RE.findall(text)
        for i in range(len(cjk) - 1):
            tokens.append(f"b:{cjk[i]}{cjk[i + 1]}")
        tokens.extend(f"c:{char}" for char in cjk)
        return tokens

    def fit(self, texts: list[str]) -> "TfidfHashEmbedding":
        df: Counter[str] = Counter()
        for text in texts:
            df.update(set(self._tokens(text)))
        self._doc_count = max(len(texts), 1)
        self.idf = {
            token: math.log((self._doc_count + 1) / (freq + 1)) + 1.0
            for token, freq in df.items()
        }
        return self

    def _hash_index(self, token: str) -> int:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.dimension

    def embed(self, text: str) -> list[float]:
        counts = Counter(self._tokens(text))
        vector = [0.0] * self.dimension
        for token, count in counts.items():
            weight = (1.0 + math.log(count)) * self.idf.get(token, 1.0)
            vector[self._hash_index(token)] += weight
        # L2 归一化，使点积即余弦相似度。
        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return vector


class BGEM3Embedding:
    """BGE-M3 外部推理服务 provider。未配置 URL 时不可用，调用方自动 fallback。

    服务约定：POST {BGE_M3_URL}  {"texts": [...]} -> {"vectors": [[float, ...], ...]}
    也可通过 BGE_M3_DIMENSION 指定维度（默认 1024）。
    """
    def __init__(self, url: str | None = None, dimension: int | None = None):
        import os
        self.url = url or os.environ.get("BGE_M3_URL", "")
        self.dimension = dimension or int(os.environ.get("BGE_M3_DIMENSION", "1024"))
        self.enabled = bool(self.url)

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not self.enabled:
            raise RuntimeError("BGE_M3_URL is not configured")
        import httpx
        response = httpx.post(self.url, json={"texts": texts}, timeout=120)
        response.raise_for_status()
        vectors = response.json().get("vectors") or []
        if len(vectors) != len(texts):
            raise RuntimeError(f"BGE-M3 service returned {len(vectors)} vectors for {len(texts)} texts")
        return [[float(value) for value in vector] for vector in vectors]


def get_embedder(dimension: int = DIMENSION) -> EmbeddingProvider:
    """按配置返回向量 provider；未配置外部服务时用 TF-IDF fallback。"""
    provider = BGEM3Embedding()
    if provider.enabled:
        return provider
    return TfidfHashEmbedding(dimension=dimension)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))

"""多路召回 + RRF 融合。

三路：BM25 关键词召回、向量召回（BGE-M3 或 TF-IDF fallback）、PostgreSQL 结构化过滤。
结构化条件作为硬过滤前置，BM25 与向量两路用 RRF 融合（k=60），不依赖分数归一化。
本模块不修改任何既有表结构或 API，search_chunks 返回格式保持兼容。
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.db import KnowledgeChunkRecord, KnowledgeDocumentRecord
from agent_runtime.knowledge.embedding import TfidfHashEmbedding, cosine_similarity
from agent_runtime.knowledge.rerank import get_reranker, get_rerank_min_score

RRF_K = 60
_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """中文 bigram + 单字 + 英文单词，供 BM25 使用。"""
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


class BM25:
    """纯 Python BM25（Robertson 权重），无需额外依赖或数据库扩展。"""

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = corpus
        self.doc_len = [len(doc) for doc in corpus]
        self.avgdl = sum(self.doc_len) / len(corpus) if corpus else 0.0
        self.tf = [Counter(doc) for doc in corpus]
        df: Counter[str] = Counter()
        for doc in corpus:
            df.update(set(doc))
        n = len(corpus)
        self.idf = {
            term: math.log((n - freq + 0.5) / (freq + 0.5) + 1.0)
            for term, freq in df.items()
        }

    def score(self, query_tokens: list[str], index: int) -> float:
        if index >= len(self.docs):
            return 0.0
        length_norm = 1 - self.b + self.b * (self.doc_len[index] / self.avgdl if self.avgdl else 0)
        total = 0.0
        for term in query_tokens:
            freq = self.tf[index].get(term, 0)
            if not freq:
                continue
            idf = self.idf.get(term, 0.0)
            total += idf * (freq * (self.k1 + 1)) / (freq + self.k1 * length_norm)
        return total


def rrf_fuse(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """RRF: score(d) = Σ 1/(k + rank_i)，rank 从 1 开始。"""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    """RRF 分数转 0~1 展示分（最大值为 1），仅用于前端展示。"""
    if not scores:
        return {}
    max_score = max(scores.values())
    if max_score <= 0:
        return {}
    return {key: round(value / max_score, 6) for key, value in scores.items()}


async def hybrid_search(
    session: AsyncSession,
    tenant_id: str,
    query: str,
    top_k: int = 5,
    filters: dict | None = None,
    candidate_k: int = 50,
) -> list[dict]:
    """结构化硬过滤 → BM25 + 向量两路召回 → RRF 融合。返回格式兼容 search_chunks。"""
    stmt = select(KnowledgeChunkRecord).where(KnowledgeChunkRecord.tenant_id == tenant_id)
    filters = filters or {}
    # 结构化过滤：使用现有字段（content_type 在 document 表，需 join）。
    if filters.get("content_type"):
        types = filters["content_type"] if isinstance(filters["content_type"], list) else [filters["content_type"]]
        stmt = stmt.join(KnowledgeDocumentRecord, KnowledgeChunkRecord.document_id == KnowledgeDocumentRecord.document_id)
        stmt = stmt.where(KnowledgeDocumentRecord.content_type.in_(types))
    if filters.get("created_after"):
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(str(filters["created_after"]).replace("Z", "+00:00"))
            stmt = stmt.where(KnowledgeChunkRecord.created_at >= dt)
        except ValueError:
            pass

    chunks = list((await session.execute(stmt)).scalars())
    if not chunks:
        return []

    by_id = {str(chunk.chunk_id): chunk for chunk in chunks}

    # 路 1：BM25 关键词召回
    corpus = [tokenize(chunk.content) for chunk in chunks]
    query_tokens = tokenize(query)
    bm25 = BM25(corpus)
    bm25_scores = [
        (str(chunk.chunk_id), bm25.score(query_tokens, index))
        for index, chunk in enumerate(chunks)
    ]
    bm25_ranking = [cid for cid, score in sorted(bm25_scores, key=lambda item: item[1], reverse=True) if score > 0][:candidate_k]

    # 路 2：向量召回。按每个 chunk 的存储向量维度选择编码方式：
    # 512 维 → TF-IDF（查询时统一重算，保证与查询向量同一空间）；
    # 其他维度 → 外部 provider（BGE-M3）。provider 不可用时该 chunk 跳过向量路，
    # 不得用 TF-IDF 向量去匹配外部向量（向量空间不同会产出无意义分数）。
    tfidf_embedder = TfidfHashEmbedding()
    tfidf_embedder.fit([chunk.content for chunk in chunks])
    tfidf_query_vector = tfidf_embedder.embed(query)

    bge_query_vector = None
    if any(len(chunk.vector or []) != 512 for chunk in chunks):
        from agent_runtime.knowledge.embedding import BGEM3Embedding
        import logging
        provider = BGEM3Embedding()
        if provider.enabled:
            try:
                candidate_vector = provider.embed(query)
                # 维度必须与存储向量一致，否则视为 provider 配置漂移。
                if len(candidate_vector) == provider.dimension:
                    bge_query_vector = candidate_vector
                else:
                    logging.getLogger(__name__).warning(
                        "BGE-M3 query vector dim %d != configured %d; skip vector recall",
                        len(candidate_vector), provider.dimension,
                    )
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "BGE-M3 query encode failed: %s: %s; skip vector recall",
                    type(exc).__name__, exc,
                )
        else:
            logging.getLogger(__name__).warning(
                "knowledge chunks use an external embedding provider but it is not "
                "configured; vector recall skipped, BM25 only"
            )

    vector_scores = []
    for chunk in chunks:
        cid = str(chunk.chunk_id)
        stored = chunk.vector or []
        dim = len(stored)
        if dim == 512:
            score = cosine_similarity(tfidf_query_vector, tfidf_embedder.embed(chunk.content))
        elif bge_query_vector is not None and dim == len(bge_query_vector):
            score = cosine_similarity(bge_query_vector, stored)
        else:
            continue
        vector_scores.append((cid, score))
    vector_ranking = [cid for cid, score in sorted(vector_scores, key=lambda item: item[1], reverse=True) if score > 0][:candidate_k]

    # RRF 融合
    fused = rrf_fuse([bm25_ranking, vector_ranking])
    normalized = _normalize(fused)
    fused_ranking = [cid for cid, _ in sorted(fused.items(), key=lambda item: item[1], reverse=True)]

    final_limit = max(1, min(top_k, 20))
    rerank_pool_size = max(final_limit, min(candidate_k, final_limit * 4))
    rerank_pool = fused_ranking[:rerank_pool_size]

    doc_ids = {by_id[cid].document_id for cid in rerank_pool if cid in by_id}
    docs: dict = {}
    if doc_ids:
        docs = {row.document_id: row for row in (await session.execute(
            select(KnowledgeDocumentRecord).where(
                KnowledgeDocumentRecord.document_id.in_(doc_ids),
                KnowledgeDocumentRecord.tenant_id == tenant_id,
            )
        )).scalars()}

    candidates = []
    for cid in rerank_pool:
        chunk = by_id.get(cid)
        if chunk is None:
            continue
        doc = docs.get(chunk.document_id)
        bm25_rank = bm25_ranking.index(cid) + 1 if cid in bm25_ranking else None
        vector_rank = vector_ranking.index(cid) + 1 if cid in vector_ranking else None
        candidates.append({
            "chunk_id": cid,
            "document_id": str(chunk.document_id),
            "document_title": doc.title if doc else "",
            "source": doc.source if doc else "",
            "heading": chunk.heading,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "score": normalized.get(cid, 0.0),
            "score_detail": {
                "bm25_rank": bm25_rank,
                "vector_rank": vector_rank,
                "rrf_score": round(fused.get(cid, 0.0), 6),
            },
        })

    reranker = None
    try:
        reranker = get_reranker()
        rerank_scores = (
            reranker.rerank(query, [item["content"] for item in candidates])
            if getattr(reranker, "enabled", False) and candidates else None
        )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "rerank failed: %s: %s; fallback to RRF ranking", type(exc).__name__, exc
        )
        rerank_scores = None

    if rerank_scores is None:
        return candidates[:final_limit]

    min_score = get_rerank_min_score()
    scored = [
        (item, raw_score)
        for item, raw_score in zip(candidates, rerank_scores)
        if raw_score >= min_score
    ]
    if not scored:
        return []

    for item, raw_score in scored:
        item["_rerank_raw"] = raw_score
        item["score_detail"]["rerank_score"] = round(raw_score, 6)
        item["score"] = round(raw_score, 6)
    scored.sort(key=lambda pair: pair[1], reverse=True)
    ranked = []
    for rank, (item, _raw_score) in enumerate(scored, start=1):
        item["score_detail"]["rerank_rank"] = rank
        item.pop("_rerank_raw", None)
        ranked.append(item)
    return ranked[:final_limit]

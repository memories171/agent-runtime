"""知识库服务：入库、列表、删除、检索。"""
from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.db import KnowledgeChunkRecord, KnowledgeDocumentRecord
from agent_runtime.knowledge.embedding import TfidfHashEmbedding, get_embedder, cosine_similarity
from agent_runtime.knowledge.pipeline import detect_content_type, parse_document, split_into_chunks


def _embed_chunks(chunks: list) -> list:
    # 优先用 BGE-M3（本地推理服务）；未配置时 fallback 到 TF-IDF 哈希向量。
    embedder = get_embedder()
    if isinstance(embedder, TfidfHashEmbedding):
        embedder.fit([chunk.content for chunk in chunks])
        return [embedder.embed(chunk.content) for chunk in chunks]
    try:
        return embedder.embed_batch([chunk.content for chunk in chunks])
    except Exception:
        # 服务不可用时降级，不阻塞入库。
        fallback = TfidfHashEmbedding()
        fallback.fit([chunk.content for chunk in chunks])
        return [fallback.embed(chunk.content) for chunk in chunks]


def _document_result(doc: KnowledgeDocumentRecord, chunk_count: int) -> dict:
    return {
        "document_id": str(doc.document_id),
        "title": doc.title,
        "chunk_count": chunk_count,
        "char_count": doc.char_count,
        "status": doc.status,
    }


async def ingest_document(
    session: AsyncSession,
    tenant_id: str,
    title: str,
    raw_content: str,
    source: str = "",
    content_type: str = "markdown",
    chunk_size: int = 400,
    chunk_overlap: int = 60,
) -> dict:
    resolved_type = detect_content_type(source, raw_content, content_type)
    parsed = parse_document(raw_content, resolved_type)
    chunks = split_into_chunks(parsed, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    if not chunks:
        raise ValueError("文档解析后没有可入库的内容")

    doc = KnowledgeDocumentRecord(
        document_id=uuid4(),
        tenant_id=tenant_id,
        title=title or parsed.title,
        source=source,
        content_type=resolved_type,
        raw_content=raw_content,
        char_count=sum(len(chunk.content) for chunk in chunks),
        chunk_count=len(chunks),
        status="READY",
    )
    session.add(doc)
    await session.flush()

    vectors = _embed_chunks(chunks)
    for chunk, vector in zip(chunks, vectors):
        session.add(KnowledgeChunkRecord(
            chunk_id=uuid4(),
            tenant_id=tenant_id,
            document_id=doc.document_id,
            chunk_index=chunk.index,
            heading=chunk.heading,
            content=chunk.content,
            vector=vector,
            char_count=len(chunk.content),
        ))
    await session.commit()
    return _document_result(doc, len(chunks))


async def update_document(
    session: AsyncSession,
    tenant_id: str,
    document_id: str,
    title: str,
    raw_content: str,
    source: str = "",
    content_type: str = "markdown",
    chunk_size: int = 400,
    chunk_overlap: int = 60,
) -> dict | None:
    try:
        doc_id = UUID(document_id)
    except ValueError:
        return None

    doc = await session.get(KnowledgeDocumentRecord, doc_id)
    if doc is None or doc.tenant_id != tenant_id:
        return None

    resolved_type = detect_content_type(source, raw_content, content_type)
    parsed = parse_document(raw_content, resolved_type)
    chunks = split_into_chunks(parsed, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    if not chunks:
        raise ValueError("文档解析后没有可入库的内容")
    vectors = _embed_chunks(chunks)

    await session.execute(delete(KnowledgeChunkRecord).where(
        KnowledgeChunkRecord.tenant_id == tenant_id,
        KnowledgeChunkRecord.document_id == doc_id,
    ))

    doc.title = title or parsed.title
    doc.source = source
    doc.content_type = resolved_type
    doc.raw_content = raw_content
    doc.char_count = sum(len(chunk.content) for chunk in chunks)
    doc.chunk_count = len(chunks)
    doc.status = "READY"
    await session.flush()

    for chunk, vector in zip(chunks, vectors):
        session.add(KnowledgeChunkRecord(
            chunk_id=uuid4(),
            tenant_id=tenant_id,
            document_id=doc.document_id,
            chunk_index=chunk.index,
            heading=chunk.heading,
            content=chunk.content,
            vector=vector,
            char_count=len(chunk.content),
        ))
    await session.commit()
    return _document_result(doc, len(chunks))


async def list_documents(session: AsyncSession, tenant_id: str, limit: int = 50) -> list[dict]:
    rows = (await session.execute(
        select(KnowledgeDocumentRecord)
        .where(KnowledgeDocumentRecord.tenant_id == tenant_id)
        .order_by(KnowledgeDocumentRecord.created_at.desc())
        .limit(max(1, min(limit, 200)))
    )).scalars()
    return [{
        "document_id": str(row.document_id),
        "title": row.title,
        "source": row.source,
        "content_type": row.content_type,
        "char_count": row.char_count,
        "chunk_count": row.chunk_count,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    } for row in rows]


async def delete_document(session: AsyncSession, tenant_id: str, document_id: str) -> bool:
    from uuid import UUID
    try:
        doc_id = UUID(document_id)
    except ValueError:
        return False
    doc = await session.get(KnowledgeDocumentRecord, doc_id)
    if doc is None or doc.tenant_id != tenant_id:
        return False
    await session.execute(delete(KnowledgeChunkRecord).where(
        KnowledgeChunkRecord.document_id == doc_id,
        KnowledgeChunkRecord.tenant_id == tenant_id,
    ))
    await session.delete(doc)
    await session.commit()
    return True


async def search_chunks(
    session: AsyncSession,
    tenant_id: str,
    query: str,
    top_k: int = 5,
    filters: dict | None = None,
) -> list[dict]:
    """多路召回统一入口：BM25 + 向量 + 结构化过滤 → RRF 融合。返回格式兼容原有调用。"""
    from agent_runtime.knowledge.retrieval import hybrid_search
    return await hybrid_search(session, tenant_id, query, top_k=top_k, filters=filters)

"""知识库 HTTP API：文档管理、向量检索、统计。"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.auth import get_principal
from agent_runtime.contracts import Principal
from agent_runtime.db import KnowledgeChunkRecord, KnowledgeDocumentRecord, get_session
from agent_runtime.knowledge.service import delete_document, ingest_document, list_documents, search_chunks, update_document

router = APIRouter()


class DocumentCreate(BaseModel):
    title: str = Field(default="", max_length=256)
    content: str = Field(min_length=1, max_length=500_000)
    source: str = Field(default="", max_length=512)
    content_type: str = Field(default="markdown", pattern="^(markdown|text|html|csv|json|jsonl|yaml|auto)$")
    chunk_size: int = Field(default=400, ge=50, le=2000)
    chunk_overlap: int = Field(default=60, ge=0, le=500)


class DocumentUpdate(BaseModel):
    title: str = Field(default="", max_length=256)
    content: str = Field(min_length=1, max_length=500_000)
    source: str = Field(default="", max_length=512)
    content_type: str = Field(default="markdown", pattern="^(markdown|text|html|csv|json|jsonl|yaml|auto)$")
    chunk_size: int = Field(default=400, ge=50, le=2000)
    chunk_overlap: int = Field(default=60, ge=0, le=500)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    filters: dict | None = Field(default=None, description="结构化硬过滤：content_type / created_after")


@router.get("/knowledge/documents")
async def get_documents(
    limit: int = 50,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    return await list_documents(session, principal.tenant_id, limit)


@router.get("/knowledge/documents/{document_id}/chunks")
async def get_document_chunks(
    document_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    try:
        doc_uuid = UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="document not found") from None

    doc = (await session.execute(
        select(KnowledgeDocumentRecord).where(
            KnowledgeDocumentRecord.tenant_id == principal.tenant_id,
            KnowledgeDocumentRecord.document_id == doc_uuid,
        )
    )).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")

    chunks = list((await session.execute(
        select(KnowledgeChunkRecord).where(
            KnowledgeChunkRecord.tenant_id == principal.tenant_id,
            KnowledgeChunkRecord.document_id == doc_uuid,
        ).order_by(KnowledgeChunkRecord.chunk_index.asc())
    )).scalars())
    raw_content = doc.raw_content or "\n\n".join(chunk.content for chunk in chunks)

    return {
        "document_id": str(doc.document_id),
        "title": doc.title,
        "source": doc.source,
        "content_type": doc.content_type,
        "raw_content": raw_content,
        "char_count": doc.char_count,
        "chunk_count": doc.chunk_count,
        "status": doc.status,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "chunks": [{
            "chunk_id": str(chunk.chunk_id),
            "chunk_index": chunk.chunk_index,
            "heading": chunk.heading,
            "content": chunk.content,
            "char_count": chunk.char_count,
        } for chunk in chunks],
    }


@router.post("/knowledge/documents", status_code=201)
async def create_document(
    body: DocumentCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await ingest_document(
            session, principal.tenant_id, body.title, body.content,
            source=body.source, content_type=body.content_type,
            chunk_size=body.chunk_size, chunk_overlap=body.chunk_overlap,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/knowledge/documents/{document_id}")
async def modify_document(
    document_id: str,
    body: DocumentUpdate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await update_document(
            session, principal.tenant_id, document_id, body.title, body.content,
            source=body.source, content_type=body.content_type,
            chunk_size=body.chunk_size, chunk_overlap=body.chunk_overlap,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="document not found")
    return result


@router.delete("/knowledge/documents/{document_id}", status_code=204)
async def remove_document(
    document_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    if not await delete_document(session, principal.tenant_id, document_id):
        raise HTTPException(status_code=404, detail="document not found")


@router.post("/knowledge/search")
async def search(
    body: SearchRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    return await search_chunks(session, principal.tenant_id, body.query, body.top_k, filters=body.filters)


@router.get("/knowledge/stats")
async def stats(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    doc_count = (await session.execute(
        select(func.count()).select_from(KnowledgeDocumentRecord)
        .where(KnowledgeDocumentRecord.tenant_id == principal.tenant_id)
    )).scalar_one()
    chunk_count = (await session.execute(
        select(func.count()).select_from(KnowledgeChunkRecord)
        .where(KnowledgeChunkRecord.tenant_id == principal.tenant_id)
    )).scalar_one()
    return {"documents": doc_count, "chunks": chunk_count}

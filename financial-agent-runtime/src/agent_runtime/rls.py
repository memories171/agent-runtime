from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

current_tenant_id: ContextVar[str | None] = ContextVar("current_tenant_id", default=None)


def set_current_tenant(tenant_id: str | None) -> Token:
    return current_tenant_id.set(tenant_id)


def reset_current_tenant(token: Token) -> None:
    current_tenant_id.reset(token)


async def set_tenant_context(session: AsyncSession, tenant_id: str | None) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id or ""},
    )


async def set_system_context(session: AsyncSession, context: str | None) -> None:
    await session.execute(
        text("SELECT set_config('app.system_context', :context, true)"),
        {"context": context or ""},
    )


@asynccontextmanager
async def system_session(session_factory, context: str) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        await set_tenant_context(session, None)
        await set_system_context(session, context)
        try:
            yield session
        finally:
            await set_system_context(session, None)


@asynccontextmanager
async def tenant_session(session_factory, tenant_id: str | None) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        try:
            yield session
        finally:
            await set_tenant_context(session, None)

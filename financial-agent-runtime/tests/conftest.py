from __future__ import annotations

import asyncio
import os

import pytest


def postgres_dsn() -> str | None:
    """测试专用 PostgreSQL DSN；必须指向非超级用户应用账号以验证 RLS。"""
    value = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not value:
        return None
    return value.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture(scope="session")
def real_postgres_dsn() -> str:
    dsn = postgres_dsn()
    if not dsn:
        pytest.skip("未设置 TEST_DATABASE_URL，跳过真实 PostgreSQL 集成测试")
    return dsn


# ---- B0: 真实 Temporal 集成测试显式开关 ------------------------------------
TEMPORAL_IT_SKIP_REASON = "未设置 RUN_TEMPORAL_IT=1：跳过真实 Temporal 集成测试"


def temporal_it_enabled() -> bool:
    """是否执行真实 Temporal 集成测试；必须显式开启，不得默认开启。"""
    return os.getenv("RUN_TEMPORAL_IT", "").strip().lower() in {"1", "true", "yes"}


@pytest.fixture(scope="session")
async def temporal_client():
    """真实 Temporal 客户端（session 级）。

    分类规则（B0-2）：
    - RUN_TEMPORAL_IT 未开启 → SKIP（模块级 skipif 先行，这里兜底）。
    - RUN_TEMPORAL_IT 已开启但 Client.connect 失败 → FAIL（pytest.fail，绝不降级为 SKIP）。
    - RUN_TEMPORAL_IT 已开启且连接成功 → 真实执行。

    10 秒超时只作用于 Client.connect；各测试原有的业务 wait_for 超时保持不变。
    """
    if not temporal_it_enabled():
        pytest.skip(TEMPORAL_IT_SKIP_REASON)
    address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    from temporalio.client import Client

    try:
        client = await asyncio.wait_for(
            Client.connect(address, namespace=namespace), timeout=10
        )
    except Exception as exc:
        pytest.fail(
            f"RUN_TEMPORAL_IT=1 已开启，但 Temporal 连接失败"
            f"（{address}/{namespace}）：{exc!r}"
        )
    yield client

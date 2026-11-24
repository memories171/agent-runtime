# B0 变更清单

日期：2026-11-21
批次：B0（Temporal 测试基础设施修复）
范围声明：仅 6 个允许文件；无 Git 操作（未 init / 未配置 Remote / 未上传）。

---

## 1. 修改文件清单（6 个）

| # | 文件 | 类型 | 改动摘要 |
|---|---|---|---|
| 1 | `pyproject.toml` | 修改 | test extras 新增 `pytest-timeout>=2.3,<3`；pytest markers 注册 `temporal_it`/`wall_clock_30m`/`reconciliation`/`checkpoint_service` |
| 2 | `tests/conftest.py` | 修改 | 新增 `TEMPORAL_IT_SKIP_REASON`、`temporal_it_enabled()`、session 级 `temporal_client` fixture（10s 连接超时 + 开关关 SKIP / 连接失败 FAIL / 成功真实执行） |
| 3 | `tests/test_subagent_timeout_cancel.py` | 修改 | 删除 4 行 setdefault 与硬编码 DSN；加模块级 `pytestmark`（temporal_it + 开关 SKIP）；DSN 走 `real_postgres_dsn`；删除本地 `temporal_client` fixture；`_conn()` 用 `_PG_DSN` |
| 4 | `tests/test_subagent_integration.py` | 修改 | 同上（`_conn()` 用 `_PG_DSN`） |
| 5 | `tests/test_graph_integration.py` | 修改 | 同上；额外将 `_start_and_wait()` 的 DSN 由 `os.environ["TEST_DATABASE_URL"]` 改为 `_PG_DSN`；删除未使用的 `Client` import |
| 6 | `tests/test_parent_close_policy.py` | 修改 | 同 #3 |

---

## 2. 各文件改动明细

### `pyproject.toml`
- L36 test 列表：`..., "pytest-asyncio>=0.25,<1", "pytest-timeout>=2.3,<3", ...`
- L41+ markers 段：新增 4 个 marker 注册。

### `tests/conftest.py`
- 新增 `import asyncio`。
- 新增常量 `TEMPORAL_IT_SKIP_REASON = "未设置 RUN_TEMPORAL_IT=1：跳过真实 Temporal 集成测试"`。
- 新增 `temporal_it_enabled() -> bool`（读取 `RUN_TEMPORAL_IT`）。
- 新增 `@pytest.fixture(scope="session") async def temporal_client()`：
  ```python
  if not temporal_it_enabled(): pytest.skip(...)
  address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
  namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
  try:
      client = await asyncio.wait_for(Client.connect(address, namespace=namespace), timeout=10)
  except Exception as exc:
      pytest.fail(f"RUN_TEMPORAL_IT=1 已开启，但 Temporal 连接失败（{address}/{namespace}）：{exc!r}")
  yield client
  ```

### 四个测试文件（统一模式）
- 删除：`DSN = "postgresql+asyncpg://agent_app:..."`, `ASYNCPG_DSN = ...`（如有）, 4 行 `os.environ.setdefault(...)`。
- 新增（模块级）：
  ```python
  _TEMPORAL_IT_ON = os.getenv("RUN_TEMPORAL_IT", "").strip().lower() in {"1", "true", "yes"}
  pytestmark = [
      pytest.mark.temporal_it,
      pytest.mark.skipif(not _TEMPORAL_IT_ON, reason="未设置 RUN_TEMPORAL_IT=1：跳过真实 Temporal 集成测试"),
  ]
  _PG_DSN: str | None = None

  @pytest.fixture(scope="session", autouse=True)
  def _init_pg_dsn(real_postgres_dsn: str) -> None:
      global _PG_DSN
      _PG_DSN = real_postgres_dsn
  ```
- `_conn()`：`asyncpg.connect(ASYNCPG_DSN)` → `asyncpg.connect(_PG_DSN)`。
- 删除本地 `temporal_client` fixture（3 文件各 1 个；graph 文件原有 1 个）。
- graph 额外：`_start_and_wait()` 的 `os.environ["TEST_DATABASE_URL"].replace(...)` → `_PG_DSN`；删除 `from temporalio.client import Client`。

---

## 3. 未修改内容（合规声明）

- `src/agent_runtime/**`：0 改动。
- 数据库迁移文件：0 改动；未执行任何迁移；未改动数据库结构与运行数据。
- Phase 1 安全修复文件（6 个生产 + 5 个测试）：0 改动。
- 其他测试文件：0 改动。
- 24 个用例的业务断言（90 条）与业务 `wait_for` 超时（`TIMEOUT`=150/180、teardown 10）：0 改动。
- 测试数量：22 函数 / 24 用例，与基线一致。
- Git：未初始化、未配置 Remote、未上传代码。

---

## 4. 回滚说明

- 回滚单位：单文件可独立回滚（备份位于 `%TEMP%\b0_backup`）。
- 回滚影响：仅影响测试基础设施；不涉及生产代码或数据库，无数据风险。
- 回滚后效应：恢复导入期 setdefault 与硬编码 DSN（即 Phase 1 的挂起/绕过 skip 问题回归）。

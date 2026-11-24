# B0 测试基础设施修复报告

日期：2026-11-21
批次：B0（Temporal 测试基础设施修复）
状态：**验收完成，等待人工验收**
修改范围：仅 6 个允许的文件（4 个 Temporal 测试文件 + `tests/conftest.py` + `pyproject.toml`）

---

## 1. 修改前基线记录

| 文件 | 测试函数 | 断言数 | 业务 wait_for 超时 | 导入期 setdefault |
|---|---|---|---|---|
| `tests/test_subagent_timeout_cancel.py` | 8 | 18 | `TIMEOUT`(=150)、worker teardown `10` | 4 |
| `tests/test_subagent_integration.py` | 4 | 35 | `TIMEOUT`(=180)、worker teardown `10` | 4 |
| `tests/test_graph_integration.py` | 5 函数 / 7 用例（1 parametrize×3） | 24 | `TIMEOUT`(=180)、worker teardown `10` | 4 |
| `tests/test_parent_close_policy.py` | 5 | 13 | `TIMEOUT`(=150)、worker teardown `10` | 4 |
| **合计** | **22 函数 / 24 用例** | **90** | — | **16** |

修改后复核：**22 函数 / 24 用例、90 断言、wait_for 超时值完全一致、setdefault=0**。业务断言与业务超时零改动。

---

## 2. 代码变更（逐文件）

### 2.1 `tests/conftest.py`（共享基础设施）
新增：
- `TEMPORAL_IT_SKIP_REASON` 常量。
- `temporal_it_enabled()`：读取 `RUN_TEMPORAL_IT`（`1/true/yes`）显式开关。
- session 级 `temporal_client` fixture：
  - 开关未开 → `pytest.skip`（兜底；模块级 skipif 先行）。
  - 开关已开但 `Client.connect` 失败 → `pytest.fail`（**FAIL，绝不降级为 SKIP**）。
  - 连接成功 → 真实执行。
  - `asyncio.wait_for(Client.connect(...), timeout=10)` —— **10 秒只作用于 Client.connect**。
- 保留原有 `postgres_dsn()` / `real_postgres_dsn()` 不变（未放宽其 SKIP 语义）。

### 2.2 `pyproject.toml`
- `test` extras 新增 `pytest-timeout>=2.3,<3`。
- `[tool.pytest.ini_options].markers` 注册 `temporal_it`、`wall_clock_30m`、`reconciliation`、`checkpoint_service`。

### 2.3 四个 Temporal 测试文件（相同模式）
每个文件：
1. 删除 4 行 `os.environ.setdefault(...)`（TEST_DATABASE_URL / DATABASE_URL / TEMPORAL_ADDRESS / TEMPORAL_NAMESPACE）。
2. 删除硬编码 `DSN` / `ASYNCPG_DSN` 常量。
3. 新增模块级：
   - `_TEMPORAL_IT_ON = os.getenv("RUN_TEMPORAL_IT", ...)`
   - `pytestmark = [pytest.mark.temporal_it, pytest.mark.skipif(not _TEMPORAL_IT_ON, reason=...)]`
   - 新增 session autouse fixture `_init_pg_dsn(real_postgres_dsn)`，把 DSN 注入模块级 `_PG_DSN`。
4. `_conn()` 由 `asyncpg.connect(ASYNCPG_DSN)` 改为 `asyncpg.connect(_PG_DSN)`（DSN 统一走 `real_postgres_dsn`）。
5. 删除各文件本地的 `temporal_client` fixture，统一使用 conftest 的共享 fixture（含 10s 连接超时与失败分类）。
6. `test_graph_integration.py` 额外：`_start_and_wait()` 的 DSN 从 `os.environ["TEST_DATABASE_URL"]` 改为 `_PG_DSN`；删除因此未使用的 `Client` import。

**未改动**：任何测试断言、`wait_for` 业务超时、清理逻辑、worker startup/shutdown、任何 `src/agent_runtime/**` 代码。

---

## 3. 修改范围核查（diff 结论）

对 6 个文件逐行对比备份与修改后版本：
- 所有差异均为上述 B0-1/B0-2/B0-3 的预期改动。
- 无 `src/agent_runtime/**` 改动。
- 无数据库迁移、无运行数据改动。
- 无 Phase 1 安全修复文件改动。
- 无其他测试文件改动。
- 未初始化 Git、未配置 Remote、未上传代码。

**修改范围合规性：PASS。**

---

## 4. 静态检查

`ruff check`（6 个目标文件）：8 个错误，全部为**修改前既已存在**（对备份文件运行 ruff 得到完全相同的 8 项：I001×3、F401×2、UP041×1、F821×1、F541×1）。本批次未新增静态错误；依据"不进行无必要重构"约束，未修改这些既有问题。

`pytest --markers`：4 个新 marker 均已注册可见。

`pip install -e '.[test]'`：EXIT=0，成功安装 `pytest-timeout-2.4.0`、`ruff-0.17.6` 等。

---

## 5. B0-4 Worker / event loop teardown 检查

**结论：未发现测试侧 Worker/event loop 资源泄漏，未做 teardown 修复。**

- 4 个文件的 worker fixture 均已有 `instance.shutdown()` + `wait_for(task, 10)` + `task.cancel()` 兜底（`test_subagent_timeout_cancel.py:122-135` 等），结构上无泄漏。
- 24 用例真实执行全部通过，进程正常退出（无挂起、无残留 worker task 阻塞会话结束）。
- 观察到一条 `PytestUnraisableExceptionWarning`：在关闭 `execute_graph.<locals>._launch` 生成器时，`generator.runner.py:762` 的 `_set_node_status(..., "FAILED")` 在 workflow event loop 已关闭后调用 `workflow.execute_activity`，抛 `NotInWorkflowEventLoopError`。
  - 性质：**警告（warning），非测试失败**，24 用例仍全部 PASS。
  - 归属：生产代码 `src/agent_runtime/graph/runner.py` 的取消清理路径，不属于本批次允许的修改范围（禁止修改 `src/**`），也不属于测试 fixture 的 worker/loop teardown。
  - 处置：如实记录为遗留问题，进入 B1+ 评估；本批次不修改。

---

## 6. 验收标准核对

| # | B0 验收标准 | 结果 |
|---|---|---|
| 1 | 裸机 `pytest -q` 30s 内完成，24 用例 SKIP 且原因明确 | PASS：20.56s，24 skipped（原因：RUN_TEMPORAL_IT 未开启） |
| 2 | `RUN_TEMPORAL_IT=1` + 服务就绪时 24 用例真实执行 | PASS：24 passed / 19m01s |
| 3 | Phase 1 的 188 passed 不回退，安全测试全绿 | PASS：188 passed / 24 deselected（可比范围一致） |
| 4 | 输出 PASS/FAIL/SKIP/NOT RUN 分类报告 | PASS（见 `B0_TEST_RESULTS.md`） |
| 5 | 无删测试/断言放宽/ACL 放宽/SKIP 伪装 PASS | PASS：22 函数/90 断言与基线一致 |
| 6 | 未改任何 `src/agent_runtime/**` | PASS |
| 7 | `pip install -e '.[test]'` 正常 | PASS |

**B0 全部 7 项验收标准满足。**

---

## 7. 风险与遗留

- 条件 SKIP 误用风险：开关默认关闭；nightly 必设 `RUN_TEMPORAL_IT=1`；报告如实分类。已缓解。
- 既有 ruff 8 项错误：不在本批次范围，未处理。
- `graph/runner.py` 的 unraisable warning：生产代码取消清理路径，非测试泄漏，非本批次范围；建议 B1+ 评估。
- `pytest-timeout` 已声明但本批次未设置全局 timeout ini（避免影响 188 个既有测试行为）；按计划在 B4 配置分层超时。

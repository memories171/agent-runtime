# B4 变更清单

日期：2026-11-21
范围：测试基础设施、测试清理、固定依赖入口与 B4 文档；未改生产业务逻辑，未改数据库 Schema。

---

## 1. 配置

### `pyproject.toml`

- `pythonpath`：`["src"]` → `["src", "."]`，修复 `scripts.*` 在 pytest 收集中不可导入的问题。
- 新增：
  - `timeout = 300`
  - `timeout_method = "thread"`
- 保留既有 markers：
  - `temporal_it`
  - `wall_clock_30m`
  - `reconciliation`
  - `checkpoint_service`

---

## 2. 测试文件

### 4 个 Temporal IT 文件

`tests/test_graph_integration.py`
`tests/test_subagent_integration.py`
`tests/test_subagent_timeout_cancel.py`
`tests/test_parent_close_policy.py`

- 在既有 `pytestmark` 中新增 `pytest.mark.timeout(900)`。
- 不修改业务断言、不修改 B0 `RUN_TEMPORAL_IT` 开关语义。

### `tests/test_invocation_recovery.py`

- 新增模块级 `_ENGINES`。
- 新增 autouse fixture `_dispose_test_engines()`，每个测试后统一 `dispose()` 所有 engine。
- `_setup()` 改为记录 engine 后再传给 `async_sessionmaker`。

### `tests/test_replay_reliability.py`

- 保留 engine 引用并在测试结束 `await engine.dispose()`。

### `tests/test_long_run_wall_clock.py`（新增）

- 新增 35 分钟真实墙钟入口。
- marker：`wall_clock_30m`。
- 默认 SKIP：`RUN_WALL_CLOCK_30M` 未设置。
- 显式启用需同时设置 `RUN_WALL_CLOCK_30M=1` 与 `RUN_TEMPORAL_IT=1`。
- 真实 Temporal 连接失败直接 FAIL。
- 使用 Temporal timer `workflow.sleep(2100)`，结束后执行历史 Replay。
- 超时：`pytest.mark.timeout(2700)`。
- Worker 显式 `shutdown()` / `wait_for()` / `cancel()` / `await` 清理。

---

## 3. 固定环境与脚本

### `requirements-b4-linux.lock.txt`（新增）

- 固化 Linux Python 3.12.16 容器中的完整测试依赖版本。
- 与 `pip freeze` 逐项一致，已比较为 `LOCK_DIFFERENCES=NONE`。

### `scripts/run_b4_linux_tests.ps1`（新增）

- 在 `b4-stable-test` 容器内运行：
  - `pytest -q -m "not temporal_it and not wall_clock_30m"`
- 输出重定向到 `b4_linux_full.out` / `b4_linux_full.err`。
- 写入 `EXITCODE=<code>` 到 `b4_linux_full.out`。

---

## 4. 交付文档

- `docs/phase2/B4_IMPLEMENTATION_REPORT.md`
- `docs/phase2/B4_CHANGELOG.md`
- `docs/phase2/B4_TEST_RESULTS.md`
- `docs/phase2/B4_LEFTOVER_ISSUES.md`

---

## 5. 未修改内容

- `src/agent_runtime/**` 生产业务逻辑：0 改动。
- B1/B2/B3 已验收业务行为：0 改动。
- Phase 1 ACL/JWT/RBAC/ABAC：0 改动。
- 数据库 Schema / 迁移：0 改动。
- Temporal SQL persistence：0 改动。
- Git remote / 提交 / 上传：未执行。
- 云端 CI：未配置。

# B4.1 变更清单

日期：2026-11-21
范围：测试基础设施遗留修复；未改生产业务逻辑，未改数据库 Schema。

---

## 1. 测试文件

### `tests/test_agent_tree_postgres.py`

- 新增 `import os`。
- 新增 `_plain_dsn()`：将 `postgresql+asyncpg://` 转为 `postgresql://`。
- `MIGRATION_DSN` 从硬编码改为环境变量优先：
  1. `TEST_MIGRATION_DSN`
  2. `DATABASE_MIGRATION_URL`
  3. 默认 `postgresql://agent:agent@localhost:5433/agent_runtime`
- 3 个使用位置保持不变，测试断言未改。

---

## 2. 测试运行脚本

### `scripts/run_b4_linux_tests.ps1`

- 新增 `TEST_MIGRATION_DSN` 注入：
  - `postgresql://agent:agent@host.docker.internal:5433/agent_runtime`
- 新增审计元数据：
  - `START`
  - `END`
  - `ELAPSED`
  - `EXITCODE`
  - `STDOUT`
  - `STDERR`
  - `COMMAND`
- 写入 `b4_linux_meta.out`。

### `scripts/run_pytest_audited.ps1`（新增）

- 通用 pytest 审计入口。
- 支持参数：
  - `LogPrefix`
  - `MarkerExpression`
  - `PytestArgs`
  - `Temporal`
  - `WallClock`
- 在脚本内部启动 pytest 并通过 `$LASTEXITCODE` 捕获真实退出码。
- 写入：
  - `<prefix>.out`
  - `<prefix>.err`
  - `<prefix>.meta.out`
- 修复 PowerShell 将 native stderr 当终止错误导致审计文件丢失的问题。

### `scripts/wait_b4_temporal.ps1`

- 本批次仅审查，未修改。
- 结论：通过 `GetProcessById()` 附加已有进程的方案无法可靠捕获跨会话进程退出码，已被新运行脚本替代用于后续批次。

### `scripts/classify_b4_warnings.py`（新增）

- 从 `b4_temporal_wait.out` 提取 Temporal stdout 段。
- 按类别统计 warnings 文本出现次数。
- 用于 B4.1 警告分类证据。

---

## 3. 文档

- `docs/phase2/B4_1_IMPLEMENTATION_REPORT.md`
- `docs/phase2/B4_1_CHANGELOG.md`
- `docs/phase2/B4_1_TEST_RESULTS.md`
- `docs/phase2/B4_1_LEFTOVER_ISSUES.md`

---

## 4. 未修改内容

- `src/agent_runtime/**`：0 改动。
- Phase 1 ACL/JWT/RBAC/ABAC：0 改动。
- B1/B2/B3 业务逻辑：0 改动。
- 数据库 Schema / migration：0 改动。
- Temporal SQL persistence：0 改动。
- Git remote / 提交 / 上传：未执行。
- 35 分钟真实墙钟任务：未启动。

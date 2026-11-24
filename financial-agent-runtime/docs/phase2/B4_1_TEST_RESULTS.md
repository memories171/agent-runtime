# B4.1 测试结果

日期：2026-11-21
说明：严格区分 PASS / FAIL / SKIP / NOT RUN。每项记录实际命令、环境、退出码、耗时和状态。

---

## 0. 环境

| 环境 | 值 |
|---|---|
| Windows Python | 3.14.7 |
| Linux 容器 | `b4-stable-test`，Python 3.12.16 |
| Windows app DSN | `postgresql+asyncpg://agent_app:agent_app@localhost:5433/agent_runtime` |
| Linux app DSN | `postgresql+asyncpg://agent_app:agent_app@host.docker.internal:5433/agent_runtime` |
| Linux migration DSN | `postgresql://agent:agent@host.docker.internal:5433/agent_runtime` |
| 依赖锁 | `requirements-b4-linux.lock.txt`，`LOCK_DIFFERENCES=NONE` |

---

## 1. Windows：MIGRATION_DSN 兼容性验证

实际命令：

```powershell
$env:DATABASE_URL="postgresql+asyncpg://agent_app:agent_app@localhost:5433/agent_runtime"
$env:TEST_DATABASE_URL=$env:DATABASE_URL
pytest -q --tb=short tests/test_agent_tree_postgres.py::test_physical_orphan_defensive_detection_with_fk_bypassed tests/test_agent_tree_postgres.py::test_migration_repair_traces_true_root_at_34_and_40_levels tests/test_agent_tree_postgres.py::test_migration_repair_reports_cycles_ambiguous_and_cross_tenant_links
```

结果：

- 测试数：3 passed
- warnings：5
- 耗时：22.88s
- 退出码：0
- 状态：**PASS**
- 说明：保持 Windows 默认值兼容，未设置 `TEST_MIGRATION_DSN`。

---

## 2. Linux：MIGRATION_DSN 兼容性验证

实际命令：

```powershell
docker exec `
  -e "DATABASE_URL=postgresql+asyncpg://agent_app:agent_app@host.docker.internal:5433/agent_runtime" `
  -e "TEST_DATABASE_URL=postgresql+asyncpg://agent_app:agent_app@host.docker.internal:5433/agent_runtime" `
  -e "TEST_MIGRATION_DSN=postgresql://agent:agent@host.docker.internal:5433/agent_runtime" `
  b4-stable-test `
  pytest -q --tb=short <3 node ids>
```

结果：

- 测试数：3 passed
- warnings：0
- 耗时：12.85s
- 退出码：0
- 状态：**PASS**
- 说明：B4 中失败的 3 项在 Linux 固定环境下通过。

---

## 3. Linux：完整非 Temporal 单次全量

实际命令（由 `scripts/run_b4_linux_tests.ps1` 执行）：

```powershell
pytest -q -m "not temporal_it and not wall_clock_30m"
```

环境：

- `DATABASE_URL=postgresql+asyncpg://agent_app:agent_app@host.docker.internal:5433/agent_runtime`
- `TEST_DATABASE_URL=same`
- `TEST_MIGRATION_DSN=postgresql://agent:agent@host.docker.internal:5433/agent_runtime`
- Python 3.12.16

审计元数据：

- 文件：`b4_linux_meta.out`
- START：`2026-11-21T15:37:24.1016260+08:00`
- END：`2026-11-21T15:38:37.4921791+08:00`
- wrapper ELAPSED：`00:01:13.3905531`
- EXITCODE：`0`
- STDOUT：`b4_linux_full.out`
- STDERR：`b4_linux_full.err`

pytest 结果：

- `219 passed, 25 deselected, 1 warning in 69.72s (0:01:09)`
- 状态：**PASS**
- 25 deselected = 24 Temporal IT + 1 wall-clock entry。
- 唯一 warning：`InsecureKeyLengthWarning`（JWT 测试既有短 key）。
- 对比 B4：原 3 failed 已消失，无新增失败。

---

## 4. 审计运行脚本：PASS 场景自检

实际命令：

```powershell
& ./scripts/run_pytest_audited.ps1 -LogPrefix "b4_audit_pass" -PytestArgs @("tests/test_step_budget.py")
```

结果：

- pytest：`13 passed, 5 warnings in 1.92s`
- wrapper ELAPSED：`00:00:03.8974988`
- EXITCODE：`0`
- 审计文件：
  - `b4_audit_pass.out`
  - `b4_audit_pass.err`
  - `b4_audit_pass.meta.out`
- 状态：**PASS**（审计脚本验证）

---

## 5. 审计运行脚本：FAIL 场景自检

实际命令：

```powershell
& ./scripts/run_pytest_audited.ps1 -LogPrefix "b4_audit_fail" -PytestArgs @("tests/__audit_missing_file__.py")
```

结果：

- pytest：文件不存在，收集失败（预期触发）
- wrapper ELAPSED：`00:00:01.3743386`
- EXITCODE：`4`
- 审计文件：
  - `b4_audit_fail.out`
  - `b4_audit_fail.err`
  - `b4_audit_fail.meta.out`
- 状态：**PASS**（审计脚本在失败场景仍可靠记录真实退出码）
- 说明：这是运行脚本自检，不是产品业务测试。

---

## 6. 真实 Temporal IT

B4.1 未重新执行完整 Temporal IT。

B4 原始结果保留：

- PID：32188
- 启动：`2026-11-21T15:16:07+08:00`
- pytest：`24 passed, 220 deselected, 382 warnings in 1028.79s (0:17:08)`
- 退出码：`EXITCODE=`（空值，审计不完整）
- 状态：B4 原始测试 **PASS**，但退出码审计不完整。
- B4.1 状态：**NOT RUN**（未重跑，不用部分用例替代）

新增审计脚本尚未用于完整 Temporal IT；后续 B5 验证应使用 `scripts/run_pytest_audited.ps1 -Temporal ...`。

---

## 7. 35 分钟真实墙钟测试

- 状态：**NOT RUN**
- 原因：B4.1 明确不启动未批准的 35 分钟真实任务。
- 入口：`tests/test_long_run_wall_clock.py`
- 默认：`RUN_WALL_CLOCK_30M` 未设置时 SKIP。
- 本批次未修改该入口。

---

## 8. 静态检查

命令范围：B4.1 修改的测试文件与脚本。

结果：

- diagnostics：0
- 状态：**PASS**

---

## 9. 汇总

| 项目 | 状态 |
|---|---|
| Windows 迁移 DSN 3 项 | PASS |
| Linux 迁移 DSN 3 项 | PASS |
| Linux 完整非 Temporal | PASS |
| 审计脚本 PASS 自检 | PASS |
| 审计脚本 FAIL 自检 | PASS |
| 完整 Temporal IT（B4.1） | NOT RUN，沿用 B4 PASS 但退出码审计不完整 |
| 35 分钟墙钟任务 | NOT RUN |
| 静态检查 | PASS |

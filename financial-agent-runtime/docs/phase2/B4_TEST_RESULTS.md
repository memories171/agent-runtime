# B4 测试结果分类报告

日期：2026-11-21
说明：严格区分 PASS / FAIL / SKIP / NOT RUN / INCOMPLETE。分批结果不得冒充单次全量结果；SKIP 不得写为 PASS。

---

## 1. 环境与命令总览

### Windows 本地环境

- OS：Windows
- Python：3.14.7
- PostgreSQL：localhost:5433，docker container healthy
- Temporal：localhost:7233，docker container healthy
- DSN：
  - `DATABASE_URL=postgresql+asyncpg://agent_app:agent_app@localhost:5433/agent_runtime`
  - `TEST_DATABASE_URL=$DATABASE_URL`

### Linux 固定环境

- Docker container：`b4-stable-test`
- Python：3.12.16
- 依赖锁：`requirements-b4-linux.lock.txt`
- `pip check`：`No broken requirements found.`
- 锁文件与容器安装逐项比较：`LOCK_DIFFERENCES=NONE`
- DSN：
  - `DATABASE_URL=postgresql+asyncpg://agent_app:agent_app@host.docker.internal:5433/agent_runtime`
  - `TEST_DATABASE_URL=$DATABASE_URL`

---

## 2. 单次完整非 Temporal 测试：Windows

命令：

```powershell
pytest -q --tb=short -m "not temporal_it and not wall_clock_30m"
```

结果：

- **PASS**
- `219 passed, 25 deselected, 6 warnings in 240.28s`
- 25 deselected = 24 Temporal IT + 1 wall-clock entry
- 退出码：_wrapper 未单独持久化_；pytest 汇总显示全通过，按 pytest 约定为 0。
- 日志：`b4_full_fast.out` / `b4_full_fast.err`

该结果是 B4 要求的单次完整非 Temporal 测试，不是分批结果。

---

## 3. 真实 Temporal 集成测试

命令：

```powershell
$env:RUN_TEMPORAL_IT="1"
pytest -q --tb=short -m "temporal_it"
```

启动信息：

- PID：32188
- 启动时间：2026-11-21 15:16:07 +08:00
- 原始日志：`b4_temporal_it.out` / `b4_temporal_it.err`
- 结果捕获脚本：`scripts/wait_b4_temporal.ps1`
- 结果捕获文件：`b4_temporal_wait.out`

最终结果：

- **PASS**
- `24 passed, 220 deselected, 382 warnings in 1028.79s (0:17:08)`
- 退出码：捕获脚本记录为 `EXITCODE=`（空值，进程对象未保留真实 code）；pytest 汇总为 24 passed，无 FAIL。
- 状态：**PASS**，但退出码捕获不完整，作为审计缺口记录。
- 警告：
  - Pydantic `model_fields` deprecated warnings。
  - `PytestUnraisableExceptionWarning`：`test_cancel_after_child_complete_does_not_overwrite` 关闭生成器时 `_NotInWorkflowEventLoopError`。
  - 这些是 warnings，不改变本次 24 passed 的 PASS 分类，但列入 `B4_LEFTOVER_ISSUES.md`。

说明：

- 只使用原始整套 Temporal IT 进程 PID 32188 的结果。
- 不重新运行部分用例替代原始结果。
- 运行中的进度点不计 PASS。

---

## 4. 单次完整非 Temporal 测试：Linux 固定容器

命令（由 `scripts/run_b4_linux_tests.ps1` 执行）：

```powershell
pytest -q -m "not temporal_it and not wall_clock_30m"
```

结果：

- **FAIL**
- `3 failed, 216 passed, 25 deselected, 1 warning in 55.08s`
- `EXITCODE=1`
- 日志：`b4_linux_full.out` / `b4_linux_full.err`

失败用例：

1. `tests/test_agent_tree_postgres.py::test_physical_orphan_defensive_detection_with_fk_bypassed`
2. `tests/test_agent_tree_postgres.py::test_migration_repair_traces_true_root_at_34_and_40_levels`
3. `tests/test_agent_tree_postgres.py::test_migration_repair_reports_cycles_ambiguous_and_cross_tenant_links`

失败原因：

- 三个失败均发生在 `asyncpg.connect(MIGRATION_DSN)`。
- `MIGRATION_DSN` 在测试文件中硬编码为 `postgresql://agent:agent@localhost:5433/agent_runtime`。
- 在容器内 `localhost:5433` 指向容器自身，而 PostgreSQL 位于宿主机。
- 错误：`OSError: Multiple exceptions: [Errno 111] Connect call failed ('::1', 5433...), [Errno 111] Connect call failed ('127.0.0.1', 5433)`
- 分类：**环境问题**，不是 B4 修改引入的代码缺陷。
- 本批次未修改该测试，也未通过临时 skip 规避。

---

## 5. 35 分钟真实墙钟入口

入口：`tests/test_long_run_wall_clock.py`

默认运行：

```powershell
pytest -q --tb=short tests/test_long_run_wall_clock.py
```

结果：

- `1 skipped in 0.38s`
- 状态：**SKIP / NOT RUN**
- SKIP 原因：`未设置 RUN_WALL_CLOCK_30M=1：35 分钟真实墙钟测试未调度`

启用方式：

```powershell
$env:RUN_TEMPORAL_IT="1"
$env:RUN_WALL_CLOCK_30M="1"
pytest -q -m "wall_clock_30m"
```

说明：

- 真实任务默认使用 `WALL_CLOCK_SECONDS=2100`。
- 超时：`pytest.mark.timeout(2700)`。
- 如果启用后真实 Temporal 缺失或连接失败，测试直接 FAIL，不伪装 SKIP。
- 入口存在不等于真实墙钟验证通过。本批次未启动 35 分钟真实任务。

---

## 6. 静态检查

- 范围：B4 修改的配置、测试、脚本相关文件。
- 结果：**0 diagnostics**
- 状态：**PASS**

---

## 7. 四类结果汇总

| 项目 | 状态 |
|---|---|
| Windows 单次完整非 Temporal | PASS，219 passed / 25 deselected / 6 warnings / 240.28s |
| 真实 Temporal IT | PASS，24 passed / 220 deselected / 382 warnings / 1028.79s；退出码捕获不完整 |
| Linux 固定容器单次完整非 Temporal | FAIL，3 failed / 216 passed / 25 deselected / 1 warning / 55.08s / EXITCODE=1 |
| 35 分钟真实墙钟测试 | NOT RUN，入口 SKIP |
| 静态检查 | PASS，0 diagnostics |

---

## 8. 分批结果与单次结果分离说明

- B1/B2/B3 的分批验证记录属于历史批次证据，不替代 B4 单次全量结果。
- B4 Windows 单次完整非 Temporal 与 Linux 容器单次完整非 Temporal 分别记录。
- Linux 容器结果虽然失败，但仍是单次完整执行，不是拆批结果。
- 任何 NOT RUN / INCOMPLETE 项均未被写为 PASS。

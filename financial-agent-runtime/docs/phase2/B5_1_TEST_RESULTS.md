# B5.1 测试结果

日期：2026-11-21
说明：严格区分 PASS / FAIL / SKIP / NOT RUN。全部 pytest 均通过 `scripts/run_pytest_audited.ps1` 运行，保留 `.out` / `.err` / `.meta.out` 审计文件。审计脚本未修改。

---

## 0. 环境与前置

| 项 | 值 |
|---|---|
| 宿主机 Python | 3.14.7 |
| pytest | 8.4.2 |
| temporalio SDK | 1.36.1 |
| Temporal Server | `temporalio/server@sha256:7bf69188...`（1.32.2） |
| PersistenceStore / VisibilityStore | `postgres12` / `postgres12` |
| `TEST_DATABASE_URL` / `DATABASE_URL` | `postgresql+asyncpg://agent_app:agent_app@localhost:5433/agent_runtime` |
| `TEMPORAL_ADDRESS` | 测试默认 `localhost:7233`（SQL-backed Server） |

审计脚本用法：

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://agent_app:agent_app@localhost:5433/agent_runtime"
$env:DATABASE_URL=$env:TEST_DATABASE_URL
& ./scripts/run_pytest_audited.ps1 -LogPrefix "<name>" -Temporal -PytestArgs @("...")
```

---

## 1. 运行时手工验证（非 pytest）

| 验证 | 方法 | 结果 |
|---|---|---|
| schema 初始化 | `docker compose logs temporal-schema-init` | `Temporal SQL schema setup complete.`，容器 Exited 0 |
| namespace | `temporal operator namespace describe default` | Registered / retention 24h / cluster active |
| 双 store 类型 | `temporal operator cluster describe` | `PersistenceStore=postgres12`、`VisibilityStore=postgres12` |
| 库隔离 | `psql` 查询两实例 `pg_database` | 业务库仅 `agent_runtime`；Temporal 实例含 `temporal` + `temporal_visibility` |
| History 写入 | `psql` 查询 `temporal.history_node` / `executions`，`temporal_visibility.executions_visibility` | 均随 workflow 启动/完成增长，workflow show 可读事件 |
| Server 重启保留 History | stop/start temporal 后比对 RunId | RunId `01a1fd46-8af7-747b-b5ad-af019f66aec1` 的 2 个事件与原始时间戳 08:43:41 完整保留；store 仍为 postgres12 |
| Worker 重连 | 临时 worker 在 Server 重启后重建到 7233 的连接 | TCP `1C41` 建立、持续轮询；正式 IT 的 worker crash/restart 用例通过 |

状态：**PASS**

---

## 2. Temporal IT 冒烟：`test_graph_integration.py`

审计文件：`b51_temporal_smoke.{out,err,meta.out}`

```powershell
& ./scripts/run_pytest_audited.ps1 -LogPrefix "b51_temporal_smoke" -Temporal -PytestArgs @("tests/test_graph_integration.py")
```

元数据：

- START：`2026-11-21T16:49:49.9063041+08:00`
- END：`2026-11-21T16:53:45.9079057+08:00`
- ELAPSED：`00:03:56.0016016`
- EXITCODE：`0`
- TEMPORAL_SWITCH：`True`
- COMMAND：`python -m pytest -q --tb=short tests/test_graph_integration.py`

pytest：`7 passed, 229 warnings in 232.38s (0:03:52)`

结果：**PASS**。包含 `test_d_worker_crash_recovery`（worker 崩溃恢复 / history replay）。期间 `graph-it-*` workflow 全部 Completed。

---

## 3. 非 Temporal 全量

审计文件：`b51_non_temporal_full.{out,err,meta.out}`

```powershell
& ./scripts/run_pytest_audited.ps1 -LogPrefix "b51_non_temporal_full" `
  -MarkerExpression "not temporal_it and not wall_clock_30m"
```

元数据：

- START：`2026-11-21T16:54:13.2165486+08:00`
- END：`2026-11-21T16:58:23.4006270+08:00`
- ELAPSED：`00:04:10.1840784`
- EXITCODE：`0`
- MARKER：`not temporal_it and not wall_clock_30m`
- TEMPORAL_SWITCH：`False`

pytest：`219 passed, 25 deselected, 6 warnings in 246.67s (0:04:06)`

结果：**PASS**。与 B4.1 Linux 基线（219 passed / 25 deselected）一致，无新增失败。warnings 为既有的 `InsecureKeyLengthWarning`（JWT 短 key）与 asyncio/Pydantic 弃用警告。25 deselected = 24 Temporal IT + 1 wall-clock。

---

## 4. 其余 Temporal IT：parent-close / subagent / timeout-cancel

审计文件：`b51_temporal_rest.{out,err,meta.out}`

```powershell
& ./scripts/run_pytest_audited.ps1 -LogPrefix "b51_temporal_rest" -Temporal `
  -PytestArgs @("tests/test_parent_close_policy.py","tests/test_subagent_integration.py","tests/test_subagent_timeout_cancel.py")
```

元数据：

- START：`2026-11-21T16:58:39.8886280+08:00`
- END：`2026-11-21T17:14:48.7662896+08:00`
- ELAPSED：`00:16:08.8776616`
- EXITCODE：`0`
- TEMPORAL_SWITCH：`True`
- COMMAND：`python -m pytest -q --tb=short tests/test_parent_close_policy.py tests/test_subagent_integration.py tests/test_subagent_timeout_cancel.py`

pytest：`17 passed, 166 warnings in 966.21s (0:16:06)`

结果：**PASS**。覆盖 parent-close policy、subagent 生命周期/超时/取消、worker 重启（含 `tc-restart-*`）。运行期间 `pcp-*` / `subagent-*` / `timeout-cancel-it-*` workflow 持续完成，Server 无持续 error。

---

## 5. 汇总

| 项目 | 测试数 | 结果 | EXITCODE |
|---|---|---|---|
| Temporal IT 冒烟（graph） | 7 | PASS | 0 |
| 非 Temporal 全量 | 219（25 deselected） | PASS | 0 |
| Temporal IT 其余三项 | 17 | PASS | 0 |
| **Temporal IT 合计** | **24** | **PASS** | — |
| 35 分钟墙钟测试 | — | **NOT RUN**（未授权） | — |

- 三次审计运行的 START/END/ELAPSED/EXITCODE/COMMAND/MARKER/日志路径齐全。
- 无 FAIL、无 SKIP（除 marker deselect）、无临时 skip。
- warnings 分类同 B4.1：Pydantic deprecated、asyncio DeprecationWarning、`PytestUnraisableExceptionWarning`（workflow 事件循环，既有）、JWT 短 key。均非 B5.1 引入。
- 审计脚本在 PASS 场景稳定记录真实退出码；FAIL 场景自检已在 B4.1 完成，本批次不重复。

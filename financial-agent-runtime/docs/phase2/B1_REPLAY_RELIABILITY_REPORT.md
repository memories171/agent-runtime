# B1 Replay 可证明化实施报告

日期：2026-11-21
批次：B1（Replay 可证明化、原子化、可观测化）
状态：**实施完成，等待人工验收**
修改范围：`src/agent_runtime/outbox/commands.py`（1 个生产文件）+ `tests/test_replay_reliability.py`（1 个新测试文件）

---

## 1. Replay 恢复链路（审查结论）

### 1.1 已实现并工作（本批次不改）
- **Worker 重启审计**：`worker.py:61-110` `record_worker_reconnect(boot_id)` 启动时扫描 `RUNNING/WAITING_INPUT/WAITING_APPROVAL/PAUSED` 的 Run，以 `boot_id` 幂等键写 `worker_failures`（`recovery_position="temporal-history-replay"`）+ 4 事件 `WORKER_FAILED/TEMPORAL_HISTORY_REPLAY/WORKER_RECONNECTED/WORKFLOW_RESUMED`。
- **Replay 状态重建**：`workflow.py:49-76` 的 `_pause_requested/_cancel_requested/_processed_commands` 等由 Temporal history 重建；`orchestration/child_registry.py:3-5` child registry 由 history 中的 `start_child_workflow` 自然重建；时间和 ID 全部用 `workflow.now()/workflow.uuid4()`（`workflow.py` 内 0 处裸 `datetime.now/uuid4/random`）——本批次新增 AST 测试守护。
- **Activity 幂等**：`ToolInvocationService.begin()` 在 CONFIRMED 且 result 非空时复用缓存结果（`invocations.py:146-154`），不再调用 provider；fencing token = activity attempt（`activities.py:179`），`confirm()` 用 `fencing<=token` CAS（`:271-309`）。
- **Outbox 幂等**：`command_id` DB 唯一约束 + workflow 侧 `_processed_commands` 去重。
- **Workflow 内 failure/recovery**：`_record_failure`/`_mark_recovered`（`workflow.py:396-440`），`recovery_count≤3`，仅包 `agent_decide_activity`。

### 1.2 本批次发现的缺陷与修复

**缺陷 1（已修复）**：生产 `start_workflow` 未传 `execution_timeout`（`outbox/commands.py:123-126`）。workflow 内部 watchdog（`workflow.py:159-179`）只在 workflow 仍被调度时生效；若 worker 永久失联，workflow 可能无限期僵死。
- **最小修复**：新增纯函数 `workflow_start_kwargs(payload)`，从 `WorkflowInput.max_duration_seconds + 300s` 计算 `execution_timeout`，在 start 时传入。
- **不影响 replay 确定性**：这是客户端启动参数，不改变 workflow 内部 command sequence/activity 顺序。
- **回归测试**：`test_outbox_start_workflow_passes_execution_timeout`（纯单测）。

**缺口 2（本批次不修改，如实记录）**：`record_worker_recovery_activity`（`activities.py:391-451`）定义+注册但无调用点（dead code）。接入 workflow 会增加一次 Activity 调用，改变 history command sequence，必须用 `workflow.patched` 保护（当前全库 0 处）；且现有 `record_worker_reconnect` 已提供 boot 级审计。遵守"不破坏 Replay / 不无谓改动"约束，本批次不接线，列入遗留风险。

**缺口 3（本批次不修改）**：`workflow.py:484` 主路径读取外部配置 `settings.orchestration_child_cancel_grace_seconds`，配置漂移可能造成旧 history replay 分歧；属 B2+ 版本治理范畴。

### 1.3 明确未做（遵守约束）
- 未实现完整 Checkpoint Restore（决策 1 方案 B）。
- 未改 ACL/RBAC/ABAC/租户隔离/JWT 边界（Phase 1 保持）。
- 未改 max_steps（B2）、未做 UNKNOWN 自动对账（B3）、未做 Temporal 持久化迁移（B5）。
- 未新增数据库字段/迁移；未改 `workflow.py` 主循环；未新增 `workflow.patched`。

---

## 2. 必测故障场景与验证结果

| # | 场景 | 验证方式 | 结果 |
|---|---|---|---|
| 1 | Worker 在 Workflow 执行中重启 | B0 `test_graph_integration.py::test_d_worker_crash_recovery`（真实 Temporal，proc.kill + 新 worker）；`scripts/verify_worker_recovery.py` | PASS（B0 真实服务） |
| 2 | Worker 重启后 Workflow Replay | B0 `test_subagent_integration.py::test_subagent_worker_restart_recovery`、`test_subagent_timeout_cancel.py::test_worker_restart_durable_timer_recovers_timeout` | PASS（B0 真实服务） |
| 3 | Activity 执行失败后重试 | `scripts/fault_injection/fixture_workflows.py::FISlowWorkflow` + `runner.py::check_slow`（heartbeat+retry，手工脚本） | 部分（缺 pytest，见遗留） |
| 4 | Activity 成功但响应丢失重复调用保护 | B1 `test_stale_fencing_rejected_and_confirmed_reused` CONFIRMED reuse 断言 + `scripts/fault_injection/runner.py::start-effect/check-effect` | PASS（pytest 真实 PG） |
| 5 | Outbox 重复投递 | B1 `test_outbox_command_id_unique_constraint_rejects_duplicate`（DB 唯一约束）+ `scripts/fault_injection/runner.py::test5_dispatcher_crash` | PASS（pytest+脚本） |
| 6 | 过期 fencing token 写入拒绝 | B1 `test_stale_fencing_rejected_and_confirmed_reused`（token=0 被拒）+ `scripts/verify_invocation_journal.py::stale_confirm_rejected` | PASS（pytest 真实 PG） |
| 7 | 恢复前后业务状态一致性 | B0 worker 重启用例（终态/结果不重复）+ B1 Replay 确定性 AST 守护 | PASS |
| 8 | Checkpoint 完整性校验 | `tests/test_checkpoint_snapshot.py::test_corrupted_blob_rejected`（B0/B1 均通过）+ `api.py:308-316` resume 门禁 | PASS |
| 9 | 恢复失败后状态记录与审计 | `scripts/verify_recovery_events.py` + `activities.py:414-419 RECOVERY_AUTH_FAILED`（手工脚本验证） | 部分（缺 pytest，见遗留） |

**time-skipping 与真实服务区分**：本批次未使用 time-skipping（全库 0 命中）。场景 1、2、7 的证据来自 B0 的真实 Temporal 集成测试（19m01s，非模拟时间）；场景 4、5、6、8 为真实 PostgreSQL pytest；场景 3、9 由手工故障注入脚本覆盖。**没有用模拟时间测试替代真实故障恢复证据。**

---

## 3. 状态一致性分析

- **Workflow 状态**：由 Temporal history replay 重建（signal/activity 结果/Bounded context），本批次 AST 测试证明 workflow 主模块无非确定性 API。
- **业务 DB 状态**：由 Activity 幂等/CAS 保证——`begin()` CONFIRMED 复用、fencing token 阻止旧 attempt 覆盖、Outbox command_id 去重。恢复前后一致性由 B0 真实 worker 重启用例断言（completed 不被 overwritten、无重复 child）。
- **Checkpoint**：不参与 replay；仅 `/resume` 前做完整性门禁（与决策 1 一致）。

---

## 4. Outbox 与 fencing token 分析

- **Outbox**：`command_id` 唯一约束（`db.py:61-65`）+ DISPATCHING lease 60s + 退避 `min(60,2^n)` + 20 次死信；重复投递由 workflow `_processed_commands` 去重。B1 新增测试确认唯一约束存在。
- **fencing token**：activity attempt（`activities.py:179`）经 CAS `fencing<=token` 保护；B1 测试证明 token=0 被拒、CONFIRMED 后复用结果不重复执行副作用。
- **已知遗留**：fencing 人工 reconcile 用 `+1` 与 attempt 共用数值空间（B3 修复）；`_mark_unknown` SQL 无状态条件（B3）；`run_events` 无幂等键（需迁移，未批准）。

---

## 5. 修改文件清单
见 `B1_CHANGELOG.md`。

---

## 6. 测试分类
见 `B1_TEST_RESULTS.md`。

---

## 7. 遗留风险
1. `record_worker_recovery_activity` 仍为 dead code；workflow 级恢复审计缺 activity 级证据（需 `workflow.patched`，B2+）。
2. 生产 workflow 无 `workflow.patched` / `WorkflowReplayer`；旧 history replay 兼容性无自动化证据。
3. Activity retry / 恢复失败审计缺 pytest（仅手工脚本）；建议 B4 补。
4. `workflow.py:484` 外部配置读取的 replay 漂移风险。
5. fencing 数值空间碰撞、`run_events` 无幂等键（B3/迁移）。
6. 全量非 Temporal 套件在本环境单次运行会在约 37%（`test_graph_core` 附近）出现会话级卡住（无生产代码缺陷证据：同组文件隔离运行 43/65/62 passed 全部通过），采用分批独立进程完成验证；建议 CI 环境（Linux + 固定依赖）复核。

---

## 8. 是否具备进入 B2 的条件
**具备（有条件）**。B1 的生产改动仅限 outbox 启动参数，不触碰 workflow 历史；Replay 主路径未被修改；B0 的 188 基线安全修复与 4 个新测试均通过。建议在进入 B2 前：(a) 在稳定 CI 环境单次跑通 192 个非 Temporal 用例以消除会话卡住疑虑；(b) B2 实施 max_steps 联动时同步评估 `workflow.py:484` 配置漂移问题。

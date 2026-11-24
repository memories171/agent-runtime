# B7 实施报告：完整 Checkpoint Restore 最小实现

日期：2026-11-23
状态：**IMPLEMENTED / PASS**（最小实现）。B7 原决策为"默认暂缓"，本批次经人工明确授权后实施。完成后停止，等待人工验收。

---

## 1. 结论

已实现"指定 checkpoint 直接恢复"的完整路径（方案 C 的最小版本），并保持诚实边界：

- 新增 `POST /runs/{run_id}/restore`：从已验证的 checkpoint **创建一个新 Run**（新 run_id、新 Temporal workflow），原 Run 与其 history 不被修改。
- 冻结的、版本化的 `RestoreContract (v1.0)` 随 `WorkflowInput` 进入新 workflow，由 `workflow.patched` 守卫以保证旧 history 可 replay。
- 业务状态确定性注入：history / tool_calls / llm_calls / plan / context cursor / 预算等。
- 副作用幂等：checkpoint 之前已 CONFIRMED 的 tool_result 通过契约带入，恢复后的 workflow 对同一逻辑调用**复用记录结果，不重放外部副作用**；"CONFIRMED 但 result=None"边界被明确排除，绝不复用。
- tenant / ACL / capability 一致性在 API 边界与恢复审计 activity 双重校验。
- 恢复事件 `CHECKPOINT_RESTORE_STARTED` 带 `recovery_source=checkpoint_restore`，幂等记录。

---

## 2. 关键设计决策

### 2.1 恢复到新 Run，而非改写原 history

这是保证 state 与 history 一致的最简单可靠方式。恢复点是一个新执行的起点；原 Run 保持不变，可对照审计。这与方案 C 文档 "注入新 workflow state" 一致。

### 2.2 版本化契约 `RestoreContract v1.0`

| 字段 | 内容 |
|---|---|
| `source` | source_run_id、checkpoint_id、sequence、tenant_id、state_digest |
| `state` | 完整 `AgentStateSnapshot` |
| `confirmed_tool_results` | `tool:call_id → tool_result` 映射（仅含带 result/error 的） |
| `capability_token_id` / `restored_by_user_id` | 冻结的能力与发起人 |

### 2.3 注入边界（明确不恢复的字段）

workflow 内部控制态（pause/resume/cancel signals、`_child_registry`、`_shutdown_reason`、pending signals）**不注入**——由新 workflow 自己的生命周期重建。这是方案 C 风险清单中"state 与 history 不一致"的最小化处理。

### 2.4 注入的字段

- 业务历史与调用记录：`history`、`tool_calls`、`llm_calls`、`tool_intents`、`approval_bindings`
- 计划：`prompt/acceptance_criteria/plan/completed_steps/pending_steps/current_node`
- 上下文：`context_summary`、`context_event_cursor`、`context_provenance`
- 执行：`current_step`、`last_completed_step`、`current_step_state`、`unconfirmed_tool_calls`、`status`
- 预算：`max_duration_seconds`、`used_duration_seconds`、`max_tokens`、`token_input`（=快照 used_tokens 合计）、`remaining_steps`
- 能力一致性：快照 principal 的 `organization_id/roles/permissions` 带入，但 `user_id` 保留为当前调用者

说明：快照 `budget_state` 只存合并的 `used_tokens`（input+output），恢复时放入 `token_input` 作为合计；input/output 拆分信息在快照层不可得（记为遗留项）。

---

## 3. 副作用幂等（核心）

`build_restore_contract()` 从快照 history 中提取 `tool_result`：
- 只在带 `result` 或 `error` 时才加入 `confirmed_tool_results`；
- 以 `{tool}:{call_id}` 为稳定 key（无 call_id 时用 `idx-N`，因快照只保留最近 16 条，index 在同一快照内稳定）。

恢复 workflow 遇到 tool_call 时：若 `reuse_key` 在已确认集合内，则：
- 复用记录的 observation（追加 `restored_from_checkpoint=True`）；
- 写 `TOOL_RESULT_RESTORED` 事件；
- 记录 tool_call 状态 `RESTORED_REUSED`；
- **跳过** `execute_tool_activity`，不产生第二次外部副作用。

---

## 4. 校验与测试

| 验证 | 结果 |
|---|---|
| B7 单元（契约/注入/幂等边界/replay 守卫） | `17 passed`（含既有 checkpoint/replay 测试），EXITCODE=0 |
| 全量非 Temporal 回归 | `227 passed, 25 deselected`（221 基线 + 6 新增 B7 用例），EXITCODE=0 |
| 运行容器加载新代码 | api 容器内 restore 路由存在；worker 已连接 Temporal(7233)+Postgres(5432) |
| 服务健康 | API `/health` 200；Temporal `postgres12`/`postgres12` |

未做：API 级真实 checkpoint → restore → workflow 运行的端到端测试（需完整 DB fixture：租户成员、RunRecord、已持久化 checkpoint；本最小实现以契约/注入单测 + 静态守卫 + 全量回归覆盖，记为遗留项）。

---

## 5. 交付物

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/agent_runtime/checkpoint/restore_contract.py` | 新增 | `RestoreContract`、`build_restore_contract`、`inject_restore_state` |
| `src/agent_runtime/contracts.py` | 修改 | `RestoreRequest`；`WorkflowInput.restore_contract` |
| `src/agent_runtime/api.py` | 修改 | `POST /runs/{run_id}/restore` |
| `src/agent_runtime/workflow.py` | 修改 | 状态注入 + confirmed 复用分支 + replay 守卫 |
| `src/agent_runtime/activities.py` | 修改 | `record_checkpoint_restore_activity`（成员校验 + 幂等） |
| `src/agent_runtime/worker.py` | 修改 | 注册新 activity |
| `tests/test_checkpoint_restore.py` | 新增 | 6 个 B7 回归用例 |
| `docs/phase2/B7_*.md` | 新增 | 6 份文档 |

未修改 B0–B6 的任何实现、测试断言、部署配置。

---

## 6. 边界

- 未实现跨环境 restore、checkpoint 编辑、选择性 step 回放（超出最小范围）。
- 未改写原 Run 的 Temporal history。
- 权限：`run:restore` + `agent:execute` + active tenant membership 三重校验。
- 完成后停止，等待人工验收；下一步按用户指示进行上线治理。

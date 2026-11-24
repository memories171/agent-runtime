# B7 变更清单

日期：2026-11-23
范围：完整 Checkpoint Restore 最小实现（方案 C）。

---

## 1. 新增文件

| 文件 | 说明 |
|---|---|
| `src/agent_runtime/checkpoint/restore_contract.py` | `RESTORE_CONTRACT_VERSION="1.0"`、`RestoreSource`、`RestoreContract`、`build_restore_contract()`、`inject_restore_state()` |
| `tests/test_checkpoint_restore.py` | 6 个用例：契约版本/来源、CONFIRMED 无结果边界、状态注入、Pydantic 往返、`workflow.patched` 静态守卫、副作用复用分支静态守卫 |
| `docs/phase2/B7_IMPLEMENTATION_REPORT.md` | 实施报告 |
| `docs/phase2/B7_CHANGELOG.md` | 本清单 |
| `docs/phase2/B7_TEST_RESULTS.md` | 测试结果 |
| `docs/phase2/B7_RESOURCE_USAGE.md` | 资源与成本 |
| `docs/phase2/B7_ROLLBACK_GUIDE.md` | 回滚指南（治理前） |
| `docs/phase2/B7_LEFTOVER_ISSUES.md` | 遗留问题 |

---

## 2. 修改文件

### `src/agent_runtime/contracts.py`
- 新增 `RestoreRequest`（`checkpoint_id`、`checkpoint_sequence`、`agent_id`、`reason`）
- `WorkflowInput` 新增 `restore_contract: dict[str, Any] | None = None`（可选，旧 history 无此字段仍能 replay）

### `src/agent_runtime/api.py`
- 模块级导入 `RestoreRequest`
- 新增 `POST /runs/{run_id}/restore`：scope `run:restore` + `agent:execute` + active membership + source run 归属校验 → `CheckpointSnapshotService.load()`（可指定 checkpoint_id/sequence）→ tenant 一致性校验 → 构建 RestoreContract → 创建新 RunRecord + WorkflowOutbox(START_WORKFLOW) + `RESTORE_REQUESTED` 事件。

### `src/agent_runtime/workflow.py`
- 新增模块级 `_restored_step_number()` 辅助函数
- 导入 `RestoreContract`、`inject_restore_state`、`record_checkpoint_restore_activity`
- `run()` 开头在 `workflow.patched("b7-checkpoint-restore-v1")` 内注入快照状态、重建 tool_calls/llm_calls 引用、step、token 计数、confirmed keys
- `RUN_STARTED` 后调用 `record_checkpoint_restore_activity` 写审计
- tool_call 分支：若命中 restored confirmed key，复用 observation 并 `continue`，记录 `TOOL_RESULT_RESTORED` + `RESTORED_REUSED`

### `src/agent_runtime/activities.py`
- 新增 `record_checkpoint_restore_activity`：校验 active user + tenant membership；用 payload JSONB 包含 `checkpoint_id + recovery_source` 做幂等；写 `CHECKPOINT_RESTORE_STARTED`（含 source/checkpoint/sequence/digest/recovery_source）

### `src/agent_runtime/worker.py`
- 导入并注册 `record_checkpoint_restore_activity`

---

## 3. 未修改项

- B0–B6 的实现、文档、测试断言
- `docker-compose*.yml`、Temporal/Staging 配置
- 前端代码
- `scripts/run_pytest_audited.ps1`

---

## 4. 实施中修复

1. 测试 helper 误用 `build_envelope_fields` 签名 → 改为直接构造 `CheckpointEnvelope`。
2. `completed_steps` 需为 `dict` 列表（Pydantic `PlanState`）→ 修正测试数据。
3. `budget_state.used_tokens` 是 input+output 合计 → 注入到 `token_input` 作为合计，测试断言同步。
4. `append_event` 无 `idempotency_key` 参数、`RunEvent` 无该列 → 恢复审计改用 payload JSONB 包含判断幂等。
5. PowerShell `$ref` 转义问题（验证 OpenAPI 时）→ 简化检查脚本。

---

## 5. 影响评估

- 新增 API 路由 `POST /runs/{run_id}/restore`，需 `run:restore` scope（新增权限点）。
- 正常 Run 不受影响：`restore_contract=None` 时注入分支不执行；`workflow.patched` 保证旧 history 可 replay。
- 全量回归 227 passed，无失败。

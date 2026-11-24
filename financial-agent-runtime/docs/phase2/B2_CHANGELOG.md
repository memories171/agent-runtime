# B2 变更清单

日期：2026-11-21
范围：5 个生产文件 + 1 个新测试文件；未改 Git / 未上传 / 未改数据库结构 / 未执行迁移。

---

## 1. 生产代码（5 个文件）

### `src/agent_runtime/config.py`
- 新增 `estimated_seconds_per_step: int = 60`（duration→steps 换算基准，含注释）。

### `src/agent_runtime/api.py`
- 新增 `import math`。
- 新增常量 `MAX_STEPS_HARD_LIMIT = 200`。
- 新增纯函数 `resolve_step_budget(duration_seconds, requested_steps, default_steps, seconds_per_step) -> (effective, adjustment|None)`：
  - `required = ceil(duration / sps)`；`required > 200` → `ValueError`（含说明）。
  - `effective = min(200, max(original, required))`。
  - 上调时返回 `{original, adjusted, reason="duration_budget", duration_seconds, required_steps}`。
- `create_run`：用 resolver 替换原 `max_steps = body.max_steps or settings...`；`ValueError` → `HTTPException(400)`；上调时 `append_event("STEP_BUDGET_ADJUSTED", adjustment)`；`WorkflowInput` 增传 `child_cancel_grace_seconds=settings.orchestration_child_cancel_grace_seconds`。

### `src/agent_runtime/contracts.py`
- `WorkflowInput` 新增 `child_cancel_grace_seconds: int | None = None`（冻结配置，默认 None 兼容旧 history）。

### `src/agent_runtime/workflow.py`
- L484：由直接读 `settings.orchestration_child_cancel_grace_seconds` 改为优先读 `run_input.child_cancel_grace_seconds`，None 时回退 settings（避免 replay 漂移）。

### `src/agent_runtime/graph/runner.py`
- L574：`"max_steps": settings.graph_max_node_steps` → `"max_steps": limits.max_node_steps`（尊重 `graph_config.limits`）。

---

## 2. 新增测试（1 个文件）

### `tests/test_step_budget.py`（新建，13 个测试）
覆盖：短任务、30min 预算、自动上调与记录、200 上限、超 200 ValueError/HTTP 400、不同 duration 一致性、sps 校验、RunCreate 契约、WorkflowInput 冻结字段、Graph 步数源码守护、workflow 确定性 AST、HTTP 400 映射。

---

## 3. 未修改内容（合规声明）
- Phase 1 安全修复文件（6 生产 + 5 测试）：0 改动。
- B0（6 文件）、B1（outbox/commands.py + test_replay_reliability.py）：0 改动。
- ACL/RBAC/ABAC/租户隔离/JWT：0 改动。
- UNKNOWN 对账（B3）、Temporal 持久化（B5）、Checkpoint Restore：0 改动。
- 数据库迁移/结构/运行数据：0 改动（测试清理残留数据属测试环境，非结构变更）。
- Git：未初始化、未上传。

---

## 4. 回滚说明
- 逐文件独立回滚；删除 `tests/test_step_budget.py` 不影响功能。
- 回滚 `api.py` 需移除 resolver 调用（恢复 `max_steps = body.max_steps or settings.max_agent_steps`）、移除 audit 事件与冻结字段传参。
- 回滚 `workflow.py`/`contracts.py`/`graph/runner.py` 为单行/单字段还原。
- 无 DB schema 变更，回滚无数据风险。

# B1 变更清单

日期：2026-11-21
范围声明：仅 2 个文件（1 生产 + 1 新测试）；未改 Git / 未上传；未改数据库 / 迁移 / ACL / 安全配置。

---

## 1. 生产代码（1 个文件）

### `src/agent_runtime/outbox/commands.py`
改动 1（新增常量，模块顶部）：
```python
WORKFLOW_EXECUTION_TIMEOUT_MARGIN_SECONDS = 300
```
改动 2（START_WORKFLOW 分支，`dispatch_commands_once` 内）：
```python
# 之前
await client.start_workflow(AgentRunWorkflow.run, payload, id=workflow_id, task_queue=_task_queue())
# 之后
await client.start_workflow(
    AgentRunWorkflow.run, payload, id=workflow_id,
    task_queue=_task_queue(),
    **workflow_start_kwargs(payload),
)
```
改动 3（文件末尾新增纯函数）：
```python
def workflow_start_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    return {"execution_timeout": timedelta(
        seconds=int(payload["max_duration_seconds"]) + WORKFLOW_EXECUTION_TIMEOUT_MARGIN_SECONDS
    )}
```

**成因**：生产启动未设 `execution_timeout`，永久失联的 worker 会使 workflow 无限僵死；内部 watchdog 无法兜底。
**影响**：仅 START_WORKFLOW 的 Temporal 启动参数；不改 workflow 内部逻辑/history；不改 ACL/权限。
**回滚**：删除上述 3 处改动（恢复原 `start_workflow` 调用），无数据风险。

---

## 2. 新增测试（1 个文件）

### `tests/test_replay_reliability.py`（新建，4 个测试）
1. `test_outbox_start_workflow_passes_execution_timeout`：纯单测，断言 `workflow_start_kwargs` 返回 `max_duration+300` 的 `execution_timeout`。
2. `test_workflow_uses_deterministic_time_and_id_apis`：AST 解析 `workflow.py`，断言无裸 `uuid4/time/monotonic/random`。
3. `test_outbox_command_id_unique_constraint_rejects_duplicate`：断言 `WorkflowOutbox` 的 `command_id` 唯一约束存在（模型级）。
4. `test_stale_fencing_rejected_and_confirmed_reused`：真实 PG，begin→stale confirm(0) 拒→valid confirm(2)→re-begin 复用结果（无重复副作用）。

**未新增**：DB 字段/迁移、workflow 代码、ACL 修改、生产 workflow 的 activity 调用。

---

## 3. 未修改内容（合规声明）
- `src/agent_runtime/workflow.py`、`activities.py`、`worker.py`、`recovery/`、`approval/`、`checkpoint/`：0 改动。
- 数据库迁移与运行数据：0 改动。
- Phase 1 安全修复文件（6 生产 + 5 测试）：0 改动。
- B0 的 6 个测试基础设施文件：0 改动。
- max_steps / UNKNOWN 对账 / Temporal 持久化：0 改动（留 B2/B3/B5）。
- Git：未初始化、未配置 Remote、未上传。

---

## 4. 回滚说明
- 回滚单位：两个文件可独立删除/还原。
- 删除 `tests/test_replay_reliability.py` 不影响任何功能；还原 `outbox/commands.py` 的 3 处改动恢复原启动行为（失去 execution_timeout 兜底）。
- 无数据库/外部状态变更，回滚无数据风险。

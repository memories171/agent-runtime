# B7 回滚指南（治理前）

日期：2026-11-23
范围：Checkpoint Restore 最小实现。注意：本指南针对 B7 代码；后续"上线治理"可能引入部署配置变更，届时需单独回滚指引。

---

## 1. 快速回滚：禁用 restore 能力（不改代码）

B7 的能力由 API 路由和 workflow 注入分支组成。运行时"关闭"方式：

- 权限层：从调用者 scopes 中移除 `run:restore`，`POST /runs/{run_id}/restore` 将被 403 拒绝。正常 Run 路径完全不受影响。
- 不调用 restore 端点即可；`restore_contract=None` 的 Run 走原有逻辑，`workflow.patched` 保证旧 history 正常 replay。

---

## 2. 代码回滚（如需彻底移除）

若需从源码移除 B7（通常在确认有严重问题时）：

1. 从 `src/agent_runtime/api.py` 删除 `restore_run` 端点与 `RestoreRequest` 导入。
2. 从 `contracts.py` 删除 `RestoreRequest` 与 `WorkflowInput.restore_contract`。
3. 从 `workflow.py` 删除注入块、`_restored_step_number`、confirmed 复用分支、restore 审计调用；保留其余不变。
4. 从 `activities.py` / `worker.py` 删除 `record_checkpoint_restore_activity` 定义与注册。
5. 删除 `src/agent_runtime/checkpoint/restore_contract.py` 与 `tests/test_checkpoint_restore.py`。
6. 重建 api/worker：
   ```powershell
   docker compose -f docker-compose.yml build api worker
   docker compose -f docker-compose.yml up -d --no-deps --force-recreate api worker
   ```
   注意：务必加 `--no-deps`，避免误重建 Temporal（见 B5.1/B7 实施教训）。

---

## 3. 数据影响

- B7 只**新增** Run（`restored_from_run_id` / `recovery_source=checkpoint_restore` 元数据）与事件 `CHECKPOINT_RESTORE_STARTED` / `RESTORE_REQUESTED`；不修改原 Run 或原 checkpoint。
- 回滚代码不会删除已产生的 restore Run/事件；如需清理，按 tenant 数据治理流程处理，不要在未确认前删库删表。
- `run:restore` scope 是新增权限点；回滚后该 scope 变为无意义，可保留（无害）。

---

## 4. 回滚验证清单

- [ ] 移除 `run:restore` scope 后 restore 端点返回 403
- [ ] 正常 Run 创建/执行不受影响（全量回归 221 基线）
- [ ] 旧 history replay 不因 `workflow.patched` 分支报错
- [ ] 若彻底回滚代码：重建 api/worker 后 `/runs/{run_id}/restore` 不在 OpenAPI 中
- [ ] Temporal store 仍为 postgres12（未被误重建影响）

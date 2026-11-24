# B3 变更清单

日期：2026-11-21
范围：3 个生产文件 + 1 个新增测试文件 + 4 份交付文档；未改数据库结构，未执行迁移，未操作 Git 远端。

---

## 1. 生产代码

### `src/agent_runtime/recovery/invocations.py`

- 新增常量 `HUMAN_RECON_FENCING_BASE = 1_000_000`。
- 新增 `human_reconciliation_fencing_token(record)`：人工裁决 token 使用高位 namespace。
- 新增 `activity_fencing_token(record, attempt)`：人工裁决后把后续 Activity attempt 平移到高位 namespace。
- 新增 `ToolInvocationService.fencing_token_for_attempt()`：在 `begin()` 前读取记录并解析正确 token。
- `_mark_unknown()`：增加 `status == record.status` CAS。
- `require_reconciliation()`：幂等化；允许 `RECONCILIATION_REQUIRED` / 终态安全返回；仅 `UNKNOWN` 可进入待对账；并发成功也视为幂等。
- `reconcile()`：重复同决策返回 `reused=True`；不再重复写终态或重复发事件；保留 CAS。

### `src/agent_runtime/activities.py`

- `execute_tool_activity()`：
  - 原始 attempt 保存在 `attempt`；
  - 调用 `fencing_token_for_attempt()` 后再进入 `begin()`；
  - `lease_owner` 使用解析后的 fencing token。
- 通用异常路径由仅 `mark_unknown()` 改为 `mark_unknown()` + `require_reconciliation()` + `commit()`。
- 异常继续上抛；不产生自动 SUCCESS/FAILED 结论。

### `src/agent_runtime/platform_api.py`

- 新增导入 `human_reconciliation_fencing_token`。
- `/runs/{run_id}/reconcile` 的 `fencing_token` 从 `record.fencing_token + 1` 改为 `human_reconciliation_fencing_token(record)`。
- `_owned_run()`、scope 校验、404/409 语义保持不变。

---

## 2. 新增测试

### `tests/test_invocation_recovery.py`（新建，14 个测试）

覆盖：

1. Activity 异常路径把 UNKNOWN 升级为 `RECONCILIATION_REQUIRED`。
2. UNKNOWN 正常发现。
3. `require_reconciliation()` 幂等。
4. UNKNOWN 未经人工确认不能自动终结。
5. 人工对账正常执行。
6. 重复对账返回 `reused=True` 且无重复 `TOOL_RECONCILED` 事件。
7. 旧 Worker 低号 fencing 失败。
8. 人工判定 FAILED 后新 Worker 高位 attempt 可安全重试并正常完成。
9. 并发人工对账只有一个终态胜者。
10. 跨租户 reconcile 被隔离。
11. reconcile API 跨用户 404。
12. reconcile API 跨租户 404。
13. reconcile API 缺少 approval/manage scope 返回 403。
14. 有权限 owner 走高位 fencing namespace；Replay AST 守护保持干净。

---

## 3. 交付文档

- `docs/phase2/B3_IMPLEMENTATION_REPORT.md`
- `docs/phase2/B3_CHANGELOG.md`
- `docs/phase2/B3_TEST_RESULTS.md`
- `docs/phase2/B3_LEFTOVER_ISSUES.md`

---

## 4. 未修改内容

- 数据库 schema、字段、迁移：0 改动。
- B2 步骤预算规则：0 改动。
- Phase 1 ACL/JWT/RBAC/ABAC 生产代码：0 改动。
- Git：未初始化、未配置 remote、未提交、未上传。
- 现有测试：未删除，未放宽。

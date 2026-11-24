# B3 遗留问题与风险

日期：2026-11-21
状态：B3 实施与分批验证完成，等待人工验收；未进入 B4。

---

## 1. 真实 Temporal 集成测试本批次 NOT RUN

- B3 没有重新执行 24 个真实 Temporal IT 用例。
- 已有证据：B1 的 AST/确定性守护、真实 PG CAS 测试、B0 的历史 Temporal 24 passed。
- 风险：Activity fencing namespace 切换逻辑已通过模拟 Activity 上下文和真实 PG 验证，但没有在本批次跑完整 Temporal workflow replay。
- 建议：人工验收后如需更高置信度，单独开启 `RUN_TEMPORAL_IT=1` 运行 Temporal 批次；不得把 B0 证据改写为 B3 PASS。

---

## 2. 全量单次非 Temporal 套件未取得稳定通过证据

- B2 已记录全量单次会话在约 37% 位置卡住。
- 本批次继续使用分批独立进程，最终已执行 136 个相关测试且 0 FAIL。
- 风险：没有“一条命令全量通过”的证据。
- 处理：保持如实记录，不删除测试，不伪造 PASS。

---

## 3. `append_event()` 仍无全局幂等键

- 人工对账幂等通过 `reconcile()` 在重复请求时提前返回 `reused=True` 实现。
- 已验证重复同决策不会产生第二个 `TOOL_RECONCILED` 事件。
- 风险：若未来新增其他调用方绕过 `reconcile()` 直接写事件，仍可能重复审计。
- 本批次不改 `append_event()`，避免扩大范围。

---

## 4. `ToolInvocation.run_id` 没有外键级联

- 现有表结构中 `tool_invocations.run_id` 无 FK，`DELETE FROM agent_runs` 不会级联清理 invocation 行。
- B3 测试使用随机 tenant 与随机 idempotency key，避免相互污染。
- 风险：长期运行可能积累历史 invocation 行。
- 本批次遵守“不新增字段、不迁移”，未处理。

---

## 5. `require_reconciliation()` 只在通用异常路径接线

- `PermissionError` / `ToolDenied` / `ValueError` 仍按原设计 `mark_failed()`，因为这是 provider 明确失败，不是 outcome unknown。
- 只有不确定异常才进入 UNKNOWN → `RECONCILIATION_REQUIRED`。
- 风险：若某些 provider 把超时包装成 `ValueError`，可能不会进入人工对账。
- 本批次不改变现有异常分类，避免影响 Phase 1 已验收行为。

---

## 6. fencing namespace 依赖 Temporal attempt 单调递增

- 当前设计依赖 `activity.info().attempt` 在重试时递增。
- 人工裁决后，旧 attempt 使用低号空间并被拒绝，下一次 attempt 被提升到高位空间。
- 风险：若 Temporal attempt 语义或 Activity 调度方式未来变化，需要重新核对 `activity_fencing_token()`。
- 已增加纯函数测试守护该映射。

---

## 7. 未修改 API schema 与权限模型

- `/runs/{run_id}/reconcile` 继续使用既有 `ReconcileRequest` 字段。
- scope 仍为 `approval:approve` 或 `runs:manage:tenant`。
- 未新增用户可见字段，未改变 JWT/ACL 逻辑。
- 风险：如果未来需要记录更丰富的人工裁决证据，需要另行设计 schema/API，并按流程审批。

---

## 8. 停止条件

B3 所有要求已完成并验证。当前停止，等待人工验收。未经人工明确批准，不进入 B4，不开展其他批次。

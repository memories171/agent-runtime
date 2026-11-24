# B3 实施报告：UNKNOWN 人工对账闭环

日期：2026-11-21
范围：Phase 2 - B3。完成后停止，等待人工验收；未进入 B4。

---

## 1. 结论

B3 已完成实施与验证。UNKNOWN 现在可以从 Activity 异常路径被稳定发现，进入 `RECONCILIATION_REQUIRED` 等待人工裁决；人工对账通过高位 fencing namespace 终结，并且具备幂等性、租户隔离、旧 Worker 防护和安全重试路径。

本批次没有新增数据库字段，没有执行迁移，没有自动把 UNKNOWN 终结为 SUCCESS/FAILED，没有修改 B2 步骤预算规则，也没有修改 Phase 1 ACL/JWT 安全逻辑。

---

## 2. 实施前审查证据

1. **UNKNOWN 状态流转不完整**
   - `ToolInvocationService` 已有 `UNKNOWN` 状态。
   - `require_reconciliation()` 已定义，但生产代码中没有调用点。
   - `execute_tool_activity()` 的通用异常路径只调用 `mark_unknown()`，结果停留在 `UNKNOWN`，不会被人工对账界面可靠发现。

2. **终态可能被覆盖**
   - `_mark_unknown()` 原条件只有 invocation 与 fencing，没有 `status` CAS，可能与并发确认/失败写入竞争。

3. **fencing token 数值空间碰撞**
   - Activity token 来自 `activity.info().attempt`（1、2、3...）。
   - 人工 API 原使用 `record.fencing_token + 1`。
   - CAS 语义为 `fencing_token <= token`，人工写 token=2 后，attempt=2 可等值通过 `begin()`/`confirm()` 覆盖人工结论。

4. **DB 字段足够复用**
   - `status`、`reconciliation_status`、`reconciliation_note`、`fencing_token`、`attempt_count` 已足以支撑最小闭环，无需 schema 变更。

---

## 3. 生产代码实施

### 3.1 `src/agent_runtime/recovery/invocations.py`

- 新增 `HUMAN_RECON_FENCING_BASE = 1_000_000`。
- 新增 `human_reconciliation_fencing_token(record)`：返回 `1_000_000 + attempt_count + 1`。
- 新增 `activity_fencing_token(record, attempt)`：
  - 人工裁决前：Activity 使用原始 Temporal attempt。
  - 人工裁决后：同一 attempt 被平移到高位 namespace，即 `1_000_000 + attempt`。
  - 这样旧 Worker 的低号 attempt 无法通过 CAS，新的下一次 attempt 仍可显式重试。
- 新增 `ToolInvocationService.fencing_token_for_attempt()`，供 Activity 在 `begin()` 前解析正确 namespace。
- `_mark_unknown()` 增加 `ToolInvocation.status == record.status` CAS，避免覆盖并发终态。
- `require_reconciliation()`：
  - 已处于 `RECONCILIATION_REQUIRED` 或终态时直接返回；
  - 仅允许 `UNKNOWN` 进入待对账；
  - rowcount 失败后重查，若已被并发置为 `RECONCILIATION_REQUIRED` 也视为成功。
- `reconcile()`：
  - 重复同决策请求返回 `reused=True`，不重复写终态或审计事件；
  - 只允许 `UNKNOWN` / `RECONCILIATION_REQUIRED` 被人工终结；
  - 写 `RESOLVED`、`reconciliation_note`、`completed_at` 并发出 `TOOL_RECONCILED`。

### 3.2 `src/agent_runtime/activities.py`

- 保留原始 `attempt = int(activity.info().attempt)`。
- 在 `begin()` 前通过 `fencing_token_for_attempt()` 解析 fencing token。
- 通用异常路径改为：
  1. `mark_unknown()`
  2. `require_reconciliation()`
  3. `commit()`
- 异常仍向上抛出；代码不推断 SUCCESS/FAILED，不自动终结 UNKNOWN。

### 3.3 `src/agent_runtime/platform_api.py`

- `/runs/{run_id}/reconcile` 保持 `_owned_run()` 与 scope 校验不变。
- 人工裁决 token 改用 `human_reconciliation_fencing_token(record)`，不再使用 `record.fencing_token + 1`。
- 保留 403 scope 拒绝与 404 invocation 不存在语义。

---

## 4. 验证证据摘要

- B3 专项：`tests/test_invocation_recovery.py`，真实 PostgreSQL，**14 passed**。
- B1/B2/B3 核心回归：`test_replay_reliability.py`、`test_step_budget.py`、`test_invocation_recovery.py`，**31 passed**。
- ACL/JWT/权限回归：8 个文件，**55 passed**。
- 真实 PG capability/RLS/租户隔离：3 个文件，**14 passed**。
- Graph 确定性/Checkpoint/Graph core：3 个文件，**36 passed**。
- `read_lints` 对 3 个生产文件和 1 个测试文件返回 0 diagnostics。

详细命令、分类和环境说明见 `B3_TEST_RESULTS.md`。

---

## 5. 约束合规

- 未新增数据库字段；未执行迁移。
- 未自动终结 UNKNOWN。
- 未修改 B2 步骤预算规则。
- 未修改 Phase 1 ACL/JWT 安全逻辑。
- 未删除或放宽现有测试。
- 未初始化 Git；未配置 remote；未上传代码。

---

## 6. 回滚说明

按文件独立回滚即可：

1. 还原 `src/agent_runtime/recovery/invocations.py` 到 B3 前的状态。
2. 还原 `src/agent_runtime/activities.py` 到 B3 前的状态。
3. 还原 `src/agent_runtime/platform_api.py` 的 reconcile token 来源。
4. 删除 `tests/test_invocation_recovery.py` 与本批次四份报告。

无数据库结构变更，因此没有 schema 回滚风险。

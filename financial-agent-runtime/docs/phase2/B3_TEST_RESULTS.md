# B3 测试结果分类报告

日期：2026-11-21
说明：严格区分 PASS / FAIL / SKIP / NOT RUN。未运行的 Temporal 集成测试不写为 PASS。

---

## 1. 环境与命令

环境：

- Windows / Python 3.14.7 / pytest。
- PostgreSQL：`127.0.0.1:5433/agent_runtime`，容器 healthy。
- 真实 PG 回归同时设置：
  - `DATABASE_URL=postgresql+asyncpg://agent_app:agent_app@localhost:5433/agent_runtime`
  - `TEST_DATABASE_URL=$DATABASE_URL`
- Temporal 容器 healthy，但本批次未开启 `RUN_TEMPORAL_IT`。

---

## 2. B3 专项测试

初次未设置 DSN：

- 命令：`pytest -q --tb=short tests/test_invocation_recovery.py`
- 结果：`2 passed, 7 skipped`
- SKIP 原因：`tests/conftest.py` 未检测到 `TEST_DATABASE_URL`，真实 PG 用例按设计跳过。不作为最终验收结果。

最终真实 PG 运行：

- 命令：`pytest -q --tb=short tests/test_invocation_recovery.py`
- 结果：**14 passed in 47.24s**
- 状态：**PASS**

覆盖场景：

| 场景 | 结果 |
|---|---|
| Activity 异常路径发现 UNKNOWN 并进入 `RECONCILIATION_REQUIRED` | PASS |
| UNKNOWN 正常发现 | PASS |
| 重复 `require_reconciliation()` 幂等 | PASS |
| UNKNOWN 未经人工确认不能自动终结 | PASS |
| 人工对账正常执行 | PASS |
| 重复对账返回 `reused=True` | PASS |
| 重复对账不产生第二个 `TOOL_RECONCILED` 事件 | PASS |
| 旧 Worker 低号 fencing 失败 | PASS |
| 人工 FAILED 后新 Worker 高位 attempt 安全重试并 confirm | PASS |
| 并发人工对账只有一个终态写入 | PASS |
| 跨租户 reconcile 隔离 | PASS |
| API 跨用户/跨租户 404 | PASS |
| API 缺少 approval/manage scope 403 | PASS |
| 有权限 owner 使用高位 fencing namespace | PASS |
| Replay AST 守护 | PASS |

---

## 3. 回归测试

均为最终成功运行，分批独立进程执行。

| 批次 | 命令/文件 | 结果 |
|---|---|---|
| B1/B2/B3 核心 | `test_replay_reliability.py` + `test_step_budget.py` + `test_invocation_recovery.py` | **31 passed in 51.30s** |
| ACL/JWT/权限 | `test_api_auth.py`、`test_auth_jwt.py`、`test_jwt_claim_boundary.py`、`test_policy.py`、`test_rbac_abac.py`、`test_security_regression.py`、`test_metrics_api.py`、`test_monitoring_api.py` | **55 passed in 4.04s** |
| 真实 PG 权限 | `test_capability_token.py`、`test_rls_postgres.py`、`test_tenant_isolation.py` | **14 passed in 59.95s** |
| Replay/Graph 守护 | `test_graph_determinism.py`、`test_checkpoint_snapshot.py`、`test_graph_core.py` | **36 passed in 0.30s** |

最终回归合计：**136 passed**，0 FAIL，0 SKIP。该数字按上述四批去重统计，包含 B3 专项 14 项。

---

## 4. 静态检查

- `read_lints`：`src/agent_runtime/recovery/invocations.py`、`src/agent_runtime/activities.py`、`src/agent_runtime/platform_api.py`、`tests/test_invocation_recovery.py`
- 结果：**0 diagnostics**

---

## 5. 四类结果汇总

| 分类 | 结果 |
|---|---|
| PASS | B3 专项 14；最终回归合计 136；静态检查 0 diagnostics |
| FAIL | 最终状态 0。中间失败已修复：测试辅助函数复用 session/RLS 上下文导致 7 个失败；人工 FAILED 后旧低号 token 不可重试暴露 namespace 设计缺口，已通过高位 Activity namespace 修复并复测 PASS |
| SKIP | 最终 B3 专项 0。初次无 DSN 运行有 7 个 SKIP，已被最终真实 PG 运行覆盖 |
| NOT RUN | 24 个真实 Temporal 集成用例本批次未运行；全量单次非 Temporal 会话本批次未运行，沿用 B2 环境风险记录 |

---

## 6. 未运行项说明

- 真实 Temporal IT：本批次未设置 `RUN_TEMPORAL_IT`，因此不写 PASS。B0 历史证据为 24 passed，但不能替代本批次证据。
- 全量单次非 Temporal 套件：B2 已记录约 37% 会话级卡住问题；本批次采用分批独立进程验证，不声称取得单次全量通过证据。

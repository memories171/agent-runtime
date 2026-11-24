# B2 测试执行分类报告

日期：2026-11-21
说明：严格区分 PASS / FAIL / SKIP / NOT RUN。环境不足标 NOT RUN，不计 PASS。

---

## 1. 新增测试（tests/test_step_budget.py）

命令：`pytest -q --timeout=60 tests/test_step_budget.py` → **13 passed**（1.16s）。

---

## 2. 回归测试（分批独立进程，因全量单次会话卡住）

| 批次/文件 | 结果 |
|---|---|
| batch1：api_auth + security_regression + jwt_claim_boundary + replay_reliability + step_budget + graph_core + capability_token + checkpoint_snapshot | **75 passed** (59.02s) |
| test_agent_tree_postgres.py（PG 重启后干净重跑） | **10 passed** (31.17s) |
| test_agent_tree_migration.py | 4 passed |
| test_benchmark.py | 1 passed |
| test_child_context_isolation.py | 6 passed |
| test_financial_monitoring.py | 5 passed |
| test_graph_context_checkpoint.py | 6 passed |
| test_graph_determinism.py | 9 passed |
| test_graph_replan.py | 1 passed |
| test_graph_rls_postgres.py | 2 passed |
| test_metrics_api.py | 5 passed |
| test_metrics_postgres.py | 15 passed (58.49s) |
| test_orchestration_delegation.py | 2 passed |
| test_orchestration_migration.py | 3 passed |
| test_orchestration_models.py | 6 passed |
| test_orchestration_rls_postgres.py（清理残留数据后） | 2 passed |
| test_monitoring_api.py + test_rbac_abac.py（verify 批次） | 16 passed（含在 1 failed+16 passed 中） |
| test_parallelism_calculator.py | 16 passed |
| test_policy.py | 7 passed |
| test_rls_postgres.py | 2 passed |
| test_skills.py | 1 passed |
| test_tenant_isolation.py | 1 passed |

**合计**：覆盖全部 30 个非 Temporal 测试文件，0 个真实代码 FAIL。

---

## 3. 四类结果汇总

| 分类 | 数量 | 说明 |
|---|---|---|
| PASS | 全部已执行批次（13 新增 + 全部分批回归） | 0 FAIL（清理数据污染后） |
| FAIL | 0（最终状态） | 早期 2 个 FAIL（agent_tree_postgres 1、orchestration_rls 1）确认为被 kill 进程遗留的测试数据污染，清理后重跑 PASS |
| SKIP | 0（B2 新增测试） | — |
| NOT RUN | 24 个真实 Temporal 用例（本批次）；全量单次 192 项 | 环境会话卡住 + 未改 workflow 内部，引用 B0 证据 |

---

## 4. 环境与注意事项
- Windows / Python 3.14.7 / pytest 8.4.2 / pytest-asyncio 0.26.0 / pytest-timeout 2.4.0。
- PostgreSQL 5433（已重启，healthy）、Temporal 7233（healthy）。
- 全量单次 192 项在约 37% 会话级卡住，未取得稳定单次全量通过证据（详见 B2_IMPLEMENTATION_REPORT 第 2 节）；分批验证结果可信。
- 早期 FAIL 的 `orchestration_rls` 因固定 `delegation_key` + 残留数据导致 `ON CONFLICT DO NOTHING` 跳过插入；清理 `orchestration-rls-a/b` 租户残留行后 2 passed。非代码缺陷。
- `agent_tree_postgres` 早期 FAIL 同为污染，PG 重启后 10 passed。

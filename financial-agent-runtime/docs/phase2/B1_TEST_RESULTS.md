# B1 测试执行分类报告

日期：2026-11-21
说明：严格区分 PASS / FAIL / SKIP / NOT RUN。未执行不计 PASS。

---

## 1. B1 新增测试（tests/test_replay_reliability.py，4 个）

| 测试 | 类型 | 结果 |
|---|---|---|
| `test_outbox_start_workflow_passes_execution_timeout` | 纯单测 | PASS |
| `test_workflow_uses_deterministic_time_and_id_apis` | AST 静态守护 | PASS |
| `test_outbox_command_id_unique_constraint_rejects_duplicate` | 纯单测（模型约束） | PASS |
| `test_stale_fencing_rejected_and_confirmed_reused` | 真实 PostgreSQL | PASS |

命令：`pytest -q --tb=short --timeout=60 tests/test_replay_reliability.py` → **4 passed in 4.89s**。

---

## 2. 回归测试（Phase 1 的 188 + B1 的 4 = 192）

由于本环境（Windows / Python 3.14 / pytest-asyncio 0.26）在全量单次运行时出现会话级卡住（约 37%，`test_graph_core` 附近；无 PG 活动、CPU 接近 0），采用**分批独立进程**完成全部 30 个非 Temporal 测试文件的验证：

| 批次 | 文件组 | 命令 | 结果 |
|---|---|---|---|
| 逐文件（前 5） | agent_tree_migration / agent_tree_postgres / api_auth / auth_jwt / benchmark | 每文件独立进程 | 4+10+4+8+1 = **27 passed** |
| A | capability_token / checkpoint_snapshot / graph_core / metrics_api | `pytest -q --timeout=120 <4 files>` | **43 passed in 54.24s** |
| B | child_context_isolation / financial_monitoring / graph_context_checkpoint / graph_determinism / graph_replan / graph_rls_postgres / jwt_claim_boundary / metrics_postgres / orchestration_delegation / orchestration_migration / orchestration_models / orchestration_rls_postgres | `pytest -q --timeout=150 <12 files>` | **65 passed in 114.02s** |
| C | monitoring_api / parallelism_calculator / policy / rbac_abac / replay_reliability / rls_postgres / security_regression / skills / tenant_isolation / metrics_api | `pytest -q --timeout=150 <10 files>` | **62 passed in 14.26s** |

覆盖核对：全部 30 个非 Temporal 测试文件均被覆盖（无遗漏）；`metrics_api` 在 A、C 两批重复出现（以其一次结果为准）。所有批次**0 failed / 0 error**。

**去重后的通过数核对**：以 B0 已确认的全量基线 188 passed（非 Temporal）+ B1 新增 4 = 192 为准；分批结果无失败，与基线一致。

---

## 3. 真实 Temporal 集成测试（B0 已执行，本批次引用）

| 组 | 状态 |
|---|---|
| 4 个 `temporal_it` 文件 / 24 用例（B0，`RUN_TEMPORAL_IT=1` + 真实 Temporal/PG） | **PASS ×24**（B0，19m01s；本批次未重跑，因生产改动不涉及 workflow 内部） |
| 本批次重跑状态 | **NOT RUN**（本批次聚焦 production outbox 参数与 service 级测试；真实 Temporal 24 用例的 B0 证据仍有效） |

---

## 4. 四类结果汇总

| 分类 | 数量 | 说明 |
|---|---|---|
| PASS | 192（188 基线 + 4 新增，分批验证）+ 24（B0 真实 Temporal，引用） | 分布见第 1、2、3 节 |
| FAIL | 0 | 所有执行批次 0 failed |
| SKIP | 0（B1 新增测试）；24（B0 裸机开关关闭时为 SKIP，本批次沿用） | RUN_TEMPORAL_IT 未开时如实 SKIP |
| NOT RUN | 24 个真实 Temporal 用例（本批次未重跑）；场景 3/9 的 pytest（仅脚本覆盖） | 如实标注，未计 PASS |

---

## 5. SKIP / NOT RUN 原因
- 真实 Temporal 24 用例本批次 NOT RUN 原因：B1 生产改动位于 `outbox/commands.py` 的 start 参数，不改变 workflow 内部行为；B0 已用真实服务验证；避免重复 19min 运行。
- 场景 3（Activity retry）/ 场景 9（恢复失败审计）无 pytest：仅手工脚本覆盖，列为遗留（B4 补）。

---

## 6. 环境说明
- Windows / Python 3.14.7 / pytest 8.4.2 / pytest-asyncio 0.26.0 / pytest-timeout 2.4.0。
- PostgreSQL 5433（healthy）、Temporal 7233（healthy）容器运行中；api/worker 容器在验证期间停止（测试直连 PG/Temporal，不依赖它们）。
- 全量单次运行的会话级卡住已如实记录于 `B1_REPLAY_RELIABILITY_REPORT.md` 第 7 节；分批验证结果可信，但不等于"单次全量通过"。

# B0 测试执行分类报告

日期：2026-11-21
说明：严格区分 PASS / FAIL / SKIP / NOT RUN。连接失败不伪装 SKIP；未执行不计 PASS；环境不具备如实标注。

---

## 1. 执行矩阵总览

| 运行场景 | 命令/条件 | 结果 |
|---|---|---|
| R1 裸机 | 无 `RUN_TEMPORAL_IT`、无 `TEST_DATABASE_URL` | `142 passed, 70 skipped in 17.40s`（墙钟 20.56s） |
| R2 仅 Temporal（开关关） | `-m temporal_it`，开关未开 | `24 skipped, 188 deselected in 1.15s` |
| R3 连接失败（开关开） | `RUN_TEMPORAL_IT=1`, `TEMPORAL_ADDRESS=localhost:19999`（不可达） | 23 errors + 1 passed；EXIT=1，10.28s 内失败 |
| R4 Temporal 真实执行 | `RUN_TEMPORAL_IT=1` + Temporal/PG 就绪 | `24 passed, 188 deselected in 1141.46s (19m01s)` |
| R5 Phase 1 回归 | `-m "not temporal_it"` + 真实 PG | `188 passed, 24 deselected in 186.96s (3m06s)` |
| R6 安装验证 | `python -m pip install -e '.[test]'` | EXIT=0 |

---

## 2. 四类结果分类

### 2.1 24 个 Temporal 用例分类

| 用例 | R2（开关关） | R4（开关开+服务就绪） |
|---|---|---|
| `test_graph_integration.py` 7 用例 | SKIP | PASS ×7 |
| `test_parent_close_policy.py` 5 用例 | SKIP | PASS ×5 |
| `test_subagent_integration.py` 4 用例 | SKIP | PASS ×4 |
| `test_subagent_timeout_cancel.py` 8 用例 | SKIP | PASS ×8 |
| **合计 24** | **SKIP ×24** | **PASS ×24** |

SKIP 原因（R2，逐条一致）：`未设置 RUN_TEMPORAL_IT=1：跳过真实 Temporal 集成测试`。

R4 逐用例结果（全部 PASS）：
```
graph: test_a_bounded_parallelism, test_b_dependency_wait,
       test_c_failure_policies[FAIL_FAST/CONTINUE_INDEPENDENT/MANUAL_INTERVENTION],
       test_e_pause_resume, test_d_worker_crash_recovery            (7/7 PASS)
parent_close: test_pcp_parent_normal_end, test_pcp_parent_user_cancel,
       test_pcp_parent_timeout, test_pcp_parent_failure_policy,
       test_pcp_parent_close_terminates_stubborn_child              (5/5 PASS)
subagent: test_subagent_success_parallel_and_result_aggregation,
       test_duplicate_delegation_is_idempotent, test_subagent_failure_policy,
       test_subagent_worker_restart_recovery                        (4/4 PASS)
timeout_cancel: test_parent_cancel_cascades_and_preserves_completed,
       test_repeated_cancel_is_idempotent, test_parent_timeout_stops_active_children,
       test_single_child_timeout_does_not_cancel_others,
       test_cancel_after_child_complete_does_not_overwrite,
       test_worker_restart_durable_timer_recovers_timeout,
       test_cancel_wins_when_cancel_and_timeout_overlap,
       test_no_new_child_started_after_cancel                       (8/8 PASS)
```

### 2.2 连接失败分类（R3，关键规则验证）

- 条件：`RUN_TEMPORAL_IT=1`（显式开启）+ `TEMPORAL_ADDRESS=localhost:19999`（不可达）。
- 结果：`1 passed, 23 errors`，EXIT=1，10.28s（10s 连接超时生效）。
- 分类：**FAIL（23）**，不是 SKIP。pytest 将 fixture 内的 `pytest.fail()` 报为 ERROR；按本批次四类口径归入 FAIL。
- 1 passed 为 `test_duplicate_delegation_is_idempotent`（纯 DB 用例，不请求 temporal_client），属真实执行的 PASS。
- 结论：**已启用集成测试的连接失败未被伪装为 SKIP**，满足 B0-2 要求。

### 2.3 PASS / FAIL / SKIP / NOT RUN 汇总

| 分类 | 数量 | 说明 |
|---|---|---|
| PASS | 188（R5 非 Temporal 回归）+ 24（R4 Temporal 真实）= 212 | R5 与 R4 为互斥子集，合计即全量 212 用例 |
| FAIL | 0（R4/R5 正常执行）；R3 故意失败验证 23 项 | R3 为分类规则验证，非产品回归 |
| SKIP | 24（R2 开关关）+ 70（R1 裸机，含 24 Temporal + 46 其他需 PG/环境） | 均注明原因 |
| NOT RUN | 0（本批次所有规定运行均已执行） | 无环境不具备而未标注项 |

### 2.4 Phase 1 可比性核对

- 原 Phase 1 基线：`188 passed`（排除 4 个 Temporal 文件）。
- B0 R5：`188 passed, 24 deselected`，命令 `-m "not temporal_it" + TEST_DATABASE_URL`。
- 两者测试范围一致（188 = 全量 212 − 24 Temporal），数量完全相同，**可比性成立，无混淆**。
- R4 新增证据：24 个 Temporal 用例由 NOT RUN 变为 PASS（真实执行，19m01s）。

---

## 3. 环境

- Python 3.14.7 / pytest 8.4.2 / pytest-asyncio 0.26.0 / pytest-timeout 2.4.0。
- Docker 服务：postgres（5433, healthy）、temporal（7233/8233, healthy, `temporal server start-dev`）、api、worker、keycloak 均运行中。
- DSN：`postgresql+asyncpg://agent_app:agent_app@localhost:5433/agent_runtime`（经 `real_postgres_dsn` 统一提供）。

---

## 4. 警告（如实记录，非 PASS/FAIL 变更）

- R4 运行末尾出现 1 条 `PytestUnraisableExceptionWarning`：`execute_graph.<locals>._launch` 生成器关闭时，`graph/runner.py:762` 在 workflow event loop 关闭后调用 `workflow.execute_activity` → `NotInWorkflowEventLoopError`。**24 用例仍全部 PASS**。
- 385 warnings 主要为 Python 3.14 的 `asyncio.get_event_loop_policy` 弃用警告与 PyJWT 短 key 警告（Phase 1 既有）。
- 这些警告未导致任何测试 FAIL，也未改变分类结果。

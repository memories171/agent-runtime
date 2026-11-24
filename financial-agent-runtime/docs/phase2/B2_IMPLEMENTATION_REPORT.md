# B2 实施报告：max_steps 与 duration 联动

日期：2026-11-21
批次：B2
状态：**实施完成，等待人工验收**
修改范围：5 个生产文件 + 1 个新测试文件

---

## 1. 第一步：当前实现与 B1 遗留问题检查

### 1.1 修改前事实（全部代码确认）
- `contracts.py:63` `RunCreate.max_steps`：`ge=1, le=200`（硬上限契约已存在）。
- `api.py:179`（修改前）：`max_steps = body.max_steps or settings.max_agent_steps`，`max_duration = body.max_duration_seconds or settings.max_run_duration_seconds` —— **两者独立，无联动**。
- `config.py:23-24`：`max_run_duration_seconds=7200`，`max_agent_steps=50`；无 per-step 估算参数。
- `graph/runner.py:574`：`"max_steps": settings.graph_max_node_steps`（=8），完全忽略 `graph_config.limits.max_node_steps` —— **静默忽略缺陷确认**。
- `workflow.py:484`：`self._child_cancel_grace_seconds = settings.orchestration_child_cancel_grace_seconds` —— workflow 内部读取 live settings，replay 漂移风险（B1 遗留）。
- B1 遗留：`record_worker_recovery_activity` dead code、无 `WorkflowReplayer`、全量单次会话卡住（37%）。

### 1.2 检查结论
- B1 遗留中与 B2 相关的两项（`graph` 静默忽略、`workflow.py:484` 漂移）均确认存在，本批次处理。
- 其余 B1 遗留（dead recovery activity、WorkflowReplayer、会话卡住）不属于 B2 范围，保留。

---

## 2. 第二步：稳定环境 192 项全量通过证据

**结论：未取得稳定环境的单次全量通过证据，已如实记录风险（未伪造 PASS、未删除测试）。**

- 本环境（Windows / Python 3.14.7 / pytest-asyncio 0.26）在 B1、B2 多次尝试中，全量非 Temporal 套件（192 项）单次运行均在约 37%（`test_graph_core::TestScheduler` 附近）出现会话级卡住：无 PG 活动、CPU 接近 0、日志停在 72 个点。
- 已尝试：禁插件、禁 timeout 插件、重启 api/worker 容器、重启 PostgreSQL、清理连接、清理残留数据；单次全量仍不可靠。
- 采用**分批独立进程**完成全部测试文件验证（见 `B2_TEST_RESULTS.md`），所有批次 0 FAIL（2 个早期 FAIL 已确认是被 kill 进程导致的测试数据污染，清理后重跑均 PASS）。
- 真实 Temporal 24 用例（B0 已验证 24 passed）本批次 NOT RUN（未改 workflow 内部逻辑；B1 已引用）。

---

## 3. 第三步：B2 最小修改方案（已实施）

| # | 改动 | 文件 | 理由 |
|---|---|---|---|
| 1 | 新增 `estimated_seconds_per_step=60` 配置 | `config.py` | 作为 duration→steps 的保守换算基准（30min≈30 步，200 上限≈3.3h） |
| 2 | 新增纯函数 `resolve_step_budget(duration, requested, default, sps)` | `api.py` | `required=ceil(duration/sps)`；`effective=max(requested/default, required)` 且 ≤200；`required>200`→ValueError→HTTP 400 |
| 3 | `create_run` 调用 resolver，异常映射 HTTP 400；自动上调时写 `STEP_BUDGET_ADJUSTED` 事件 | `api.py` | 满足"记录原始值/调整值/原因"与"超 200 返回 400" |
| 4 | Graph 子 agent `max_steps` 改用 `limits.max_node_steps` | `graph/runner.py` | 修复用户 `graph.limits.max_node_steps` 被静默忽略 |
| 5 | `WorkflowInput` 新增 `child_cancel_grace_seconds`（默认 None）；创建时冻结 settings 值；workflow 优先读冻结值，旧 history 回退 settings | `contracts.py`/`api.py`/`workflow.py` | 消除 replay 时对 live 配置的漂移，统一配置来源 |

**未做**：删除/放宽任何护栏；改 ACL/RBAC/租户隔离/JWT；UNKNOWN 对账（B3）；Temporal 持久化（B5）；Checkpoint Restore；DB 迁移；Git 操作。

---

## 4. 第四步：测试摘要（详见 B2_TEST_RESULTS.md）

- B2 新增 13 测试：13 passed（含 30min 预算、自动上调记录、200 上限、超 200 HTTP 400、Graph 步数、replay 安全、不同 duration 一致性）。
- 受影响文件批次：75 passed。
- 全部分批验证合计 0 FAIL（早期 2 个 FAIL 为数据污染，清理后重跑 PASS）。
- 真实 Temporal 24 用例：NOT RUN（引用 B0 证据）。

---

## 5. 必测场景核对

| 场景 | 证据 | 结果 |
|---|---|---|
| 短任务正常执行 | `test_short_task_keeps_requested_steps` | PASS |
| 30min 合理步骤预算 | `test_thirty_minute_task_gets_reasonable_budget` + `test_long_task_auto_upgrades_and_records` | PASS |
| 步骤达到上限正确处理 | `test_step_cap_never_exceeded_without_error` | PASS |
| 超硬上限返回 HTTP 400 | `test_required_steps_over_hard_cap_raises_for_http_400` + `test_create_run_over_cap_duration_returns_http_400` | PASS |
| 不同 duration 预算一致 | `test_deterministic_budget_across_durations` | PASS |
| Graph 路径执行步骤限制 | `test_graph_uses_validated_limits_max_node_steps`（源码 AST）+ `graph_config.limits` 合并逻辑 | PASS |
| Workflow Replay 不受影响 | `test_workflow_no_nondeterministic_time_or_id_calls` + 冻结 grace 字段守护 + B0 replay 用例 | PASS |
| 权限/安全/状态回归 | batch1（security_regression/jwt_claim/api_auth/capability_token/replay_reliability 等）75 passed | PASS |

---

## 6. 遗留风险
见 `B2_LEFTOVER_ISSUES.md`。

---

## 7. 是否具备进入 B3 的条件
**具备（有条件）**。B2 改动均为 API 层/输入契约/Graph 参数，不改 workflow 主循环与 DB schema；13 新测试 + 分批回归全绿；Phase 1 安全修复未回退。建议进入 B3 前在稳定 CI（Linux，固定依赖）取得 192 项单次全量通过证据，以消除本环境的会话卡住疑虑。

# Phase 2 实施计划：长任务可靠性与中断恢复能力

日期：2026-11-21
版本：**v2.0（五项决策已批准，等待 B0 开始确认）**
范围：`financial-agent-runtime`（后端）。

> 本文件已按人工批准的五项决策更新。批准的是**架构方向**，不代表批准所有后续数据库迁移和生产环境变更。涉及数据库迁移、持久化环境切换、权限边界变化时，必须先提交方案并等待人工批准。

---

## 0. 决策批准状态（v2.0 新增）

### 0.1 五项决策最终结论

| 决策 | 批准结论 | 关键约束 |
|---|---|---|
| 1. Checkpoint Restore | **方案 B**：Temporal Replay 为主要恢复机制；Checkpoint 作为辅助恢复、审计和状态校验；暂不实现完整指定 Checkpoint Restore；保留未来扩展能力 | 不得为实现 Restore 破坏现有 Replay；B7 暂缓，除非未来单独评估+人工批准 |
| 2. max_steps=50 | **保留 200 硬上限**；实施 duration/steps 联动校验；按 duration 计算合理 steps，≤200 时**自动上调**并记录原始值/调整值/原因；>200 返回 HTTP 400；不静默突破 200；不删除限制；修复 Graph 步数静默忽略 | 实施前先确认所有 max_steps 调用路径与实际语义；保持安全/成本/资源保护 |
| 3. Temporal 持久化 | **方案 B**：SQL Persistence + 独立 PostgreSQL；固定镜像版本；保留 Dev Server 开发环境；分阶段迁移；先 staging 双跑验证再切换 | 未经人工批准不得替换现有开发环境或执行不可逆迁移；需提供备份/恢复/回滚/Worker 兼容方案 |
| 4. UNKNOWN 自动对账 | **先方案 A**：接通 reconciliation 流程；修复 fencing token 使用问题；增加幂等保护；保留人工终结；暂不全自动写终态 | 新增数据库字段暂不批准；先分析现有字段是否足够，不足则单独提交字段设计/迁移/兼容/回滚/测试证据，待批准 |
| 5. 30 分钟测试 | **两层**：time-skipping 快速层 + CI nightly 真实墙钟层（35min）；两层不互相替代；严格区分 PASS/FAIL/SKIP/NOT RUN；不得把模拟时间当真实证据 | CI 暂定本地/自托管 Runner；不得配置 GitHub 云端或上传代码；先提供本地/自托管方案、资源需求与真实成本估算 |

### 0.2 批准边界（必须遵守）

1. **B7 默认不实施**。
2. **数据库迁移未获批准**：决策 4 的字段、决策 3 的持久化 schema 均需单独提交方案并等待批准。
3. **生产环境切换未获批准**：Dev Server 保留；持久化切换仅在 staging 双跑验证后、经人工批准才进行。
4. **权限边界变化未获批**：UNKNOWN 对账不得绕过现有 ACL/RLS；自动任务身份需方案评审。
5. **无 GitHub 上传**：CI 仅本地/自托管 Runner。

### 0.3 对原计划（v1.0）的调整

- 原 P0-B（完整 Checkpoint Restore）→ 重命名为 **B7，暂缓默认不实施**。
- 原 P0-A1/A2/A3/A4 → 并入 **B0（测试基础设施）+ B1（Replay 可证明化）**。
- 原 P1-4（max_steps 联动）→ **B2**，按批准规则改为"自动上调 + 记录 + 400 超限"。
- 原 P1-3（UNKNOWN 对账）→ **B3**，仅方案 A。
- 原 P1-1/P1-2（两层测试）→ **B4**。
- 原 P0-C（Temporal 持久化）→ **B5**，分阶段且不经人工批准不切换。
- 原 P1-1 真实墙钟 → **B6**。

---

## 1. 当前架构与实现现状（审查证据）

### 1.1 技术栈与分层

- API：FastAPI（`api.py` / `platform_api.py` / `monitoring_api.py` / `auth_router.py`）。
- 执行引擎：Temporal Durable Execution（`workflow.py` / `activities.py` / `graph/runner.py` / `orchestration/runner.py`）。
- 持久化：PostgreSQL + SQLAlchemy async + 16 个 Alembic 迁移；租户表 `FORCE ROW LEVEL SECURITY`。
- 恢复基础设施：`outbox/` / `recovery/invocations.py` / `approval/` / `checkpoint/`。
- 安全约束（Phase 1 已修复，必须保持）：逐 Run ACL、RBAC/ABAC、RLS、Capability Token 衰减、JWT claim 不可信、`DataQuality` 状态。

### 1.2 长任务执行链路

```
POST /runs (api.py:158)
  ├─ RunCreate 校验：max_steps ≤200 (contracts.py:63), duration ≤86400 (:64)
  ├─ max_steps = body.max_steps or settings.max_agent_steps(50) (api.py:179)
  ├─ 构造 WorkflowInput → enqueue_command(START_WORKFLOW) (api.py:194-209)
  └─ workflow_outbox 表
       ↓ worker.py outbox_dispatch_loop 每 2s (:119-122)
dispatch_commands_once (outbox/commands.py:86-157)
  ├─ claim 20，lease 60s，退避 min(60,2^n)，20 次死信
  └─ start_workflow(AgentRunWorkflow.run) ← 未传 execution_timeout (:123-126)
       ↓
AgentRunWorkflow.run (workflow.py:442+)
  ├─ watchdog durable timer = max_duration_seconds (159-179)
  ├─ while self._step < max_steps (542)；每步 elapsed 检查 (553-557)
  ├─ agent_decide_activity (3min/heartbeat45s/retry3) (587-593)
  ├─ execute_tool_activity (1h/2h/heartbeat30s/retry3) (743-768)
  ├─ 每 3 步 persist_checkpoint_activity (804-805)
  └─ 异常 → _record_failure/_mark_recovered (recovery_count≤3, 仅包 decide) (594-604)
```

**30min 技术可行性**：默认 duration=7200s 满足；**实际瓶颈是 `max_steps=50`**（平均 >36s/步即触顶，触顶为 FAILED + RUN_FAILED，`workflow.py:838,849-860`）。Graph 模式绕过该上限（`workflow.py:506-540`）。token/cost 无硬上限；context/tool result 均降级不终止。

### 1.3 Checkpoint 保存与恢复链路（决策 1 依据）

- **保存（已实现）**：`workflow.py:255-288` → `activities.py:319-342` → `checkpoint/service.py:68-194`（canonical JSON→SHA-256→zlib→AES-256-GCM→对象存储 put/get 校验→Envelope→事件）。
- 保存点：暂停前（:561）、每 3 步（:804-805）、完成前（:827）。
- **加载（部分实现）**：`service.py:196-236` 完整实现，但生产唯一调用点 `api.py:308-316` 仅做 `/resume` 完整性门禁，返回值丢弃。
- **未保存**：workflow 内部字段（`workflow.py:49-66`）、pending signals、Temporal history、activity 结果、graph 内部状态。
- **死代码**：`record_worker_recovery_activity`（`activities.py:390-451`，注册但无调用）。
- **结论**：Checkpoint = 加密审计快照 + 完整性门禁，非可重放执行点。与决策 1 的批准一致。

### 1.4 Worker 重启恢复（决策 1 依据）

- `worker.py:61-110` `record_worker_reconnect()`：扫描非终态 Run，写 `WORKER_FAILED/TEMPORAL_HISTORY_REPLAY/WORKER_RECONNECTED/WORKFLOW_RESUMED` 四事件；docstring 明确"不创建/重启 Workflow，也不从 Checkpoint 手动执行 step"。
- 恢复源为 Temporal replay（`workflow.py:58,63`、`child_registry.py:3-5`）。
- 证据为手工脚本 `scripts/verify_worker_recovery.py` / `verify_recovery_events.py`，非 pytest。

### 1.5 Outbox / 幂等 / fencing / 状态机（决策 4 依据）

- Outbox 已实现（`commands.py:20-157`）：幂等键、lease 60s、退避 `min(60,2^n)`、20 次死信、`command_id` 去重。
- `ToolInvocationService`（`invocations.py`）：状态机 :14-24；幂等键 `sha256(tenant:run:tool_call_id)`（:40-46）；fencing = Temporal attempt（`activities.py:179`）；非幂等 provider fail-closed 转 UNKNOWN（:187-193）。
- `require_reconciliation()`（:357-390）已实现但无调用者；人工 API `platform_api.py:549-584`。
- 已知问题：fencing 人工 `+1` 与 attempt 共用数值空间且 CAS 用 `<=`（碰撞风险）；`_mark_unknown()` SQL 无状态条件（:252-264）；`RunEvent` 无幂等键（`db.py:44-56`）。

### 1.6 测试覆盖现状（决策 5 / B0 依据）

- 4 个 Temporal 集成测试**实际 22 函数 / 24 用例**（非"31"），全部依赖真实 Temporal + PG，导入期用 `os.environ.setdefault` 绕过 conftest skip：`test_subagent_timeout_cancel.py:22-25`、`test_subagent_integration.py:20-23`、`test_graph_integration.py:23-26`、`test_parent_close_policy.py:23-26`。
- 挂起根因：`Client.connect` 无超时（session fixture）、`wait_for(150/180)`、session loop、无 `pytest-timeout`（`pyproject.toml:36`）。
- 全项目无 time-skipping、无 CI yml、无 nightly、无 `WorkflowReplayer`。
- Outbox / Invocation / Approval / UNKNOWN / Checkpoint 服务级主要为手工脚本，缺 pytest。

---

## 2. 恢复路径汇总（决策 1 结论落地）

| 路径 | 触发 | 恢复源 | 状态 | 决策后定位 |
|---|---|---|---|---|
| Worker 进程重启 | 崩溃/容器重建 | Temporal history replay | 已实现 | **主要恢复机制**，B1 使其可证明/可观测 |
| 用户暂停/恢复 | `/pause` → `/resume` | 同一 workflow 内存 state + signal | 已实现 | Checkpoint 仅完整性门禁（辅助） |
| Tool 结果未知 | Activity 异常/超时 | 人工 `/reconcile` | 部分实现 | B3 接线方案 A |
| 指定 Checkpoint 恢复 | — | — | 未实现 | **B7 暂缓，默认不实施** |

---

## 3. 实施批次路线（B0–B7）

### 3.1 批次总览与依赖

| 批次 | 名称 | 决策依据 | 前置依赖 | 状态 |
|---|---|---|---|---|
| **B0** | Temporal 测试基础设施修复 | 决策 5 前提 | 无 | **待人工确认开始** |
| B1 | Replay 恢复可证明化/可观测化/原子化 | 决策 1 方案 B | B0 | 未开始 |
| B2 | max_steps 联动与 Graph 步数修复 | 决策 2 | B0 | 未开始 |
| B3 | UNKNOWN 对账接线与幂等/fencing 修正 | 决策 4 方案 A | B0 | 未开始 |
| B4 | 两层测试基础设施 | 决策 5 | B0、B1、B2、B3 | 未开始 |
| B5 | Temporal 持久化分阶段迁移 | 决策 3 | B0、B1（版本化） | 未开始，需单独批准切换 |
| B6 | 30 分钟真实墙钟验证 | 决策 5 | B4、建议 B5 | 未开始 |
| B7 | 完整 Checkpoint Restore | 决策 1 | B1、B5、B6 + 单独批准 | **暂缓，默认不实施** |

**每批次必须**：
1. 先审查代码 → 明确修改范围 → 修改 → 执行相关测试。
2. 检查 Phase 1 的 188 passed 基线与安全修复不回退。
3. 输出变更文件清单 + 测试报告 + 风险。
4. 未通过验收不得进入下一批次。
5. 涉及数据库迁移、持久化环境切换、权限边界变化 → 先提交方案，等待人工批准。

---

### 3.2 B0 详细实施任务与验收标准

#### B0 目标

修复 4 个 Temporal 长时集成测试的基础设施问题，使：
- 默认 `pytest -q` 在无 Temporal/PG 的环境下**快速、如实跳过**，不再挂起；
- 服务就绪 + 显式开关时，24 个用例可被真实执行；
- 如实区分 PASS / FAIL / SKIP / NOT RUN；
- **不删除任何测试、不放宽任何 ACL、不把 SKIP 伪装成 PASS**。

#### B0 范围（严格限定）

**允许修改**：
- `tests/test_subagent_timeout_cancel.py`
- `tests/test_subagent_integration.py`
- `tests/test_graph_integration.py`
- `tests/test_parent_close_policy.py`
- `pyproject.toml`（仅 pytest 配置：markers 注册、`pytest-timeout` 依赖声明）
- `tests/conftest.py`（仅新增共享 fixture，不改现有 `real_postgres_dsn` 语义）

**禁止修改**：
- 任何 `src/agent_runtime/**` 生产代码
- 任何数据库迁移
- 任何 Phase 1 安全修复文件（6 个生产 + 5 个测试）
- 测试的业务断言与用例逻辑（仅改"环境接入与跳过机制"）

#### B0-1：移除 skip 绕过，改为显式开关

- 现状：4 个文件在**导入期**用 `os.environ.setdefault("TEST_DATABASE_URL", DSN)`（`test_subagent_timeout_cancel.py:22-25`、`test_subagent_integration.py:20-23`、`test_graph_integration.py:23-26`、`test_parent_close_policy.py:23-26`），使 conftest 的 `pytest.skip`（`conftest.py:20`）永不触发。
- 动作：
  1. 删除 4 处 `os.environ.setdefault`，不再在导入期写环境变量。
  2. 在 `tests/conftest.py` 新增模块级 helper：`temporal_available()`（检查 `TEMPORAL_ADDRESS`，默认 `localhost:7233` 的可达性）与 `should_run_temporal_it()`（读 `RUN_TEMPORAL_IT`）。
  3. 每个文件顶部加 `pytestmark = pytest.mark.skipif(not should_run_temporal_it(), reason="...")`，或定义 session fixture `temporal_client` 在开关未开/连接失败时 `pytest.skip`。
  4. PG DSN 统一使用 `real_postgres_dsn`，不再硬编码。
- 验收：未设 `RUN_TEMPORAL_IT=1` 时，4 文件 24 用例全部 SKIP，原因明确；设置且服务就绪时全部实际执行。

#### B0-2：Client.connect 超时保护

- 现状：`Client.connect("localhost:7233")` 在 session fixture 中无超时（`test_subagent_timeout_cancel.py:118-119`、`test_subagent_integration.py:148-149`、`test_graph_integration.py:144-146`、`test_parent_close_policy.py:120-121`），服务未就绪时 SDK 内置重试导致挂起。
- 动作：用 `asyncio.wait_for(Client.connect(...), timeout=10)` 包裹；超时或连接异常 → `pytest.skip("Temporal unavailable")`，**不 fail、不静默通过**。
- 验收：在 Temporal 不可达环境运行，10s 内跳过，无超过 15s 的挂起。

#### B0-3：pytest 配置与超时兜底

- 现状：`pyproject.toml:41-46` 无 markers 注册；test extras（:36）无 `pytest-timeout`。
- 动作：
  1. `pyproject.toml` 注册 markers：`temporal_it`（真实 Temporal 集成）、`wall_clock_30m`（35min 墙钟）、`reconciliation`（对账）、`checkpoint_service`。
  2. 声明 `pytest-timeout>=2,<4`，并在 pytest 配置中设置合理全局 timeout（如 300s），对 `temporal_it` 用例单独放宽（如 600s）。
  3. 保持 `asyncio_mode=auto` 与现有 loop scope（:43-44）不变，除非发现 worker task 泄漏导致阻塞（B0-4）。
- 验收：`pytest --markers` 可见新 marker；单个卡住用例 timeout 后报 FAIL 而非无限挂起。

#### B0-4：Worker/loop 清理审查（条件性）

- 现状：session 级 loop + worker task 未干净退出会阻塞后续用例（`pyproject.toml:43-44`；`test_subagent_timeout_cancel.py:122-135` 的 cancel 兜底）。
- 动作：审查每个文件的 worker fixture teardown，确保 `task.cancel()` + `await task`（或 `await worker.shutdown()`）；若存在泄漏，在 fixture teardown 中修复。**仅在发现实际泄漏时改**。
- 验收：连续运行 4 文件 3 次，无"前一个 worker 未退出导致后一个测试阻塞"。

#### B0-5：`asyncio.wait_for` 长等待不改业务语义

- 现状：`wait_for(handle.result(), timeout=150/180)` 分布在各用例（如 `test_subagent_timeout_cancel.py:233,256,274...`）。
- 动作：**保持这些超时值不变**（它们是测试断言的一部分，不是基础设施问题）；仅保证 B0-2/3 的超时兜底先于它们触发。
- 验收：24 用例的业务断言与超时值 diff 为空（可用审查确认）。

#### B0 验收标准（全部满足才进入 B1）

| # | 标准 | 验证方式 |
|---|---|---|
| 1 | 裸机（无 Temporal/PG，未设开关）`pytest -q` **30s 内完成**，24 用例 SKIP，原因含 `RUN_TEMPORAL_IT` | 计时运行 + 输出分类 |
| 2 | 服务就绪 + `RUN_TEMPORAL_IT=1` 时，24 用例实际执行，PASS/FAIL 如实 | 在 compose 栈上完整运行一次 |
| 3 | Phase 1 基线 **188 passed 不回退**，安全测试全绿 | 全量测试 + 对比 |
| 4 | 输出 PASS/FAIL/SKIP/NOT RUN 分类报告，SKIP 原因逐条记录 | 测试报告 |
| 5 | 无测试删除、无断言放宽、无 ACL 放宽、无 SKIP 伪装 PASS | diff 审查 |
| 6 | 未修改任何 `src/agent_runtime/**` 生产代码 | git/diff 核对（无 Git 时用文件清单核对） |
| 7 | `pyproject.toml` 新依赖/marker 不破坏安装（`pip install -e '.[test]'`） | 安装验证 |

#### B0 预计修改文件
- `tests/test_subagent_timeout_cancel.py`
- `tests/test_subagent_integration.py`
- `tests/test_graph_integration.py`
- `tests/test_parent_close_policy.py`
- `tests/conftest.py`（新增共享 helper/fixture）
- `pyproject.toml`（markers + pytest-timeout）

#### B0 风险与回滚
- 风险：条件 skip 可能被误用为"逃避测试"。缓解：开关只在服务不可用时跳过；CI nightly 必设开关；报告如实分类。
- 风险：新增 `pytest-timeout` 影响安装。缓解：版本范围宽松；回滚=移除依赖声明。
- 回滚：逐文件恢复（保留变更清单）；生产代码零改动，回滚无数据风险。

---

### 3.3 B1–B7 概要（详细方案见各批次启动前的审查）

**B1：Replay 可证明化**（决策 1 方案 B）
- 接入 `record_worker_recovery_activity`（死代码）；补 `execution_timeout = max_duration + margin`（`outbox/commands.py:123-126`）；恢复来源标签；checkpoint 孤立对象清理与 digest 修正（可选）。
- 验收：恢复事件带来源标签可查；timeout 生效；旧 history replay 不破坏（必要时 `workflow.patched`）。

**B2：max_steps 联动**（决策 2）
- **启动前必须先完成"全调用路径确认"**（见 3.4）。
- 实现自动上调：按 duration 计算 steps 需求，≤200 自动上调并记录 `{original, adjusted, reason}`，>200 返回 400；修复 Graph 子 agent 步数静默忽略（`graph/runner.py:574`）。
- 验收：duration=1800 且 steps 默认 50 → 自动上调并审计；steps 需求 >200 → 400；Graph `limits.max_node_steps` 生效于子 agent。

**B3：UNKNOWN 对账**（决策 4 方案 A）
- **启动前必须先完成"现有字段充分性分析"**（见 3.5）。
- 接线 `require_reconciliation()`；修复 fencing 碰撞与 `_mark_unknown()` 状态 CAS；审计事件幂等；保持人工终结；不自动写终态。
- 验收：UNKNOWN 自动→RECONCILIATION_REQUIRED；重复调用幂等；人工 reconcile 后终态；权限不绕过。

**B4：两层测试基础设施**（决策 5）
- 快速层：time-skipping + 短 activity + 真实 PG（<5min）。
- 慢速层：35min 墙钟 nightly 骨架（本地/自托管 Runner，见 3.6）。
- 验收：九类场景有明确归属；SKIP/NOT RUN 分类清晰。

**B5：Temporal 持久化**（决策 3）
- **切换前必须单独提交方案并等待人工批准**。
- SQL persistence + 独立 PostgreSQL + 固定镜像 + auto-setup + 卷/备份；Dev overlay 保留；staging 双跑。
- 验收：重启 temporal 容器 history 不丢；备份/恢复演练通过；Worker 兼容。

**B6：30 分钟真实验证**（决策 5）
- 在持久化 Temporal（或明确标注 dev server 未验证持久化）上跑 35min 墙钟 + worker SIGKILL 变体。
- 验收：九类场景证据齐全；如实标注模拟/真实。

**B7：完整 Checkpoint Restore** — **暂缓，默认不实施**，除非未来单独评估+人工批准。

---

### 3.4 决策 2 前置：max_steps 全调用路径确认清单（B2 启动前必须完成）

实施 B2 前必须逐项确认并记录（不允许假设）：

| # | 位置 | 当前语义 | 确认要点 |
|---|---|---|---|
| 1 | `contracts.py:63` `RunCreate.max_steps` | API 入参，None/1–200，超界 422 | 自动上调后是否仍满足契约 |
| 2 | `contracts.py:234` `WorkflowInput.max_steps` | workflow 必填 int，无字段约束 | 上调在 API 层完成，不改 WorkflowInput |
| 3 | `config.py:24` `Settings.max_agent_steps=50` | 全局默认 | env `MAX_AGENT_STEPS` 可被运维调整 |
| 4 | `api.py:179` truthy 回退 | 缺省取默认 | 自动上调的记录点 |
| 5 | `workflow.py:542` 循环顶部 | 硬上限检查 | 不改判断逻辑 |
| 6 | `workflow.py:606` 递增 | decide 成功后 +1 | 与 duration 估算的"平均步时长"假设 |
| 7 | `workflow.py:838,849-860` 超限失败 | FAILED + RUN_FAILED | 上调后避免触发 |
| 8 | `workflow.py:243-247` telemetry | `current_step`/`progress_pct` | 记录原始/调整值的位置 |
| 9 | `workflow.py:264` checkpoint | `remaining_steps` | 上调后快照字段一致性 |
| 10 | `graph/runner.py:557` `graph.limits` 覆盖 | 节点循环步数 | 与 API `max_steps` 的关系 |
| 11 | `graph/runner.py:574` 子 agent 固定取 settings | **静默忽略用户设置** | 必须修复 |
| 12 | `graph/models.py:45-51` `GraphLimits.max_node_steps=8` | 无 ge/le | 需加合理 clamp |
| 13 | `orchestration/activities.py:134,220` | 子 `WorkflowInput(max_steps=...)` | 修复后的传递终点 |

输出：一份"max_steps 语义与调用路径确认表"作为 B2 的输入，经人工确认后实施。

---

### 3.5 决策 4 前置：现有字段充分性分析（B3 启动前必须完成）

用户批准方案 A，但**未批准新增数据库字段/迁移**。B3 启动前必须分析现有字段是否足够：

**现有可用字段**（`db.py:82-106` 的 `ToolInvocation`）：
- `fencing_token`、`reconciliation_status`（NONE/PENDING/RESOLVED）、`reconciliation_note`、`lease_owner`、`lease_expires_at`、`attempt_count`、`status`、`result`、`external_reference`、`completed_at`。

**分析任务**：
1. 能否仅用 `fencing_token` + `reconciliation_status` + `status` 的 CAS 实现幂等升级（UNKNOWN→RECONCILIATION_REQUIRED）与人工终结，不新增字段？
2. fencing 碰撞修复能否通过"改用严格 `fencing_token = expected_token` 比较"实现，而不新增列？
3. 审计事件幂等能否通过"复用现有唯一键或在事件 payload 中带 attempt_key 且不新建表"实现？

**结论分支**：
- 若现有字段足够：B3 不提迁移，直接实施。
- 若不足：单独提交字段设计、迁移方案、兼容性分析、回滚方案和测试证据，**等待人工批准后才执行**；未批准前 B3 只能实施"不依赖新字段"的最小修复。

---

### 3.6 决策 5 前置：本地/自托管 Runner 方案与成本估算

用户要求先提供方案、资源需求与真实成本估算。以下为估算，实际取决于现有硬件。

#### 方案 1：开发者本机 Docker Compose（零新增资本支出）

- 形态：本机跑 `docker compose up`（postgres + temporal + api + worker），nightly 用本机调度（如 Windows Task Scheduler / cron）触发 `pytest -m wall_clock_30m`。
- 资源：2–4 vCPU、4–8 GB RAM 可用；磁盘额外 2–5 GB（PG + snapshot + Temporal dev 数据）。
- 成本：无云账单；边际成本 = 电费。按 4vCPU 主机约 150W、45min/晚估算：\(0.15kW × 0.75h × 30 = 3.375 kWh/月\)，按 ¥0.6/kWh ≈ **¥2/月**。
- 缺点：占用开发机 40–45min/晚；不可与日常开发并行；稳定性依赖本机。

#### 方案 2：自托管专用 Runner（复用现有服务器）

- 形态：在现有内网服务器（或一台小型云主机）安装自托管 runner，仅 nightly 活跃。
- 资源：4 vCPU / 8 GB RAM / 20 GB 磁盘。
- 成本：
  - 复用现有服务器：边际电费约 ¥2–5/月（同方案 1 量级），无新增资本支出。
  - 单独云主机（如 4vCPU/8GB 按量或包月）：约 **¥150–300/月**（包月）；若采用按需启动（跑完即停）可降至 **¥20–60/月**。
- 优点：不上传代码到 GitHub；可控；可保留历史测试数据。
- 缺点：需维护 runner 进程与安全隔离。

#### 方案 3：CI 平台云端托管（**本阶段不采用**）

用户明确不配置 GitHub 云端仓库/上传代码，故不采用。仅记录：2-core 云 runner 约 $0.008/min × 45min ≈ $0.36/次 ≈ $10.8/月（不含 SIGKILL 变体）。

#### 推荐

**方案 2（自托管专用 Runner，复用现有服务器）**，次选方案 1。SIGKILL 变体会使时间/成本翻倍（80–90min/晚，¥4–10/月 电费或 ¥40–120/月 按需云主机）。

#### Nightly 测试成本参数（用于 B4/B6）
- 快速层：<5min/次，PR 必跑。
- 墙钟层：35min + compose/migration/cleanup ≈ 40–45min；pytest-timeout 45min；job timeout 50min。
- 报告分类：PR job `pytest -q -m "not wall_clock_30m"`；nightly `-m wall_clock_30m`；缺环境 SKIP；未调度 NOT RUN。

---

## 4. 每批次通用验收门槛

1. 测试真实性：不删测试、不放宽 ACL、不把 SKIP 伪装 PASS。
2. 回归配套：每个修复新增/修改 pytest，提交时一起运行。
3. 安全保持：Phase 1 的逐 Run ACL、租户隔离、Capability Token、JWT claim 不可信、`DataQuality` 状态相关测试全绿。
4. 全量基线：每批次相关子集；阶段结束全量（含 PG），输出 PASS/FAIL/SKIP/NOT RUN。
5. 变更可审查：逐文件改动、风险、回滚点；每批次输出代码变更 + 测试结果 + 风险。
6. 未获批准的数据库迁移、环境切换、权限变化不得实施。

---

## 5. 预计涉及文件与模块（按批次）

### B0（仅测试/配置）
- `tests/test_subagent_timeout_cancel.py`、`test_subagent_integration.py`、`test_graph_integration.py`、`test_parent_close_policy.py`
- `tests/conftest.py`、`pyproject.toml`

### B1
- `workflow.py`、`activities.py`、`worker.py`、`outbox/commands.py`、`checkpoint/service.py`（可选）、`config.py`

### B2
- `api.py`、`contracts.py`、`graph/runner.py`、`graph/models.py`、`config.py`、`.env.example`

### B3
- `recovery/invocations.py`、`activities.py`、`worker.py`（可选）、`outbox/commands.py`（可选）、`platform_api.py`（若加 expected_fencing_token）

### B4
- `pyproject.toml`（markers/依赖）、4 个 Temporal 测试文件、新增 `tests/test_long_run_fast.py`、`tests/test_long_run_wall_clock.py`、`tests/test_checkpoint_service.py`、`tests/test_invocation_recovery.py`、本地 runner 配置

### B5（需单独批准）
- `docker-compose.yml`、`docker-compose.fi.yml`、`docker/temporal/*`、备份文档、`worker.py`/`temporal_client.py`（韧性，可选）、`workflow.py`/`graph/runner.py`（`workflow.patched`，改代码前）

### B6
- `tests/test_long_run_wall_clock.py`、`scripts/verify_worker_recovery.py`（timeout 可配置）

### B7（暂缓）
- 见 v1.0 计划 P0-B，本版不展开。

---

## 6. 风险与回滚（摘要）

| 风险 | 影响 | 缓解/回滚 |
|---|---|---|
| B0 条件 skip 被误用 | 中 | 开关仅控制环境；nightly 必设；报告如实分类；回滚=恢复文件 |
| B1 `execution_timeout` 提前终止长任务 | 高 | timeout=duration+margin；30min 用例验证；回滚=移除参数 |
| B1 新 activity 破坏 replay 确定性 | 高 | 用 `workflow.patched`；旧 history replay 测试；回滚=撤销 workflow 改动 |
| B2 自动上调掩盖配置错误 | 中 | 记录 original/adjusted/reason；超 200 返回 400；回滚=恢复校验前逻辑 |
| B3 fencing/幂等改错致状态覆盖 | 高 | 严格 CAS；真实 PG 测试；回滚=恢复原 CAS 条件（保留人工路径） |
| B5 持久化切换丢 history | 高 | staging 双跑；无长任务时切换；保留 dev overlay；回滚=切回 dev compose |
| B6 墙钟测试资源成本 | 中 | 自托管 runner；按需启动；SIGKILL 变体单独调度 |
| B7（暂缓）副作用重复 | 高 | 不启动；未来单独评估 |

**全局回滚**：按批次回滚；B5/B7 需人工批准后才可进入或回退。

---

## 7. 未解决/待批准事项

1. B1 的 `execution_timeout` 具体 margin 值与是否引入 `workflow.patched`（需在 B1 审查中确认）。
2. B2 的"平均步时长"计算模型与自动上调的审计事件字段（需 B2 前置确认表）。
3. B3 的现有字段充分性结论，以及是否需要单独提交迁移方案（需 B3 前置分析）。
4. B5 的 Temporal 版本选型、DB 账号设计、staging 双跑窗口、切换点（需单独方案+人工批准）。
5. B6 的自托管 runner 落点（本机 vs 现有服务器）与 nightly 调度时间（需人工确认）。
6. B7 默认不实施。

---

## 8. 等待确认

**当前状态**：计划已按五项批准决策更新至 v2.0。

**下一步**：等待人工确认 **B0 开始执行**。

确认 B0 后，我将：
1. 先做 B0 的代码审查（确认 4 个文件当前状态与 diff 范围）；
2. 明确修改清单；
3. 修改后执行 B0 相关测试与 Phase 1 基线回归；
4. 输出变更文件清单 + 测试报告 + 风险；
5. B0 验收通过后再申请 B1。

**本轮未修改任何业务代码、数据库、迁移、现有测试或安全限制；未初始化 Git，未上传代码。**

---

**报告结束。等待人工确认 B0 开始执行。**

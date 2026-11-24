# Phase 2 决策评审：五项待人工决策逐项分析

日期：2026-11-21
状态：**审查与方案设计完成，等待人工确认**
范围：`financial-agent-runtime`。本轮仅审查与设计，**未修改任何生产代码、未修改数据库结构、未执行迁移、未修改现有测试、未删除或放宽任何安全限制、未初始化 Git、未上传代码**。

评审输入：
- `docs/phase2/PHASE2_IMPLEMENTATION_PLAN.md`（下称《计划》）
- 五个待人工决策点（计划第 8 节）

本文件对每个决策点给出：当前代码事实与证据、当前问题与影响、至少两个可选方案及优缺点/风险、推荐方案及理由、预计修改文件、兼容性影响、对应测试与验收标准、是否需要人工确认。最后给出按依赖关系排列的实施路线。

---

## 决策 1：Checkpoint Restore 是否真的需要实现？

### 1.1 当前代码事实与证据

**三种恢复方式的现状**：

| 方式 | 当前状态 | 证据 |
|---|---|---|
| Temporal Replay 恢复 | **已实现并工作** | `worker.py:61-110` `record_worker_reconnect()` 写 `TEMPORAL_HISTORY_REPLAY` 等 4 事件；docstring :61-66 明确"不创建/重启 Workflow，也不从 Checkpoint 手动执行 step"；`workflow.py:63`、`:58` 与 `orchestration/child_registry.py:3-5` 由 replay 重建状态；SIGKILL 测试 `test_graph_integration.py:332`、`scripts/verify_worker_recovery.py` |
| Checkpoint 辅助恢复 | **部分实现** | `CheckpointSnapshotService.load()`（`checkpoint/service.py:196-236`）完整可解密校验，但生产唯一调用点 `api.py:308-316` `resume_run()` 只做完整性门禁，返回值被丢弃；workflow `resume` signal（`workflow.py:84-88`）只置标志，继续内存 state（:559-569） |
| 指定 Checkpoint 直接恢复 | **尚未实现** | 全库无 `restore` / `from_checkpoint` / 指定 `checkpoint_id` 恢复执行路径；`platform_api.py:423-444` 只有只读 metadata；`resume_run()` 不接受 checkpoint id/sequence |

**Checkpoint 保存能力（已实现）**：`service.py:68-194` canonical JSON→SHA-256→zlib→AES-256-GCM→对象存储 put/get 回读→DB Envelope→事件；保存点 `workflow.py:561`（暂停前）、`:804-805`（每 3 步）、`:827`（完成前）。

**Checkpoint 未保存的关键状态**：workflow 内部字段 `_pause_requested`/`_resume_requested`/`_cancel_requested`/`_user_input`/`_approval`/`_child_registry`/`_shutdown_reason`（`workflow.py:49-66`）、pending signals、Temporal history、activity 结果、graph 内部状态（仅 `graph_state_ref` 引用）。

**未接线能力**：`record_worker_recovery_activity`（`activities.py:390-451`，定义+在 `worker.py:153` 注册，但 `workflow.py` 无调用点）。

### 1.2 当前问题及影响

- 用户目标"Checkpoint 能真正恢复任务"与代码事实不符：Checkpoint 实际是"加密审计快照 + resume 前完整性门禁"。
- 如果强行实现"指定 checkpoint 直接恢复"，由于 checkpoint 不含 workflow 内部状态/pending signals/activity 结果，恢复出的 state 与 Temporal history 不一致，会带来**状态一致性风险**和**重复执行副作用风险**（tool/approval）。
- 反向风险：如果完全依赖 replay 而不做 checkpoint 恢复，则当 Temporal history 因 dev server 丢失、或 workflow 代码不兼容导致 replay 失败时，没有兜底恢复手段。

### 1.3 可选方案

**方案 A：仅 Temporal Replay + 可观测化（不做 Checkpoint Restore）**
- 内容：明确文档化"恢复源 = replay"；接入 `record_worker_recovery_activity` 替换纯审计；补 `execution_timeout`；让 replay 路径可证明、有事件、有 fencing。
- 优点：成本最低、风险最小、不触碰状态一致性难题；已有 replay 测试支撑。
- 缺点：Temporal history 丢失或 replay 失败时无业务 state 兜底；Checkpoint 的 `AVAILABLE_FOR_RECOVERY` 状态语义仍名不副实。

**方案 B：Replay 为主 + Checkpoint 辅助恢复（推荐）**
- 内容：恢复执行仍由 replay 负责；Checkpoint 用于三个辅助用途：① resume 前完整性门禁（已有）；② 事件/审计中记录 `checkpoint_id` 作为恢复锚点；③ 提供只读诊断 API 供人工检查快照（已有 metadata API 可扩展）。**不实现**从 checkpoint 注入状态到 workflow。
- 优点：兼顾成本与能力；避免状态不一致；把 checkpoint 定位为"审计与诊断"而非"执行点"，与现有代码一致；改动集中在观测层。
- 缺点：仍不能从 checkpoint 重建业务 state；若要求"跨 Temporal 故障恢复业务进度"，此方案不满足。

**方案 C：指定 Checkpoint 直接恢复（完整 P0-B）**
- 内容：新增 `POST /runs/{run_id}/restore`，`load()` 指定 checkpoint → 注入新 workflow state → 依赖 ToolInvocation CONFIRMED 复用 + approval consume 幂等防重复。
- 优点：提供 replay 之外的独立恢复路径；在 history 丢失时仍可恢复业务进度。
- 缺点：**高风险**。checkpoint 不含 workflow 内部状态/pending signals/activity 结果，恢复后 state 与 history 不一致；需处理 tenant/ACL/Capability 一致性；需解决 CONFIRMED 但 `result=None` 的边界（`invocations.py:146,211-212`）；需新增版本契约；测试成本高。

### 1.4 推荐方案及理由

**推荐方案 B（Replay 为主 + Checkpoint 辅助）**，不默认实现完整 Checkpoint Restore。

理由：
1. 代码事实表明 replay 恢复已实现且有测试证据；真正缺的是可观测性/原子性，不是恢复能力本身。
2. Checkpoint 数据结构决定它无法完整重建 workflow 内部状态，强行恢复会带来一致性与重复副作用风险，收益/风险比不佳。
3. 方案 B 把 `record_worker_recovery_activity` 死代码接入，正好补齐"恢复可证明"的缺口，属于低风险高收益。
4. 若后续人工验收认为必须支持"跨 Temporal 故障恢复业务进度"，再把方案 C 作为独立批次推进（计划 P0-B）。

### 1.5 预计修改文件（若采用推荐方案 B）
- `src/agent_runtime/workflow.py`（接入 `record_worker_recovery_activity`、恢复来源标签）
- `src/agent_runtime/activities.py`（恢复事件 activity 参数/语义）
- `src/agent_runtime/worker.py`（reconnect 审计与恢复标签对齐）
- `src/agent_runtime/checkpoint/service.py`（孤立对象清理、checkpoint_id digest 修正——可选）
- `docs/phase2/` 中补充"恢复语义说明"

### 1.6 对现有功能的兼容性影响
- 低：不新增 workflow 分支/activity 调用顺序变更；不改 checkpoint schema；不动安全约束。
- 注意：若在 `workflow.py` 主循环**新增 activity 调用**，会影响旧 history replay 的确定性（见决策 3 的版本兼容风险），必须用 `workflow.patched` 包裹或在非 replay 敏感点接入。

### 1.7 对应测试与验收标准
- 新增测试：patched Activity 调用链单测（monkeypatch）；Worker 重启后 DB 中存在带 `recovery_source` 的恢复事件；checkpoint 完整性门禁仍返回 409。
- 验收：Phase 1 的 188 passed 基线不回退；`test_graph_integration.py::test_d_worker_crash_recovery` 在 nightly 通过；不新增"从 checkpoint 恢复"的虚假断言。

### 1.8 是否需要人工确认
**需要**。确认是否接受"Replay 为主 + Checkpoint 辅助"的定位，即**暂不实现完整 Checkpoint Restore**。

---

## 决策 2：`max_steps=50` 应该如何处理？

### 2.1 当前代码事实与证据

**生效链路**：
- `contracts.py:63` `RunCreate.max_steps: int | None = Field(default=None, ge=1, le=200)` —— API 校验，非静默 clamp，超界 422。
- `api.py:179` `max_steps = body.max_steps or settings.max_agent_steps`；`config.py:24` 默认 `max_agent_steps=50`。
- `api.py:194-195` 写入 `WorkflowInput.max_steps`（`contracts.py:234` 进入 workflow 后必填 int，无字段约束）。
- `workflow.py:542` `while self._step < run_input.max_steps` —— **硬上限在循环顶部**；`:606` decide 成功后才 `self._step += 1`；`:838` 超限 `raise RuntimeError("run exceeded max_agent_steps")`，`:849-860` 落入 FAILED + `RUN_FAILED`，**非优雅失败**。
- `max_duration_seconds` 默认 7200（`config.py:23`），API 上限 86400（`contracts.py:64`）；watchdog（`workflow.py:159-179`）+ 每步 elapsed 检查（:553-557）触发 TIMED_OUT（相对优雅）。

**其他限制**：
- Graph 模式绕过 `max_steps`（`workflow.py:506-540` 直接 return），另有 `GraphLimits.max_node_steps=8`（`graph/models.py:45-51`，无 ge/le）、`graph/runner.py:557` 允许 API `graph.limits` 覆盖；但 `graph/runner.py:574` 把子 agent 步数固定取 `settings.graph_max_node_steps`（8），**用户通过 API 调大 max_node_steps 对子 agent 静默无效**。
- token/cost **无硬上限**：`checkpoint/models.py:67-74` 的 `max_tokens/max_cost_usd` 字段从未赋值，仅遥测记账（`workflow.py:615`）。
- context 截断（降级，不终止）：`activities_context.py:174-186`、`workflow.py:577-583`。
- tool result 超 20000 字符截断不终止（`activities.py:259-263`）。
- `recovery_count > 3` 失败，仅包 decide activity（`workflow.py:594-604`）。
- 子 agent timeout 900–1800s（config.py:65 / orchestration/models.py:96）。
- Root workflow 无 Temporal `execution_timeout`（`outbox/commands.py:123-126`）。

**安全/成本属性**：
- `max_steps` 不是显式的安全护栏（全库无 runaway/guardrail 关联注释），而是执行上限 + 观测指标（telemetry `current_step`/`progress_pct`，`workflow.py:243-247`；checkpoint `BudgetState.remaining_steps`，`:264`）。但它客观上起了成本控制作用。

### 2.2 当前问题及影响

- 默认 50 步是 30 分钟任务的实际天花板：若平均 36s/步即触顶，且触顶是 FAILED + 无部分结果落库。
- 用户可显式传 `max_steps≤200` 覆盖，但"duration=2h + steps=50"的组合未被校验，用户容易踩坑。
- Graph 模式下 `graph.limits.max_node_steps` 与子 agent 步数不一致，存在静默忽略。
- 删除步数限制会去掉唯一的执行上限，带来失控/成本风险，不可取。

### 2.3 可选方案

**方案 A：仅靠用户显式配置 + 文档（不改代码）**
- 内容：在文档中说明 30min 任务必须传 `max_steps`（如 ≥60–200）、Graph 模式还要设置 `GRAPH_MAX_NODE_STEPS`。
- 优点：零代码改动，不影响现有行为。
- 缺点：默认体验差；用户忘传就 FAILED；无法解决 Graph 静默忽略问题。

**方案 B：保留上限 + 联动校验 + 可调默认（推荐）**
- 内容：① 保留 `max_steps` 硬上限（1–200）作为安全/成本护栏；② 在 `api.py` 创建 run 时做 `max_steps` 与 `max_duration_seconds` 的一致性校验：若 duration ≥ 1800 且 `max_steps` 估算不足（例如 < ceil(duration / 60s)），返回 400 并说明，或自动上调到合理值并记录事件（需二选一，建议 400）；③ 修复 Graph 模式 `graph.limits.max_node_steps` 与子 agent 步数的不一致（`graph/runner.py:574` 改为取 `limits.max_node_steps` 并做合理 clamp）；④ 提供 env `MAX_AGENT_STEPS` 让运维按场景调整默认。
- 优点：不删除护栏；解决默认踩坑；修复静默忽略；成本可控。
- 缺点：需要小的 API 校验改动，可能让部分现有请求从"接受后失败"变为"立即 400"（兼容性变化，需评估）。

**方案 C：按时间自动推导步数（动态上限）**
- 内容：取消固定步数上限，按 `max_duration_seconds` 动态推导（如 `max_steps = ceil(duration / min_step_seconds)`），但保留绝对上限（如 1000）。
- 优点：用户友好；不需要理解步数概念。
- 缺点：改动契约语义大；动态推导依赖平均步时长假设，可能不准；仍需绝对上限；测试复杂度高。

### 2.4 推荐方案及理由

**推荐方案 B**。

理由：
1. 不简单删除步数限制，满足约束；保留 200 上限作为成本护栏。
2. 联动校验直接解决"duration 长但 steps 默认 50"这个 30 分钟任务的核心陷阱。
3. 修复 Graph 子 agent 步数静默忽略是明确 bug，属于小范围正确性修复。
4. 方案 C 的语义改动大，不适合在当前阶段引入。

### 2.5 预计修改文件
- `src/agent_runtime/api.py`（创建 run 的校验）
- `src/agent_runtime/contracts.py`（可选：校验错误信息或新字段）
- `src/agent_runtime/graph/runner.py:574`（子 agent 步数取 limits）
- `src/agent_runtime/config.py` / `.env.example`（默认步数文档化）
- 测试：新增 API 参数校验测试、Graph 步数传递测试

### 2.6 对现有功能的兼容性影响
- 中：原本"接受后因 steps 耗尽 FAILED"的请求会变为"立即 400"，属于行为改进但需下游知晓。
- Graph 子 agent 步数从固定 8 改为用户指定，会影响子 agent 行为上限，需确保 `GraphLimits.max_node_steps` 有合理 clamp（建议 1–200）。

### 2.7 对应测试与验收标准
- API 校验测试：`duration=1800, max_steps=50` → 400；`duration=1800, max_steps=200` → 201。
- Graph 测试：`graph.limits.max_node_steps=30` → 子 agent `WorkflowInput.max_steps=30`（不再是 8）。
- 回归：Phase 1 的 188 passed 不回退。
- 验收：30min 测试（决策 5）能显式设置足够 steps 且不被默认 50 影响。

### 2.8 是否需要人工确认
**需要**。确认联动校验采用"400 拒绝"还是"自动上调并记录事件"；确认保留 200 上限。

---

## 决策 3：Temporal 应该如何实现持久化？

### 3.1 当前代码事实与证据

**当前 Dev Server**：
- `docker-compose.yml:29-39` 镜像 `temporalio/temporal:latest`（未 pin），命令 `server start-dev --ip 0.0.0.0`，**无 environment、无卷、无 depends_on**，端口 7233/8233，healthcheck `temporal operator cluster health`。
- `docker-compose.fi.yml` 不含 temporal 服务，仅复用地址。
- 卷现状：`postgres_data`（业务库）、`snapshot_data`（本地 checkpoint）、`fi_data`；**Temporal 无卷**。
- 无任何备份脚本（`*.sh` 0 命中；`pg_dump|backup|restore` 无实现）。

**持久化需求**：
- 项目已有 `postgres:16-alpine`（compose:13-27）；SDK `temporalio>=1.15,<2`（`pyproject.toml:15`）。
- 推荐 SQL persistence + PostgreSQL（复用/新增独立 DB），需 `TEMPORAL_DB_TYPE=sql`、`TEMPORAL_SQL_DRIVER=postgres12`、DSN（建议独立 `temporal` database 与账号）、固定镜像版本（server 与 auto-setup 同 tag）。
- `temporal_namespace='default'`（`config.py:14`）、task queue='agent-runtime'（`config.py:15`）必须一致。
- `history-retention-period` 应 ≥ 最长 workflow（7200s）。

**Worker 重启与恢复**：
- `worker.py:128` `Client.connect(...)` 无 rpc retry/keepalive；`temporal_client.py:6-10` 同。
- Workflow ID 稳定：`agent-run/{tenant_id}/{run_id}`（`outbox/commands.py:115`、`api.py:247`），含 tenant 前缀，天然隔离。
- 重复 start 由 `handle.describe()` 兜底（`outbox/commands.py:117-128`）；signal 去重 `_accept_command`（`workflow.py:68-76`）。
- Worker 重启恢复靠 replay + 4 事件审计（`worker.py:61-110`），已实现。

**版本兼容**：
- 全库无 `workflow.patched` / `get_version` / `workflow.versioning`（0 命中）。
- Checkpoint schema 2.0 拒绝 1.0（`service.py:220-223`），无迁移器。
- replay 风险点：`workflow.py:442-510` 初始化序列、`:586` decide 及后续条件分支、`:396-435` failure 路径；`graph/runner.py` 的 graph 分支；`_processed_commands` 去重集合（:68-76）。修改这些处的 activity 调用顺序/数量会导致 `NonDeterministicError`。

### 3.2 当前问题及影响

- dev server 容器重建即丢 workflow history，无法证明"30 分钟长任务跨 Temporal 重启恢复"。
- 无备份/恢复流程，生产不可接受。
- 镜像 `latest` 未固定，`start-dev` 与 `server start` 参数语义可能漂移。
- 修改 workflow 代码时无版本化保护，旧 history replay 可能失败。

### 3.3 可选方案

**方案 A：继续使用 dev server + 卷（过渡）**
- 内容：给 dev server 加数据卷，保留 `start-dev`，固定镜像版本。
- 优点：改动最小。
- 缺点：`start-dev` 不是生产模式，仍不适合 HA/生产；Temporal 官方不推荐。仅可作为本地过渡。

**方案 B：SQL persistence + PostgreSQL，独立数据库，固定版本（推荐）**
- 内容：新增 `temporal` database/role；用 `temporalio/auto-setup:<固定版本>` 初始化 schema；temporal 服务改 `server start` + SQL DSN；加 `depends_on: postgres healthy`；保留 dev overlay 供本地；分阶段 staging 双跑后切生产。
- 优点：复用现有 PostgreSQL 运维栈；无新增 Cassandra 复杂度；history 可持久化；命名空间不变则对应用透明。
- 缺点：需要运维变更与切换窗口；切换点需避开运行中的长 workflow；回滚会丢失切换期间新 history。

**方案 C：Cassandra persistence**
- 内容：引入 Cassandra 集群作为 Temporal persistence。
- 优点：Temporal 官方大规模生产常用方案，水平扩展好。
- 缺点：新增整套运维栈，成本高；项目规模未必需要。

### 3.4 推荐方案及理由

**推荐方案 B，分阶段迁移，不直接生产重构。**

理由：
1. 项目已用 PostgreSQL，SQL persistence 是最小运维增量。
2. 独立 database 避免 Temporal schema 与业务 Alembic/RLS 互相污染。
3. 固定版本 + auto-setup 解决参数漂移与 schema 初始化。
4. 保留 dev overlay 和回滚路径，符合"不直接生产重构"要求。
5. 在修改 workflow 代码前必须引入 `workflow.patched`，这是独立的代码批次（计划中的版本兼容改造）。

### 3.5 预计修改文件
- `docker-compose.yml`（temporal 服务 command/environment/volumes/depends_on，新增 DB）
- `docker-compose.fi.yml`（确认无需覆盖或加持久化 overlay）
- 新增 `docker/temporal/*`（auto-setup 配置、备份脚本）
- `docs/phase2/`（升级/备份/restore 文档）
- API/Worker 韧性：`src/agent_runtime/worker.py:128`、`src/agent_runtime/temporal_client.py:6-10`（rpc retry/keepalive，可选批次）
- workflow 版本化：`src/agent_runtime/workflow.py`、`src/agent_runtime/graph/runner.py`（用 `workflow.patched` 包裹新增分支，改代码前必做）

### 3.6 对现有功能的兼容性影响
- 低（基础设施层）：只要 namespace=`default`、端口=7233、task queue='agent-runtime' 不变，应用代码无需变化。
- 中（代码层）：若同时改 workflow 代码，需 `workflow.patched` 保护旧 history；否则可能出现 NonDeterministicError。

### 3.7 对应测试与验收标准
- 持久化验收：启动 workflow → 停止并删除 temporal 容器 → 重新 up（卷/库保留）→ `describe` 同一 workflow id 成功，history 不丢。
- Worker 恢复验收：重启 temporal 后 worker 自动恢复轮询；SIGKILL 脚本 `verify_worker_recovery.py` 在持久化环境下通过。
- 版本兼容验收：用旧 history 做 replay 测试通过（新增 `WorkflowReplayer` 测试）。
- 备份验收：演练一次 `pg_dump` restore 到新 DB。

### 3.8 是否需要人工确认
**需要**。确认 PostgreSQL persistence（而非 Cassandra）、确认 staging 双跑窗口、确认切换点（无长时 workflow 运行时）。

---

## 决策 4：UNKNOWN 状态自动对账应该如何设计？

### 4.1 当前代码事实与证据

**状态机**：`recovery/invocations.py:14-24`
- `UNKNOWN → RECONCILIATION_REQUIRED / CONFIRMED / FAILED`；`RECONCILIATION_REQUIRED → CONFIRMED / FAILED`。
- `CONFIRMED` 真正不可逆；`FAILED` 可回 `DISPATCHED`；不支持 `RECONCILIATION_REQUIRED → UNKNOWN`。
- `require_reconciliation()`（:357-390）已实现但无调用方；实际 SQL 只接受 `UNKNOWN`，重复调用抛冲突（非幂等）。
- `reconcile()`（:392-434）需人工提供 `resolved_status ∈ {CONFIRMED, FAILED}`，可直接从 UNKNOWN 终结。

**幂等与 fencing**：
- 幂等键 `sha256(tenant:run:tool_call_id)`（`invocations.py:40-46`），唯一约束 `uq_tool_idempotency`（db.py:82-90）+ `uq_tool_invocation_call`（0007 迁移:51-54）。
- CONFIRMED 且 result 非空时复用（`invocations.py:146-154`，`activities.py:217-218`）。
- fencing token = Temporal activity attempt（`activities.py:179`），人工 reconcile = `record.fencing_token + 1`（`platform_api.py:574`），**共用数值空间且 CAS 用 `<=`，存在碰撞覆盖风险**（如当前 token=1 人工写 2，下一次 attempt 也是 2，会被接受）。
- `_mark_unknown()` 的 SQL 只有 token 条件无状态条件（`invocations.py:252-264`），并发 `confirm()` 可能冲突。
- 审计事件 `RunEvent`（`db.py:44-56`，`append_event` :730-740）**无幂等键**，自动重试会重复写事件。

**权限**：
- 人工 API `platform_api.py:549-584`：`_owned_run` 校验 tenant + `can_access_run`（owner 或 `runs:read:tenant`），再要求 `approval:approve` 或 `runs:manage:tenant`（OR）。
- 服务层 `reconcile()` 无 ACL 检查；自动任务直接调用会绕过 HTTP 层权限。
- `system_session` 的 RLS 放行上下文仅 `outbox_dispatcher`、`identity_resolver`（`0003_enable_rls.py:23-39`）；`system_session(..., "reconciler")` 会被 RLS 拒绝。

**重试/失败**：
- 无 provider 查询接口（`ToolDefinition` `tools/registry.py:8-14` 无查询方法；`execute_tool` 只执行）。
- 无对账 claim/lease/attempt 表；无 `IN_PROGRESS`；无最大尝试次数、dead-letter（Outbox 的 DEAD 不适用）。
- provider 持续失败时记录长期停在 UNKNOWN 或 RECONCILIATION_REQUIRED/PENDING。

### 4.2 当前问题及影响

- `require_reconciliation()` 断链导致 UNKNOWN 不会自动升级；人工 API 可用但依赖人盯。
- fencing token 混用可能让旧 worker 重试与人工对账互相覆盖，存在数据正确性风险。
- 无 provider 查询适配器，无法判断外部工具真实结果。
- 自动重试缺幂等与 attempt 记录，可能重复查询/重复审计。
- 权限边界若处理不当会绕过 ACL/RLS。

### 4.3 可选方案

**方案 A：最小修复——接线 require_reconciliation + 保持人工终结（推荐起步）**
- 触发条件：Activity 捕获"UNKNOWN/reconcile"异常后，在 workflow 中调用 `require_reconciliation()`（通过 activity）升级为 RECONCILIATION_REQUIRED；不自动查询 provider，不自动写终态。
- 执行入口：现有 `execute_tool_activity` 异常路径（`activities.py:250-257`）+ worker 循环新增一个轻量扫描（或复用 outbox 命令）；人工仍用 `POST /reconcile`。
- 幂等保护：用 invocation 状态 CAS（UNKNOWN→RECONCILIATION_REQUIRED 只成功一次）；自动任务不写自定义审计事件或写时带 attempt key。
- fencing：自动升级使用一个独立字段（如 `reconciliation_version`）而非复用 activity attempt；人工 reconcile 保持 `+1` 但改用严格 `expected_fencing_token` CAS。
- 优点：不绕过状态机/权限；改动有限；先解决"UNKNOWN 无人发现"的问题。
- 缺点：不自动判定结果，仍需人工。

**方案 B：两阶段自动建议 + 人工确认**
- 内容：新增 provider 查询适配器（按 `external_reference`/idempotency key 查询），自动任务查询后写 `reconciliation_status=SUGGESTED` + 证据，不写终态；人工通过扩展 API 确认建议后调 `reconcile()`。
- 优点：降低人工负担；安全（不自动写终态）。
- 缺点：需新增适配器接口、状态字段、API 字段、attempt 表；改动较大。

**方案 C：全自动对账（自动写终态）**
- 内容：provider 查询成功后自动 `reconcile(CONFIRMED/FAILED)`。
- 优点：完全自动化。
- 缺点：**高风险**。误判会导致错误终态；需要高置信度 provider 查询与审计；必须严格隔离权限；不建议当前阶段采用。

### 4.4 推荐方案及理由

**推荐"分两步"：先落地方案 A（接线 + 幂等/fencing 修正），再评估方案 B。不采用方案 C。**

理由：
1. 方案 A 解决最明确的 bug（`require_reconciliation` 无调用者）且不动安全边界。
2. fencing 混用与审计幂等是真实风险，必须在任何自动化前修正。
3. 方案 B 需要 provider 查询能力，当前代码完全没有，应在 A 验证稳定后作为独立批次。
4. 方案 C 的误判风险高，且用户要求"不得绕过现有状态机和权限控制"，全自动写终态与此冲突。

### 4.5 预计修改文件
- `src/agent_runtime/recovery/invocations.py`（幂等/fencing CAS 修正、可选 reconciliation_version）
- `src/agent_runtime/activities.py`（异常路径调用 require_reconciliation）
- `src/agent_runtime/worker.py`（对账扫描/命令入口，若采用）
- `src/agent_runtime/outbox/commands.py`（可选新增 `RECONCILE_INVOCATION` 命令类型及独立 dead-letter）
- `src/agent_runtime/platform_api.py`（人工 API 加 expected_fencing_token、建议确认字段，若采用 B）
- 新增 `src/agent_runtime/recovery/reconciler.py`（provider 适配器接口，若采用 B）
- 若新增状态/字段：数据库迁移（**需另行人工批准**，本轮不改）

### 4.6 对现有功能的兼容性影响
- 低-中：不改状态机的合法转换；人工 reconcile API 若加 `expected_fencing_token` 需保持向后兼容（可选字段）。
- 若新增 DB 字段/迁移，必须单独批次并做 up/down 测试。
- 必须保持 `_owned_run` + scope 校验与 RLS 不变。

### 4.7 对应测试与验收标准
- pytest（真实 PG）：UNKNOWN 自动→RECONCILIATION_REQUIRED；重复调用幂等不重复事件；人工 reconcile 后终态；`expected_fencing_token` 不匹配被拒；旧 attempt 迟到写入被 fencing 拒绝。
- 权限测试：无 `approval:approve`/`runs:manage:tenant` 被拒；跨租户运行 ID 404；service 层直接调用不能绕过 ACL（若有服务层测试）。
- 失败处理：provider 查询失败（若 B）不写终态，状态保持 PENDING 且有结构化原因；达到最大尝试次数后进入人工队列（dead-letter 语义待设计）。

### 4.8 是否需要人工确认
**需要**。确认是否采用"方案 A → B"路线；确认 fencing 是否新增独立字段（涉及迁移）；确认是否允许 provider 查询适配器（B）以及是否永远禁止自动写终态。

---

## 决策 5：30 分钟以上任务应该如何验证？

### 5.1 当前代码事实与证据

**测试基础设施现状**：
- 无 `TimeSkippingWorkflowEnvironment` / `start_time_skipping`（全库 0 命中）。
- `pyproject.toml:15` `temporalio>=1.15,<2`（SDK 支持 time-skipping，但本地未验证具体 API）。
- 4 个 Temporal 测试文件用 `os.environ.setdefault` 硬编码 DSN 绕过 skip（`test_graph_integration.py:21-26` 等），`Client.connect` 无超时保护；session loop + 无 `pytest-timeout`（`pyproject.toml:36`）导致挂起。
- 无 `.github` / CI yml / nightly job。
- 无真实 30 分钟墙钟测试；最长输入 900s（`test_graph_integration.py:72-88`），脚本 `verify_worker_recovery.py:36-60` 用 10min execution_timeout。
- `WorkflowReplayer` 0 命中；只有静态确定性检查 `test_graph_determinism.py:19-62`。

**九类场景覆盖现状**（详见审查报告）：
- 正常 30min：缺。
- Worker 重启：短时真实有（`test_subagent_timeout_cancel.py:326-366` 等）；30min 长跑无。
- Workflow Replay：无显式测试。
- Activity 超时/重试：短时 timeout 有；retry policy 多靠脚本。
- 用户取消：短时真实充分。
- 网络异常：有 fault injection 基础设施，无 pytest。
- 重复调度：有手工脚本，无 pytest。
- Checkpoint：仅单元/手工脚本。
- UNKNOWN 对账：代码+脚本，无 pytest。

**模拟时间的风险**：
- workflow 用 `workflow.now()` 确定性时间（`workflow.py:163-168,447-448`），但 activity/DB 用真实 `datetime.now()`（`activities.py:426,441`、`invocations.py`、`outbox/commands.py:52`），lease/expiry 不会随 time-skipping 推进。
- Activity 内真实 `asyncio.sleep` + heartbeat（`activities.py:34-40`）、外部 signal（API/outbox 真实 2s 轮询）、durable timer 精度都无法被 time-skipping 证明。
- 因此**模拟时间不能替代真实墙钟**。

### 5.2 当前问题及影响

- 没有可可靠执行的长任务测试；Phase 1 的 24 个 Temporal 用例因绕过 skip + 无超时被标 NOT RUN。
- 若只做 time-skipping 会给出"30 分钟任务已验证"的假象。
- 缺少 CI 基础设施，无法持续证明长时可靠性。

### 5.3 可选方案

**方案 A：仅 time-skipping 快速测试**
- 内容：用 `start_time_skipping` + 短 activity + 真实 PG，验证 deadline/watchdog/checkpoint 周期/signal 去重。
- 优点：CI 中 <5min 完成。
- 缺点：不能验证真实墙钟、心跳、网络、持久化；不能替代方案 B。

**方案 B：仅真实墙钟 nightly**
- 内容：真实 Temporal + PG + 35min 用例，每晚跑。
- 优点：真实可信。
- 缺点：反馈慢（每晚一次）、资源成本高（约 $0.32/次，$9.6/月；加 worker SIGKILL 变体翻倍）、无法快速迭代。

**方案 C：两层结合（推荐）**
- 快速层（PR 必跑）：time-skipping + 短 activity + 真实 PG，覆盖状态机逻辑。
- 慢速层（nightly）：真实 Temporal + PG + 35min 墙钟，覆盖真实时长/心跳/重启/持久化。
- 前提：先修复 4 个 Temporal 测试文件的可运行性（条件 skip + `pytest-timeout` + marker 注册 + `Client.connect` 超时）。

### 5.4 推荐方案及理由

**推荐方案 C，两层严格区分，不相互替代。**

理由：
1. 用户明确要求区分模拟时间与真实墙钟，且不能用前者替代后者。
2. 快速层提供日常回归保护；慢速层提供可信证据；二者互补。
3. 先修可运行性是前置条件，否则两层都无法稳定执行。

### 5.5 两层设计细节

**快速层（time-skipping）**：
- 环境：`WorkflowEnvironment.start_time_skipping()` + 真实 PG（`real_postgres_dsn`）+ 短耗时 mock activity。
- 覆盖场景：正常执行推进到 30min+（模拟）、Watchdog/deadline 触发 TIMED_OUT、用户取消与 cancel 优先、signal 去重/重复调度、checkpoint 保存周期、Activity 异常转 UNKNOWN 的状态流（用 mock）。
- 目标：<5min，PR 必跑。
- 限制声明：不验证 heartbeats 真实超时、网络、外部 signal 竞态、真实持久化。

**慢速层（真实墙钟 nightly）**：
- 环境：真实 Temporal（建议已在决策 3 完成持久化）+ 真实 PG + 专用 worker/task queue + 测试 activity。
- 参数：`max_duration_seconds=2100`（35min）、`max_steps≥200`、`execution_timeout=40min`（需决策/修复 outbox 未传该参数）、pytest-timeout 45min、CI job timeout 50min。
- 九类场景分配：
  - 正常 >30min：墙钟层主用例（断言 30min 前仍 RUNNING）。
  - Worker 重启：墙钟层变体（30min 中 SIGKILL worker 并重启，断言不丢/不重）。
  - Workflow Replay：墙钟层 + 新增 `WorkflowReplayer` 单测（两处都要）。
  - Activity 超时/重试：快速层 mock 状态机 + 墙钟层一个真实 heartbeat/timeout 用例。
  - 用户取消：两层都有（快速层竞态逻辑，墙钟层真实投递）。
  - 网络异常：fault injection overlay（`docker-compose.fi.yml`）在墙钟层跑一个用例。
  - 重复调度：快速层 + 真实 PG 的 outbox 幂等 pytest。
  - Checkpoint 保存/校验：快速层周期断言 + 真实 PG 服务级 pytest（persist/load/幂等/租户隔离）。
  - UNKNOWN 对账：真实 PG pytest（决策 4），墙钟层验证长跑中出现 UNKNOWN 后的人工/自动路径。

**报告分类**：PR job `pytest -q -m "not wall_clock_30m"`；nightly `-m wall_clock_30m`；环境缺失 SKIP，未调度 NOT RUN，不得计入 PASS。

### 5.6 预计修改文件
- `pyproject.toml`（markers 注册、pytest-timeout 依赖）
- 4 个 Temporal 测试文件（条件 skip、`Client.connect` 超时）
- 新增 `tests/test_long_run_fast.py`（time-skipping）
- 新增 `tests/test_long_run_wall_clock.py`（35min nightly）
- 新增 `tests/test_checkpoint_service.py`（服务级 persist/load）
- 新增 `tests/test_invocation_recovery.py`（UNKNOWN 对账）
- CI 配置新增（当前无，需人工确认平台后创建）

### 5.7 对现有功能的兼容性影响
- 低：仅测试与 CI 配置，不改生产代码；但参数联动（决策 2）与 `execution_timeout`（P0-A2）是前置修复。

### 5.8 对应测试与验收标准
- 快速层 <5min 通过；墙钟层 35min 用例在 45min pytest-timeout 内通过。
- 报告明确 PASS/FAIL/SKIP/NOT RUN；未调度 nightly 不得算 PASS。
- 九类场景都有明确的测试归属（至少一项 pytest 或一项 nightly）。

### 5.9 是否需要人工确认
**需要**。确认接受两层测试的资源成本（约 $9.6/月起，SIGKILL 变体翻倍）；确认 CI 平台与 nightly 调度；确认 35min 用例参数（duration=2100、steps≥200、execution_timeout=40min）。

---

## 6. 实施路线（独立批次与依赖关系）

按用户优先级和代码证据，建议以下批次顺序。**每个批次结束后人工验收，再进入下一批。**

| 批次 | 名称 | 对应决策/计划 | 前置依赖 | 主要内容 | 验收关键点 |
|---|---|---|---|---|---|
| B0 | Temporal 测试基础设施修复 | 决策 5 前提 / 计划 P0-A1 | 无 | 4 个测试文件条件 skip + Client.connect 超时 + pytest-timeout + markers | 裸机 `pytest -q` 30s 内完成且不挂起；24 用例如实 SKIP/NOT RUN |
| B1 | Replay 恢复可证明化/原子化 | 决策 1 方案 B / 计划 P0-A2/A3/A4 | B0 | 接入 `record_worker_recovery_activity`；设置 `execution_timeout`；checkpoint 孤立对象清理与 digest 修正；恢复来源标签 | 恢复事件可查；timeout 生效；Phase 1 安全测试不回退 |
| B2 | max_steps 联动与 Graph 步数修复 | 决策 2 方案 B / 计划 P1-4 | B0 | API 校验 duration/steps；Graph 子 agent 步数取 limits | 30min 配置不再因默认 50 步失败；无静默忽略 |
| B3 | UNKNOWN 对账：接线与幂等修正 | 决策 4 方案 A / 计划 P1-3 | B0 | 调用 `require_reconciliation`；fencing 分离；审计幂等；保持权限 | UNKNOWN 自动升级；无重复事件；旧写被拒；权限不绕过 |
| B4 | 长任务测试基础设施（两层） | 决策 5 / 计划 P1-1/P1-2 | B0、B1、B2、B3 | time-skipping 快速层 + 35min 墙钟 nightly 骨架；九场景归属 | 快速层 <5min；nightly 明确 SKIP/NOT RUN |
| B5 | Temporal 持久化分阶段迁移 | 决策 3 / 计划 P0-C | B0（稳定性）、B1（版本化准备） | 固定镜像 + 独立 PostgreSQL + auto-setup + 卷 + 备份文档；staging 双跑 | 重启 temporal 容器 history 不丢；备份演练通过 |
| B6 | 30 分钟真实任务验证 | 决策 5 / 计划 P1-1 | B5（建议，非硬依赖）、B4 | 在持久化 Temporal 上跑 35min 墙钟用例 + worker SIGKILL 变体 | 九类场景证据齐全；报告区分模拟/真实 |
| B7（条件） | 指定 Checkpoint Restore | 决策 1 方案 C / 计划 P0-B | B1、B5、B6；且人工明确要求 | 恢复契约、restore API、状态注入、副作用幂等 | 仅在人工确认"必须实现"后启动；否则取消 |

**依赖说明**：
- B0 是所有后续批次的前置，因为现有 Temporal 测试挂起会阻碍任何验证。
- B1 在 B5 之前做，因为 workflow 版本化（`workflow.patched`）需要在持久化环境中验证旧 history replay。
- B5 建议在 B6 之前完成，使 30min 墙钟测试能在持久化 Temporal 上验证 history 不丢；若时间紧，B6 可在 dev server 上先跑，但结果需标注"未验证持久化"。
- B7 完全条件化：默认不实施，除非决策 1 人工确认需要。

---

## 7. 需要人工确认的五项决策（汇总）

请将以下五项决定回复后，再按批准范围进入实施：

1. **决策 1 — Checkpoint Restore 是否实现？**
   推荐：方案 B（Replay 为主 + Checkpoint 辅助），**暂不实现完整 Checkpoint Restore**（B7 默认取消）。是否同意？

2. **决策 2 — max_steps=50 如何处理？**
   推荐：方案 B（保留 200 上限 + duration/steps 联动 400 校验 + 修复 Graph 子 agent 步数静默忽略）。确认采用"400 拒绝"还是"自动上调并记录事件"？

3. **决策 3 — Temporal 持久化方案？**
   推荐：方案 B（SQL persistence + 独立 PostgreSQL 数据库 + 固定镜像 + auto-setup + 分阶段 staging 双跑）。是否同意？确认 staging 双跑窗口与切换点。

4. **决策 4 — UNKNOWN 自动对账边界？**
   推荐：先方案 A（接线 `require_reconciliation` + 幂等/fencing 修正，保持人工终结），再评估方案 B（provider 建议 + 人工确认）；**不采用全自动写终态**。是否同意？是否批准新增 fencing/reconciliation 字段（涉及迁移）？

5. **决策 5 — 30 分钟验证的两层测试与资源成本？**
   推荐：方案 C（time-skipping 快速层 + 35min 真实墙钟 nightly）。是否接受每晚约 40–45min runner 占用（约 $9.6/月起，SIGKILL 变体翻倍）？确认 CI 平台与 nightly 调度。

---

**报告结束。本轮未修改任何生产代码/数据库/测试/安全限制，未初始化 Git，未上传代码。等待人工确认上述五项决策后，再按批准的批次进入实施。**

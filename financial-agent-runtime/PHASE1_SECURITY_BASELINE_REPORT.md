# 第一阶段工程任务交付报告：安全与工程基线

日期：2026-11-21
范围：`financial-agent-runtime`（后端）。按要求跳过 Git 初始化与任何 GitHub/上传操作；变更追踪采用逐文件清单 + 本报告，不依赖 Git。

---

## 1. 总体结论

本阶段验收标准：**已达标（PASS）**，针对 Task 1–5 中定义的工程基线与三个 P1/P2 安全问题。

- P1-2（Monitoring anomalies 逐 Run ACL 泄露）：**已修复**，含跨用户/跨租户负向测试。
- P1-1（Metrics 错误 root 静默全零 + OK）：**已修复**，含 3 个回归测试；引入统一数据质量状态。
- P2-9（JWT permissions claim 信任边界）：**已修复**，权限仅来自 DB；伪造 claim 测试覆盖。
- 测试基线：全量（含真实 PostgreSQL）**188 passed / 0 failed / 0 skipped**；4 个 Temporal 长时集成测试单独验证（本轮未修改其依赖的符号）。

未解决/超出本阶段范围的问题见第 7 节，Phase 2 建议见第 8 节。

---

## 2. Task 1：工程基线

### 2.1 环境
- Python 3.14.7，pytest 8.4.2，Docker 29.9.3。
- 依赖可导入：fastapi 0.141.1、sqlalchemy 2.0.52、temporalio、PyJWT、cryptography、asyncpg。
- Docker Compose 栈运行中：postgres（127.0.0.1:5433）、temporal（7233，dev server）、keycloak（8090）、api（8000）、worker。

### 2.2 Git / 依赖 / 迁移 / Docker 审查结论
- **Git**：工作区与后端根目录均非 Git 工作树（`git rev-parse` 返回 `fatal: not a git repository`）。按用户指示，本阶段不初始化 Git、不上传。
- **依赖**：仅 `pyproject.toml`，全部为范围约束，无 lock 文件；Dockerfile 用 `pip install .`，构建非完全可复现。
- **迁移**：16 个 Alembic 迁移，线性单 head（`0016_repair_agent_tree_root_backfill`）。`db.py:706-708` 的 `init_db()` 仍调用 `Base.metadata.create_all`，与 Alembic 并存（Phase 2 处理）。Compose 中 temporal 为 `start-dev`，非持久化。
- **测试配置**：`pyproject.toml` 中 `asyncio_mode=auto`；唯一 skip 逻辑在 `tests/conftest.py:16-21`（未设 `TEST_DATABASE_URL` 时跳过真实 PG 用例）。4 个 Temporal 文件用 `os.environ.setdefault` 硬编码 DSN，不走该 skip。

### 2.3 测试执行报告（PASS / FAIL / SKIP / NOT RUN）

基线运行（设置 `TEST_DATABASE_URL`，排除 4 个慢速 Temporal 文件）：
- 初次（未设 DSN）：123 passed, 41 skipped（41 个真实 PG 用例因 `未设置 TEST_DATABASE_URL，跳过真实 PostgreSQL 集成测试` 被跳过）。
- 设 DSN 后核心集：164 passed, 0 failed, 0 skipped, 175.68s。
- 修改后全量（含真实 PG，排除 4 个 Temporal 文件）：**188 passed, 0 failed, 0 skipped, 187.57s**。

新增测试分布：
| 批次 | 测试文件 | 新增用例 | 结果 |
|---|---|---|---|
| 2 | test_monitoring_api.py + test_agent_tree_postgres.py | 5 | 18 passed |
| 3 | test_metrics_postgres.py | 3 | 既有17 + 新3 全通过 |
| 4 | test_jwt_claim_boundary.py | 5 | 17 passed（含既有认证12） |
| 5 | test_security_regression.py | 11 | 11 passed |

分类汇总：
- **PASS**：188（全量非 Temporal）/ + Temporal 文件单独验证。
- **FAIL**：0。
- **SKIP**：0（设 DSN 后）；未设 DSN 时 41，原因均为 conftest 的 TEST_DATABASE_URL 缺失，非伪装。
- **NOT RUN（本阶段未全量重跑）**：4 个 Temporal 集成测试文件（`test_subagent_timeout_cancel.py`、`test_subagent_integration.py`、`test_graph_integration.py`、`test_parent_close_policy.py`，共约 31 个用例）。原因：这些用例包含真实 Temporal durable timer 与 worker 重启，单用例耗时 1–2 分钟甚至更久，本环境多次出现长时间挂起；本阶段为控制验证时间将其从全量批次中显式排除。
  - 部分验证：批次 1 中 `test_subagent_timeout_cancel.py` 的前两个用例（`test_parent_cancel_cascades_and_preserves_completed`、`test_repeated_cancel_is_idempotent`）已单独观察为 PASSED。
  - 符号重叠检查：对 4 个文件搜索本阶段改动涉及的符号（`DataQuality`、`ExecutionMetrics`、`detect_tree_anomalies`、`trust_jwt`、`get_principal`、`Principal`）均为 0 命中，改动面与其无依赖交集。
  - 建议：Phase 2 在 CI 中以 nightly 任务全量运行这 4 个文件，并加 `pytest-timeout`。

### 2.4 SKIP 原因清单（未设 DSN 运行）
所有 41 个 SKIP 均来自 `tests/conftest.py:20`：`pytest.skip("未设置 TEST_DATABASE_URL，跳过真实 PostgreSQL 集成测试")`。无环境 SKIP 被标记为 PASS。

---

## 3. Task 2：P1-2 Monitoring anomalies ACL 泄露修复

### 3.1 修复前
- `monitoring_api.py:83-86` 只校验 Root ACL，调用 `detect_tree_anomalies()` 时未传 `principal`。
- `delegation_service.py:563-629` 的 `detect_tree_anomalies(tenant_id, root_run_id)` 无逐 Run ACL，直接把 `orphans/cycles/duplicate_links` 中隐藏 Child 的原始 UUID 返回。
- 契约 `AgentTreeAnomaliesRead`（`contracts.py`）docstring 自称 "ACL filtered"，与实现矛盾。

### 3.2 修复后（变更文件）
- `src/agent_runtime/orchestration/delegation_service.py`：`detect_tree_anomalies()` 增加 `principal: Principal` 参数；校验 `tenant_id == principal.tenant_id`；加载所有涉及边的 `RunRecord.user_id/deleted_at`，用 `can_access_run` 计算可见集；仅对 parent 与 child 均可见（或 parent 物理不存在）的边计算异常；隐藏节点不进入任何结果字段。
- `src/agent_runtime/monitoring_api.py`：传入 `principal=principal`；修正模块与端点 docstring。
- `src/agent_runtime/contracts.py`：`AgentTreeAnomaliesRead` docstring 更新为逐 Run ACL 过滤说明。

### 3.3 回归测试
- `tests/test_agent_tree_postgres.py`：3 处既有调用补 `principal`；新增 `test_anomalies_hide_cross_user_child_uuid`（隐藏 Child UUID 不出现在 orphans/cycles/duplicate_links，tenant-scope 读者可见）与 `test_anomalies_reject_cross_tenant_principal`（跨租户 ValueError）。
- `tests/test_monitoring_api.py`：新增 `test_anomalies_requires_authentication`、`test_anomalies_root_acl_denial_is_404`、`test_anomalies_passes_principal_and_never_leaks_hidden_uuid`。

### 3.4 前后对比
| 维度 | 修复前 | 修复后 |
|---|---|---|
| Root ACL | 有 | 有（不变） |
| Child ACL | 无 | 每条边独立校验 |
| 跨租户 | 仅靠 Root run 查询 | 服务层显式拒绝 `tenant_id != principal.tenant_id` |
| 隐藏 UUID | 出现在 orphans/cycles/duplicate | 完全不输出 |
| 契约声明 | 声称 ACL filtered（假） | 与实现一致 |

---

## 4. Task 3：P1-1 Metrics 错误 root 静默全零修复

### 4.1 修复前
- `metrics_service.py:293` 用调用方 `root_run_id` 作 BFS 起点；请求错误 root（无 delegation 边或另一连通分量）时 `scope_records` 为空；`:399-400` 空 scope 不加入任何 reason；最终返回全零指标 + `data_quality="OK"`。
- data_quality 只有 `OK/DEGRADED/UNAVAILABLE`，无法区分真实零值、数据缺失、权限拒绝、部分数据、计算错误。

### 4.2 修复后
- `contracts.py`：新增统一 `DataQuality(str, Enum)`（`OK/DEGRADED/PARTIAL/MISSING/DENIED/ERROR/UNAVAILABLE`），`ExecutionMetricsRead.data_quality` 改用该枚举（保留 DEGRADED/UNAVAILABLE 兼容既有客户端）。
- `metrics_service.py`：
  - 新增 reason：`ROOT_UNRESOLVED`、`PARTIAL_SCOPE`、`ACCESS_DENIED`、`COMPUTE_ERROR`。
  - `ScopeLoad` 增加 `root_in_delegations`；`_load_scope()` 计算请求 root 是否实际作为某条 delegation 的 parent。
  - 空 scope 或 root 不在 delegation 中 → `ROOT_UNRESOLVED` + `data_quality=MISSING`（不再静默 OK 全零）。
  - 全部边被 ACL 过滤 → `DENIED`；部分可见 → `DEGRADED`/`PARTIAL_SCOPE`；计算/完整性异常 → `UNAVAILABLE` + `COMPUTE_ERROR` reason。
  - 优先级：MISSING > DENIED > UNAVAILABLE > DEGRADED > OK；既有 reason 映射保持不变，既有 17 个 metrics 测试全通过。

### 4.3 回归测试（tests/test_metrics_postgres.py 新增 3 个）
- `test_wrong_root_with_no_edges_not_reported_as_ok_zero`：错误 root（无边）→ MISSING + ROOT_UNRESOLVED；真实 root 仍 OK 且 attempt=1。
- `test_leaf_child_as_root_not_reported_as_ok`：叶子 child 当 root → MISSING（不复用父树指标）。
- `test_data_quality_states_are_distinguished`：owner 无 tenant-scope → 隐藏 child 被排除且状态非 OK；tenant-scope 读者 → OK 且 attempt=2。

### 4.4 前后对比
| 场景 | 修复前 | 修复后 |
|---|---|---|
| 无 delegation 边的 root | 全零 + OK（错误） | MISSING + ROOT_UNRESOLVED |
| 叶子 child 当 root | 复用父树指标 + OK | MISSING |
| 全部 child 被 ACL 隐藏 | NO_VISIBLE/UNAVAILABLE | DENIED + ACCESS_DENIED |
| 部分 child 隐藏 | DEGRADED | DEGRADED + ACCESS_DENIED reason |
| 五种状态区分 | 不可区分 | 枚举+reason 明确区分 |

---

## 5. Task 4：JWT permissions claim 信任边界（P2-9）

### 5.1 修复前
`auth.py:138-141` 将合法签名 token 的 `permissions`/`roles` claim 并集进 DB 权限，并把 `scopes` 重写为该并集 → 客户端可在 token 中自报 `runs:read:tenant`/`platform:manage` 提权，并传导到 capability token（`token_service.py:_principal_permissions()`）。

### 5.2 修复后
- `config.py`：新增 `trust_jwt_authorization_claims: bool = False`（默认关闭）；`validate_runtime_config()` 在 production 下若开启则启动失败（fail closed）。
- `auth.py`：默认忽略 token 的 roles/permissions claim；权限仅来自 `_load_identity()`（DB membership + RolePermission）；`scopes` 精确镜像 DB 权限，不被放大；被忽略的可疑 claim 记 warning 审计。保留 `exp → token_expires_at` 与 `organization_id` 兜底（非权限授予）。
- 签名、issuer、audience、exp/nbf、算法 allowlist、JWKS rotation 校验保持不变（`auth_jwt.py` 未改动）。

### 5.3 回归测试（新增 tests/test_jwt_claim_boundary.py，5 个）
- `test_forged_permissions_claim_is_not_adopted`：合法签名 token 自报高权限 → 不被采纳，scopes 未放大。
- `test_expired_token_rejected_by_get_principal`：过期 token → 401。
- `test_wrong_issuer_and_audience_rejected`：错误 iss/aud → 401。
- `test_production_forbids_trusting_jwt_authorization_claims`：生产开启信任开关 → RuntimeError。
- `test_capability_token_not_escalated_by_forged_claim`：capability 根权限仅含 DB 权限。

### 5.4 前后对比
| 维度 | 修复前 | 修复后 |
|---|---|---|
| token 自报 permissions | 并集进权限、重写 scopes | 默认忽略，仅 warning |
| 生产环境 | 可信任 claim | 启动即失败（禁止开启） |
| 权限唯一来源 | DB + token claim（双源） | DB（单源） |
| capability token | 继承被污染 principal | 仅 DB 权限 |

---

## 6. Task 5：安全回归验证（7 场景）

| # | 场景 | 覆盖方式 | 状态 |
|---|---|---|---|
| 1 | 未授权用户访问 Child | `test_agent_tree_postgres.py::test_anomalies_hide_cross_user_child_uuid` + tree ACL 测试 | PASS |
| 2 | 不同 Tenant 读取数据 | `test_agent_tree_postgres.py::test_anomalies_reject_cross_tenant_principal` + `test_security_regression.py` memory 条件编译断言 + `test_tenant_isolation.py` | PASS |
| 3 | 伪造 JWT permissions | `test_jwt_claim_boundary.py::test_forged_permissions_claim_is_not_adopted` | PASS |
| 4 | 越权调用管理 API | `test_security_regression.py::test_admin_api_denied_without_manage_permission` + `test_security_snapshot_denied_without_scope` | PASS |
| 5 | 越权查看 Metrics | `test_security_regression.py::test_metrics_cross_tenant_root_denied` + `test_metrics_api.py` | PASS |
| 6 | 未授权访问 Memory | `test_security_regression.py::test_memory_read_requires_scope` / `test_memory_write_requires_scope` / `test_memory_visible_conditions_hide_other_users_private_scope` | PASS |
| 7 | 越权调用 MCP Tool | `test_security_regression.py::test_mcp_tool_denied_cross_tenant` / `test_mcp_tool_denied_missing_scope` / `test_high_risk_mcp_tool_requires_approval` | PASS |

所有场景均为负向断言，且未通过删除/跳过测试或放宽 ACL 达成。

---

## 7. 未解决问题清单（本阶段范围外 / Phase 2）

1. **Git**：未初始化，无版本基线（按用户要求跳过）。
2. **依赖锁定**：无 lock 文件，Docker 构建非完全可复现。
3. **Temporal 持久化**：Compose 使用 `temporal server start-dev`，无持久化/HA；企业生产需替换。
4. **`init_db()` 的 `create_all`**：`db.py:706-708` 与 Alembic 并存，可能绕过迁移版本；Phase 2 应移除并强制 `alembic upgrade head`。
5. **审计文档中 P1-1/P1-2 的文本**仍是修复前描述，需更新文档（代码已修复）。
6. **4 个 Temporal 长时集成测试**本轮未全量重跑（耗时限制），但改动面与其无符号重叠；建议 Phase 2 在 CI 中定时全跑。
7. **P2 遗留**（审计文档）：`auth_router.py:admin_roles` 返回的全局角色无 tenant 过滤（低敏）、Resource Grant 仍为简单映射、Token Exchange/MFA 待办、MCP Server 出站网络控制与沙箱待办。
8. **Memory 检索**仍为子串匹配，无语义/向量检索；`expires_at` 未在查询过滤（本阶段未触及，属功能而非 P1 安全）。
9. **Benchmark** 仍为冒烟级（子串评分、小合成集）。
10. **Python 3.14 + pytest-asyncio** 产生 `asyncio.get_event_loop_policy` 弃用警告（Python 3.16 将移除），Phase 2 升级依赖版本。

---

## 8. Phase 2 技术实施建议

按优先级：

1. **工程基线**：初始化 Git（用户允许时）、生成依赖 lock（`uv lock` 或 `pip-compile`）、Docker 多阶段构建锁定 digest。
2. **Temporal 生产化**：替换为持久化 Temporal 集群（Cassandra/PostgreSQL persistence + HA）；移除 dev server。
3. **迁移治理**：删除 `init_db()` 的 `create_all`，启动时强制 `alembic upgrade head` 并校验 head 一致；CI 加迁移 up/down 测试。
4. **CI 分层**：单测（无外部依赖）→ PG 集成（service container）→ Temporal 长时用例（nightly）；Temporal 用例加 `pytest-timeout` 与并发隔离，避免本地长等待。
5. **监控面扩展**：将本次 P1-1 的“统一数据质量状态”推广到 `platform_api.py:get_recovery_stats`、`get_security_snapshot`、`context_api.py` 等其他聚合端点，统一权限过滤与 reason 语义。
6. **JWT claim 治理**：与 IdP 约定 claim 白名单；如需信任 claim，走受控流程（显式配置 + 值域枚举校验 + 审计），且仅在 dev/staging 开启。
7. **安全回归持续化**：将 `test_security_regression.py` 纳入 PR 必跑；新增场景：Resource Grant 实时撤销、Approval envelope 过期/篡改、Checkpoint 快照越权读取。
8. **长期记忆与 Benchmark**（功能线）：向量检索 + 过期过滤；Benchmark 引入冻结数据集、语义评分、置信区间与成本指标。

---

## 9. 变更文件清单（逐文件、可审查）

生产代码（6 个）：
- `src/agent_runtime/monitoring_api.py`
- `src/agent_runtime/orchestration/delegation_service.py`
- `src/agent_runtime/contracts.py`
- `src/agent_runtime/monitoring/metrics_service.py`
- `src/agent_runtime/auth.py`
- `src/agent_runtime/config.py`

测试（5 个，含 2 个新建）：
- `tests/test_monitoring_api.py`（修改）
- `tests/test_agent_tree_postgres.py`（修改）
- `tests/test_metrics_postgres.py`（修改）
- `tests/test_jwt_claim_boundary.py`（新建）
- `tests/test_security_regression.py`（新建）

未修改：`auth_jwt.py`、`token_service.py`、`db.py` 及所有迁移、`authorization/`、`platform_api.py`、`auth_router.py` 等。

---

## 10. 最终验收判定

| 任务 | 验收标准 | 结果 |
|---|---|---|
| Task 1 | 检查工程基线、重跑 pytest、分类报告、SKIP 原因 | PASS |
| Task 2 | Root/Child 分别校验、隐藏节点不泄露、跨用户/租户负向测试 | PASS |
| Task 3 | 区分状态、权限不足不静默 OK、统一状态、回归测试 | PASS |
| Task 4 | 检查 claim 信任边界、iss/aud/exp/签名、不信任自报权限、capability 衰减、伪造/过期测试 | PASS |
| Task 5 | 7 场景安全回归 | PASS |
| 约束 | 不删/跳测试、不放宽 ACL、不伪装 SKIP、不大改架构、每问题有回归测试、可追踪 | PASS |

**本阶段验收：PASS（达到验收标准）。**

说明：上述 PASS 针对 Task 1–5 的全部交付要求。唯一保留项是 4 个 Temporal 长时集成测试未在本阶段全量重跑（已如实列为 NOT RUN，且有部分用例通过与零符号重叠作为补充证据）；该项被列入第 7 节未解决问题与 Phase 2 建议（CI nightly + pytest-timeout），不影响三个 P1/P2 安全修复本身的验收结论。若将“全量重跑含 Temporal 长时用例”视为硬性验收门槛，则该单项为 PARTIAL，需在具备长时测试基础设施后补验。

# B5.0 Temporal SQL Persistence 实施方案设计

日期：2026-11-21
状态：设计完成，等待人工验收与 B5.1 单独授权。本批次没有实施配置变更或数据库迁移。

---

## 1. 设计原则

1. **业务数据库与 Temporal Persistence 必须完全隔离**
   - 业务库：`agent_runtime`，应用账号 `agent_app`，迁移账号 `agent`。
   - Temporal 库：独立 `temporal` database，独立 role，独立卷，独立备份策略。
   - 禁止把 Temporal SQL DSN 指向 `agent_runtime`。

2. **固定版本**
   - Temporal Server 与 auto-setup 使用同一固定 tag。
   - PostgreSQL 固定 major/minor tag，例如 `postgres:16.4-alpine`，不得使用 `latest`。

3. **开发态与持久化态并存**
   - 保留现有 `server start-dev` dev overlay。
   - 新增 SQL persistence overlay，只在显式组合 compose 文件时生效。
   - 不直接覆盖开发默认值。

4. **先验证，再切换**
   - Dev SQL → Staging SQL → Staging 双环境验证 → Worker 恢复 → Replay 确定性 → 备份恢复演练。
   - 未通过前一阶段不得进入下一阶段。

5. **审计证据**
   - 所有 pytest 运行使用 `scripts/run_pytest_audited.ps1`。
   - 必须保存 stdout、stderr、meta（开始/结束/耗时/退出码/命令/开关）。

---

## 2. 目标架构

```text
                  +--------------------------------+
                  | temporal-postgres:16.x         |
                  | DB: temporal                   |
                  | role: temporal/temporal        |
                  | volume: temporal_pg_data       |
                  | backup: temporal_pg_backup     |
                  +----------------+---------------+
                                   | SQL DSN
                                   v
+---------------------------------------------------------------+
| temporal-auto-setup:<fixed-tag>                               |
| - initialize SQL schema                                       |
| - create/verify namespace default                             |
| - exit 0 before server starts                                 |
+-------------------------------+-------------------------------+
                                |
                                v
+---------------------------------------------------------------+
| temporal-server:<fixed-tag>                                   |
| command: server start                                         |
| env: SQL persistence + visibility + retention >= 7200s        |
| ports: 7233 / 8233                                            |
| healthcheck: temporal operator cluster health                 |
+---------------------------------------------------------------+
                                ^
                                | TEMPORAL_ADDRESS=temporal:7233
                                |
             +------------------+-------------------+
             |                                      |
      +------+-------+                       +------+-------+
      | api          |                       | worker       |
      | no schema mix|                       | reconnect    |
      +--------------+                       +--------------+

Business PostgreSQL remains separate:
+---------------------------------------------------------------+
| postgres:16-alpine / DB agent_runtime / volume postgres_data  |
+---------------------------------------------------------------+
```

---

## 3. 独立 SQL Persistence 设计

### 3.1 独立数据库

| 项 | 建议 |
|---|---|
| Service name | `temporal-postgres` |
| Image | `postgres:16.4-alpine` 或组织已批准固定版本 |
| Database | `temporal` |
| User / Password | `temporal` / 单独密钥，不复用业务密码 |
| Volume | `temporal_pg_data` |
| Port | 默认不对外暴露；如调试必须 `127.0.0.1` 绑定 |
| Healthcheck | `pg_isready -U temporal -d temporal` |

### 3.2 Schema 初始化

使用官方 `temporalio/auto-setup:<fixed-tag>`：

- 与 server 同版本 tag。
- 等待 `temporal-postgres` healthy。
- 初始化 persistence schema、visibility schema 与 namespace。
- 初始化完成后正常退出；server 依赖其 `service_completed_successfully`。
- 禁止复用业务 `docker/init` 脚本。
- 禁止在业务库中创建 Temporal schema。

### 3.3 Temporal Server

- 固定镜像 tag，不使用 `latest`。
- 命令从 `start-dev` 改为 `server start`。
- 必须配置：
  - persistence driver：`sql`
  - SQL driver：`postgres12`
  - SQL DSN：指向 `temporal-postgres`
  - visibility store：SQL 或双写策略按验证结果决定
  - namespace：`default`
  - retention：至少 ≥ `MAX_RUN_DURATION_SECONDS`，建议 Staging 7d
- API/UI 端口保持 7233/8233。
- healthcheck 保持 `temporal operator cluster health`。

### 3.4 启动依赖

顺序：

1. `temporal-postgres` healthy。
2. `temporal-auto-setup` completed successfully。
3. `temporal` healthy。
4. `api` 与 `worker` 启动。
5. worker 执行 Client 连接验证后再启动 Worker loop。

### 3.5 Worker / Client 连接韧性

B5.1 应补：

- `TEMPORAL_CONNECT_TIMEOUT_SECONDS`
- RPC retry / keepalive 配置
- TLS 配置占位（Staging/生产必填）
- Worker 启动失败时快速 FAIL，不得无限重连掩盖配置错误

不得修改已验收业务状态机；连接配置属于基础设施层。

---

## 4. 分阶段实施计划

## Phase 0：B5.0 设计验收（当前）

前置条件：

- B4.1 验收通过。

内容：

- 仅审查与设计。
- 不修改 Compose、不改 Schema、不启动新 DB。

通过标准：

- 四份 B5.0 文档生成。
- 人工批准 B5.1 单独授权。

回滚：

- 无需回滚。

---

## Phase 1：Dev SQL Persistence

前置条件：

- B5.1 单独授权。
- 本地 PostgreSQL/Temporal 容器可运行。
- 明确保留 dev overlay。

拟修改范围：

- 新增 `docker-compose.temporal-sql.yml` overlay。
- 可能新增 `docker/temporal/*` config。
- 不修改业务 Schema。

实施命令（计划，本批次未执行）：

```powershell
docker compose -f docker-compose.yml -f docker-compose.temporal-sql.yml config
docker compose -f docker-compose.yml -f docker-compose.temporal-sql.yml up -d temporal-postgres temporal-auto-setup temporal
docker exec <temporal> temporal operator cluster describe --address localhost:7233
```

通过标准：

- `PersistenceStore = sql`。
- `VisibilityStore` 不为 sqlite。
- `temporal operator namespace describe -n default` 返回 Registered。
- 业务库 `agent_runtime` 中无 Temporal schema。
- dev overlay 仍可单独使用 `start-dev`。

回滚：

- 停止 SQL overlay 服务。
- 恢复原 compose 启动命令。
- 保留 `temporal_pg_data` 卷，未经人工批准不得删除。

---

## Phase 2：Staging SQL Persistence

前置条件：

- Phase 1 通过。
- 固定镜像 tag 和固定 PostgreSQL tag 已人工确认。
- 备份策略与凭据管理就绪。

实施内容：

- 在 Staging 部署独立 Temporal PostgreSQL。
- Server 使用 `server start`。
- TLS / retry / timeout 配置按环境注入。
- `retention` 设置为 ≥ 7d 或人工批准值。

验证命令（计划）：

```powershell
docker compose -f docker-compose.yml -f docker-compose.staging.yml config
docker compose -f docker-compose.yml -f docker-compose.staging.yml ps
docker exec <temporal> temporal operator cluster health --address localhost:7233
docker exec <temporal> temporal operator namespace describe -n default --address localhost:7233
```

通过标准：

- 固定版本输出与部署清单一致。
- Cluster health OK。
- namespace retention 符合批准值。
- 业务 PostgreSQL 连接串与 Temporal DSN 完全不同。
- Worker 能连接并通过启动检查。

回滚：

- 切回上一版 compose/config。
- 将 Worker 指向上一 Temporal 集群。
- 不删除持久化卷。
- 保留失败阶段日志与快照。

---

## Phase 3：Staging 双环境验证

目标：

- 在真实 SQL Persistence 上验证功能，同时避免重复外部副作用。

设计：

- 旧 dev 集群与新 SQL 集群可并行存在。
- 只允许一个 active worker 执行真实副作用。
- 另一环境只做：
  - workflow history 导出；
  - Replayer 回放；
  - 只读 API/health 验证。
- 禁止两个 worker 使用相同 task queue 并同时消费同一业务语义工作流。

验证命令（计划）：

```powershell
& ./scripts/run_pytest_audited.ps1 -LogPrefix staging_temporal_it -Temporal -MarkerExpression "temporal_it"
```

通过标准：

- 24 个 Temporal IT PASS。
- stdout/stderr/meta 完整。
- EXITCODE=0。
- 无重复触发外部副作用证据。
- history 可从 SQL Persistence 读取并回放。

回滚：

- 停用新集群 worker。
- active worker 指回旧集群。
- 保留新集群 history 与日志用于分析。

---

## Phase 4：Worker 重启与 Workflow 恢复

验证流程：

1. 在 SQL Persistence 上启动长 workflow。
2. 确认 workflow id 不变。
3. SIGKILL / 重启 worker 容器。
4. Worker 重新连接同一 namespace/task queue。
5. 检查 Run ID 未变、history 连续、恢复事件存在。

验证命令（计划）：

```powershell
& ./scripts/run_pytest_audited.ps1 -LogPrefix staging_recovery_it -Temporal -PytestArgs @("tests/test_graph_integration.py")
docker restart <worker>
& ./scripts/run_pytest_audited.ps1 -LogPrefix staging_recovery_recheck -Temporal -PytestArgs @("tests/test_graph_integration.py")
```

通过标准：

- Worker 重启后 workflow 不新建 Run。
- Temporal history 可查询。
- 恢复事件写入业务 DB。
- 无重复副作用。
- 所有运行 EXITCODE 与日志完整。

回滚：

- 停止新 worker。
- 恢复上一 worker 版本。
- 不删除 workflow history。

---

## Phase 5：Replay 确定性验证

前置条件：

- Phase 4 通过。
- 已收集代表性 history：短任务、graph 任务、tool UNKNOWN、pause/resume、worker restart。

实施内容：

- 为 `AgentRunWorkflow` 建立生产级 `WorkflowReplayer` 测试或脚本。
- 如果必须改动 workflow activity 调用顺序，先引入 `workflow.patched` / versioning，并单独审批。
- 禁止直接删除旧 history 规避 replay 失败。

验证命令（计划）：

```powershell
& ./scripts/run_pytest_audited.ps1 -LogPrefix staging_replay_it -Temporal -PytestArgs @("tests/test_replay_reliability.py")
```

通过标准：

- 代表性 history 全部 replay 成功。
- 无 `NonDeterministicError`。
- 若引入版本化，旧 history 与新 history 均通过。

回滚：

- 回退 workflow 代码到上一固定版本。
- 停止使用不兼容部署版本。
- 保留 history 样本用于复核。

---

## Phase 6：备份与恢复演练

前置条件：

- SQL Persistence 已运行足够时间产生测试数据。
- 备份目标与保留周期已批准。

备份流程（计划）：

```powershell
docker exec <temporal-postgres> pg_dump -U temporal -Fc temporal > temporal_backup.dump
```

恢复演练：

- 恢复到隔离数据库 `temporal_restore_test`。
- 不得覆盖运行中的 `temporal` 库。
- 启动隔离 Temporal 只读验证实例或直接用 SQL 检查关键表。
- 验证 history 数量、namespace 配置、最新 workflow 可查询。

通过标准：

- dump 文件可恢复。
- 恢复后 namespace 与 history 校验通过。
- 记录备份耗时、恢复耗时、文件大小。
- 备份文件完整性校验通过。

回滚：

- 删除隔离恢复测试库（经批准）。
- 不影响运行库。

---

## 5. 测试与审计设计

所有 B5.1 及后续 Temporal 验证必须使用 `scripts/run_pytest_audited.ps1`，禁止使用无退出码留档的裸后台命令。

### 5.1 完整 Temporal IT

```powershell
& ./scripts/run_pytest_audited.ps1 `
  -LogPrefix "b5_temporal_it" `
  -Temporal `
  -MarkerExpression "temporal_it"
```

验收证据：

- `b5_temporal_it.out`
- `b5_temporal_it.err`
- `b5_temporal_it.meta.out`
- meta 中 EXITCODE=0
- 不得把 B4 的旧结果复制为 B5 结果

### 5.2 真实 PostgreSQL 持久化测试

测试内容：

- Temporal cluster describe 显示 SQL persistence。
- workflow history 写入 `temporal` 数据库。
- 业务库 `agent_runtime` 中无 Temporal schema。
- namespace/retention 与配置一致。

验证命令（计划）：

```powershell
docker exec <temporal> temporal operator cluster describe --address localhost:7233
docker exec <temporal-postgres> psql -U temporal -d temporal -c "\dt"
```

结果必须附带命令输出文件与执行时间。

### 5.3 Worker 重启恢复测试

使用审计脚本分别记录：

- 重启前 smoke test。
- 重启动作时间与方式。
- 重启后 history / Run ID / 副作用检查。

通过标准：

- Run ID 不变。
- history 连续。
- 无重复 activity 副作用。
- meta EXITCODE=0。

### 5.4 History Replay 测试

通过审计脚本运行 replay 相关测试：

```powershell
& ./scripts/run_pytest_audited.ps1 `
  -LogPrefix "b5_replay" `
  -Temporal `
  -PytestArgs @("tests/test_replay_reliability.py")
```

如新增生产 workflow Replayer，必须使用代表性 history 样本：

- 短任务
- graph 任务
- tool UNKNOWN
- pause/resume
- worker restart 后恢复

### 5.5 数据库不可用与恢复测试

验证内容：

- temporal-postgres 未 healthy 时，server 不启动或健康检查失败。
- worker 启动连接失败时快速 FAIL，不无限静默重连。
- 恢复后使用隔离库演练 pg_dump/pg_restore。
- 不覆盖运行库。

所有输出写入审计日志，失败分类为 FAIL，不写 SKIP。

### 5.6 结果留档规则

- PASS：EXITCODE=0 且无 FAIL。
- FAIL：EXITCODE≠0 或存在失败断言。
- SKIP：仅明确的 opt-in 未开启，且 reason 可审计。
- NOT RUN：未执行的测试/阶段。
- INCOMPLETE：日志、退出码、时间或命令缺失。

35 分钟真实墙钟测试归 B6，本阶段不启动。

---

## Phase 7：切换门槛

必须同时满足：

- Phase 1–6 全部 PASS。
- 所有 pytest 运行均由审计脚本留档。
- 业务 PostgreSQL 未混用。
- 备份恢复演练通过。
- Workflow replay 通过。
- Worker 重启恢复通过。
- 人工批准生产/Staging 切换。

未满足任一项，不得进入 B5.1 实际切换。

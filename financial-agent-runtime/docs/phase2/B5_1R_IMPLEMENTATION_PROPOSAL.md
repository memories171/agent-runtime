# B5.1-R Dev SQL Persistence 实施提案

日期：2026-11-21
状态：设计提案，未实施。本阶段未创建 overlay、数据库、role、volume，未启动任何新服务。

---

## 1. 提案依据

本提案基于已验证事实：

- `temporalio/server:1.32.2` 可拉取，digest `sha256:7bf69188a6232fea14869b9cef3d94aa82a1601d99517f7e86c71f22da4e0670`。
- `temporalio/admin-tools:1.32.2` 可拉取，digest `sha256:4a95866efba29d7e17ab012df5232795e8b0ad0f7d5f9d1ec4df321f7792a754`。
- Admin Tools 中 `temporal-sql-tool version 1.32.2`。
- 二者镜像 label `com.temporal.server.version=1.32.2`，revision 同为 `6d085822273f4336796e6959c73ab1d14a23eb77`。
- 官方 compose 示例使用：
  - `temporalio/server`
  - `temporalio/admin-tools`
  - `postgres`
  - `DB=postgres12`
  - `POSTGRES_SEEDS`
  - `setup-postgres.sh`
  - `create-namespace.sh`

状态：**PASS（提案证据充分）**

---

## 2. 目标架构

```text
Docker network: agent-runtime default network

Business:
  postgres:16-alpine
  DB: agent_runtime
  role: agent / agent_app
  volume: postgres_data
  unchanged

Temporal SQL Dev overlay:
  temporal-postgres:postgres:16-alpine
    DBs: temporal, temporal_visibility
    role: temporal
    volume: temporal_pg_data
    no host port by default

  temporal-schema-init:temporalio/admin-tools:1.32.2
    command: setup-postgres.sh
    runs once to completion
    uses SQL_PASSWORD env
    depends_on temporal-postgres healthy

  temporal:temporalio/server:1.32.2
    command: /etc/temporal/entrypoint.sh -> temporal-server start
    env: DB=postgres12, POSTGRES_*, BIND_ON_IP=0.0.0.0
    depends_on temporal-schema-init completed_successfully
    ports: 7233/8233 unchanged

  temporal-namespace-init:temporalio/admin-tools:1.32.2
    command: create-namespace.sh
    depends_on temporal healthy
    ensures namespace default
```

Original start-dev service remains available when overlay is not used.

---

## 3. 镜像版本选择

| 组件 | 镜像 | tag | digest | 理由 |
|---|---|---|---|---|
| Temporal Server | `temporalio/server` | `1.32.2` | `sha256:7bf691...e0670` | 官方镜像；本地拉取成功；label version 1.32.2 |
| Admin Tools | `temporalio/admin-tools` | `1.32.2` | `sha256:4a9586...2a754` | 与 Server 同版本/revision；含 `temporal-sql-tool 1.32.2` |
| PostgreSQL | `postgres` | `16-alpine` | 本地 digest `sha256:1758b87430847848ff5a71cbdd898c7a21e469166c3e25244940f05ea97` | 项目已在用；PostgreSQL 16 满足 Temporal v12 plugin 要求 |
| UI | 当前 dev server 内置或项目既有配置 | 不在本提案切换 | - | 避免扩大范围 |

不使用：

- `temporalio/temporal:latest`
- `temporalio/auto-setup:*`
- 未验证第三方镜像

---

## 4. 电源与环境变量

建议所有可变值写入本地忽略文件 `.env`（`.gitignore:1` 已忽略）：

```env
TEMPORAL_SQL_USER=temporal
TEMPORAL_SQL_PASSWORD=<local-dev-secret>
TEMPORAL_SQL_DB=temporal
TEMPORAL_SQL_VISIBILITY_DB=temporal_visibility
POSTGRES_SQL_VERSION=16-alpine
TEMPORAL_VERSION=1.32.2
TEMPORAL_ADMINTOOLS_VERSION=1.32.2
```

禁止：

- 在 compose 中写真实密码。
- 复用业务数据库账号 `agent` / `agent_app`。
- 把 `TEMPORAL_SQL_DB` 设为 `agent_runtime`。

---

## 5. 服务职责

### 5.1 `temporal-postgres`

- 独立 PostgreSQL 实例。
- 独立 volume：`temporal_pg_data`。
- 独立 role：`temporal`。
- 数据库：`temporal`、`temporal_visibility`。
- 不映射宿主端口，除非调试时显式添加安全绑定。
- healthcheck：`pg_isready -U temporal -d temporal`。

### 5.2 `temporal-schema-init`

- 使用 `temporalio/admin-tools:1.32.2`。
- 运行官方风格 `setup-postgres.sh`。
- 只负责首次初始化：
  - `create`
  - `setup-schema -v 0.0`
  - `update-schema -d ...`
- 完成后退出 0。
- Server 依赖其 `service_completed_successfully`。

### 5.3 `temporal`

- 使用 `temporalio/server:1.32.2`。
- 使用生产 `temporal-server start`。
- 环境：
  - `DB=postgres12`
  - `POSTGRES_SEEDS=temporal-postgres`
  - `POSTGRES_USER=${TEMPORAL_SQL_USER}`
  - `POSTGRES_PWD=${TEMPORAL_SQL_PASSWORD}`
  - `SQL_PASSWORD=${TEMPORAL_SQL_PASSWORD}`
  - `BIND_ON_IP=0.0.0.0`
- 保留端口 7233 / 8233。

### 5.4 `temporal-namespace-init`

- 使用 admin-tools。
- 运行官方风格 `create-namespace.sh`。
- 幂等检查并创建 `default` namespace。
- 依赖 Temporal healthy。

---

## 6. 首次初始化与升级分离

### 首次初始化

只在全新 `temporal_pg_data` 上运行：

- 创建 `temporal` 与 `temporal_visibility` 数据库。
- `setup-schema -v 0.0`。
- `update-schema` 到 v1.19 / v1.14。
- 创建 namespace。

### 后续升级

不得执行 `create` 或 `setup-schema -v 0.0`。

只执行：

```sh
temporal-sql-tool ... --db temporal update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned -v 1.19
temporal-sql-tool ... --db temporal_visibility update-schema -d /etc/temporal/schema/postgresql/v12/visibility/versioned -v 1.14
```

升级前必须：

- 备份 temporal 与 temporal_visibility 数据库。
- 记录当前 schema version。
- 固定 Server / admin-tools 镜像版本。

---

## 7. 保留 start-dev

默认启动方式不变：

```powershell
docker compose up -d temporal api worker
```

SQL persistence 启动必须使用显式 overlay：

```powershell
docker compose -f docker-compose.yml -f docker-compose.temporal-sql.yml up -d
```

停止 SQL persistence：

```powershell
docker compose -f docker-compose.yml -f docker-compose.temporal-sql.yml down
```

回到默认 dev：

```powershell
docker compose up -d temporal api worker
```

验证：

```powershell
docker exec financial-agent-runtime-temporal-1 temporal operator cluster describe --address localhost:7233
```

预期：

- 默认模式：`PersistenceStore = sqlite`
- SQL overlay：`PersistenceStore = sql`

---

## 8. 验证设计

B5.1 实施时必须执行：

1. 服务启动：
   - PostgreSQL healthy。
   - schema init exit 0。
   - Temporal healthy。
   - namespace describe 成功。
2. History 持久化：
   - 执行 workflow。
   - CLI 查询 history。
   - SQL 查询独立 temporal DB 写入。
3. Server 重启：
   - 不删除 volume。
   - 重启 temporal。
   - 查询原 workflow history。
4. Worker 基础恢复：
   - 如能安全模拟则执行；否则 NOT RUN。
5. pytest：
   - 必须通过 `scripts/run_pytest_audited.ps1`。
   - 保存 out/err/meta。

本提案阶段均未执行。

---

## 9. 回滚

简版：

1. 停止 overlay。
2. 启动默认 compose。
3. 验证 sqlite dev server。
4. 不删除 `temporal_pg_data`。
5. 不删除 schema 数据。

详见 `B5_1R_RISK_AND_ROLLBACK.md`。

---

## 10. 是否具备实施条件

技术条件：**PASS**

- 官方 Server 镜像可获取。
- 官方 Admin Tools 镜像可获取。
- SQL 工具版本匹配。
- Schema 路径与版本确认。
- 官方 compose / setup 脚本确认。

流程条件：**BLOCKED**

- 需要人工批准 B5.1 实施。
- 需要人工确认 `.env` 密码管理方案。
- 需要人工确认是否采用两个 DB（`temporal` + `temporal_visibility`）于同一独立实例；提案建议采用该官方结构。

当前不得自动进入 B5.1 实施。

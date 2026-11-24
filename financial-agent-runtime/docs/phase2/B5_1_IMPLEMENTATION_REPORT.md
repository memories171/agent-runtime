# B5.1 实施报告：Dev Temporal SQL Persistence 最小实现

日期：2026-11-21
状态：**IMPLEMENTED / PASS**。Dev 环境已完成 Temporal SQL Persistence 最小实现，全部规定的验证项通过。按授权边界停止，未进入 B5.2。

---

## 1. 结论

已在本地 Dev 环境完成 Temporal SQL Persistence 最小实现：

- 新增独立 Temporal PostgreSQL（`temporal-postgres`），与业务 PostgreSQL（`postgres` / `agent_runtime`）完全隔离。
- 固定 Temporal Server 与 Admin Tools 镜像到 1.32.2 的精确 digest。
- 用独立 SQL overlay 覆盖 `temporal` 服务，未修改 `docker-compose.yml`。
- 通过 `temporal-sql-tool` 完成主库 `temporal` 与可见性库 `temporal_visibility` 的 schema 初始化（v1.19 / v1.14）。
- 保留原 `start-dev` 启动方式（不使用 overlay 时仍为 SQLite dev server）。
- 验证：Server 连 PostgreSQL、Workflow 执行、History 写入、Server 重启保留 History、Worker 重启恢复。
- 全部 pytest 均通过 `scripts/run_pytest_audited.ps1` 运行，审计元数据完整。

与目标架构的唯一偏差：默认 compose 的 `temporal` 服务在 overlay 中由 `temporalio/server:1.32.2` 覆盖，仍沿用端口 7233 / 8233；端口、namespace、task queue 对应用无变化。

---

## 2. 交付物（文件变更）

| 文件 | 类型 | 说明 |
|---|---|---|
| `docker-compose.temporal-sql.yml` | 新增 | SQL Persistence overlay；新增 `temporal-postgres`、`temporal-schema-init`、`temporal-namespace-init`，覆盖 `temporal`；声明 `temporal_pg_data`、`temporal_schema_flag` 两个卷 |
| `docker/temporal/scripts/setup-postgres.sh` | 新增 | 幂等 schema 初始化：首次执行 create + setup-schema + update-schema，后续仅 update-schema |
| `docker/temporal/scripts/create-namespace.sh` | 新增 | 幂等创建 `default` namespace（retention 24h） |
| `docker/temporal/dynamicconfig/development-sql.yaml` | 新增 | Dev 动态配置（仅保留已注册 key） |
| `.env` | 修改（gitignored 本地文件） | 追加 `TEMPORAL_SQL_*`、`POSTGRES_SQL_VERSION`、`TEMPORAL_VERSION`、`TEMPORAL_ADMINTOOLS_VERSION` |
| `docker-compose.yml` | **未修改** | 原 `start-dev` 与其他服务保持原样 |

未修改任何业务代码、API、Worker 逻辑、ACL/JWT、B1/B2/B3/B4 实现。

---

## 3. 固定镜像与 digest

| 组件 | 引用 | 实际 digest | 校验 |
|---|---|---|---|
| Temporal Server | `temporalio/server@sha256:7bf691...` | `sha256:7bf69188a6232fea14869b9cef3d94aa82a1601d99517f7e86c71f22da4e0670` | `docker image inspect` 本地确认 |
| Admin Tools | `temporalio/admin-tools@sha256:4a9586...` | `sha256:4a95866efba29d7e17ab012df5232795e8b0ad0f7d5f9d1ec4df321f7792a754` | `docker image inspect` 本地确认 |
| PostgreSQL | `postgres:16-alpine` | 项目既有镜像 | 与业务库同版本 |

- Server 镜像内 `temporal-server start` 使用内嵌 `config_template_embedded.yaml` + 环境变量，无需外部 config template。
- Admin Tools 内 `temporal-sql-tool 1.32.2`、`temporal` CLI、`nc` 均已确认存在。
- 平台：Docker Server 29.9.3，OS linux，ARCH amd64，WSL2 内核。仅验证本地 linux/amd64 manifest；未做多架构拉取验证。

---

## 4. 数据库与隔离

| 项 | 业务库 | Temporal 库 |
|---|---|---|
| 服务 | `postgres`（5432，宿主映射 127.0.0.1:5433） | `temporal-postgres`（仅容器网络 5432，无宿主映射） |
| 镜像 | `postgres:16-alpine` | `postgres:16-alpine` |
| role | `agent` / `agent_app` | `temporal` |
| 数据库 | `agent_runtime` | `temporal`、`temporal_visibility` |
| volume | `postgres_data` | `temporal_pg_data` |

实测隔离确认：

- `temporal-postgres` 数据库列表：`postgres`、`temporal`、`temporal_visibility`。
- 业务 `postgres` 数据库列表：`postgres`、`agent_runtime`。
- 未复用业务账号，未将 `TEMPORAL_SQL_DB` 指向 `agent_runtime`。

---

## 5. Schema 初始化

`setup-postgres.sh` 逻辑：

- 等待 PostgreSQL healthy。
- 以 `/flags/temporal_schema_initialized` 标记区分首次与已有 schema。
- 首次：`create` → `setup-schema -v 0.0` → `update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned -v 1.19`；可见性库同理到 `-v 1.14`。
- 后续：仅执行幂等 `update-schema`。
- 修复：schema-init 容器以 `user: "0:0"` 运行，解决命名卷 `/flags` 的 root 属主导致 flag 写入 `Permission denied`（第一次启动因此 exit 1，已修复）。

实测结果：

- 主库 `temporal` schema version `1.19`。
- 可见性库 `temporal_visibility` schema version `1.14`。
- schema-init 容器 `Exited (0)`，Server 满足 `service_completed_successfully` 后启动。
- namespace-init 幂等创建 `default`，retention `24h`，cluster `active`。

---

## 6. 验证结果摘要

| 验证项 | 状态 | 证据 |
|---|---|---|
| 服务启动 | PASS | temporal-postgres healthy、schema-init exit 0、temporal healthy、namespace describe 成功 |
| Server 连 PostgreSQL | PASS | `cluster describe`：`PersistenceStore=postgres12`、`VisibilityStore=postgres12` |
| 业务库隔离 | PASS | 两实例数据库列表互不包含对方的库 |
| Workflow 执行 + History 写入 | PASS | workflow list 中多条 `graph-it-*` / `pcp-*` / `subagent-*` 为 Completed；SQL `history_node` / `executions` / `executions_visibility` 有写入 |
| Server 重启保留 History | PASS | 重启前记录 RunId `01a1fd46-...`，重启后同一 RunId 的 `workflow show` 事件与原始时间戳 08:43:41 完整保留 |
| Worker 基础恢复 | PASS | `test_d_worker_crash_recovery`、`tc-restart-*` 等用例在 SQL 上通过；临时 worker 在 Server 重启后重新连接 7233 并轮询 |
| pytest 全量 | PASS | 219 passed（非 Temporal）+ 24 passed（Temporal IT），全部经审计脚本，EXITCODE=0 |

说明：应用自带 docker `worker` 容器因 `MCP_SERVERS_JSON` 指向的 `host.docker.internal:3001`（mock-external）已停止 44 小时，卡在 MCP discovery（非致命、非 SQL 引入），因此长期运行的 worker 不处理任务；这是既有测试基础设施条件。Temporal IT 使用进程内 worker，不受影响，全部通过。该问题记入 `B5_1_LEFTOVER_ISSUES.md`。

---

## 7. 启动与回滚

启动 SQL persistence（显式 overlay）：

```powershell
docker compose -f docker-compose.yml -f docker-compose.temporal-sql.yml up -d
```

回到默认 dev（start-dev / SQLite）：

```powershell
docker compose -f docker-compose.yml -f docker-compose.temporal-sql.yml down
docker compose up -d temporal api worker
```

详细回滚见 `B5_1_ROLLBACK_GUIDE.md`。

---

## 8. 边界遵守

- 未进入 B5.1 之外的实施阶段（未开始 B5.2 Staging / 双环境 / 备份演练）。
- 未启动 35 分钟墙钟测试（未授权）。
- 未修改测试断言、未删除测试、未临时 skip。
- 所有 pytest 都通过 `scripts/run_pytest_audited.ps1` 运行并保留审计日志。

完成后停止，等待人工验收。

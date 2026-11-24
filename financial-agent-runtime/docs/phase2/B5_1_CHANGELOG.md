# B5.1 变更清单

日期：2026-11-21
范围：Dev Temporal SQL Persistence 最小实现。

---

## 1. 新增文件

### 1.1 `docker-compose.temporal-sql.yml`

SQL Persistence overlay，通过 `-f` 显式启用，不修改 `docker-compose.yml`。

服务：

- `temporal-postgres`
  - `image: postgres:16-alpine`
  - role `TEMPORAL_SQL_USER`、password `TEMPORAL_SQL_PASSWORD`、默认 DB `TEMPORAL_SQL_DB=temporal`
  - volume `temporal_pg_data:/var/lib/postgresql/data`
  - healthcheck `pg_isready`
  - 无宿主端口映射
- `temporal-schema-init`
  - `image: temporalio/admin-tools@sha256:4a95866e...`
  - `user: "0:0"`（写 flag 卷所需）
  - `depends_on: temporal-postgres: service_healthy`
  - 挂载 `./docker/temporal/scripts:/scripts:ro` 与 `temporal_schema_flag:/flags`
  - `entrypoint: /bin/sh`，`command: /scripts/setup-postgres.sh`
- `temporal`（覆盖基础 compose 同名服务）
  - `image: temporalio/server@sha256:7bf69188...`
  - `command: ["/etc/temporal/entrypoint.sh"]`
  - `depends_on: temporal-schema-init: service_completed_successfully`
  - env：`DB=postgres12`、`DB_PORT=5432`、`POSTGRES_SEEDS=temporal-postgres`、`POSTGRES_USER`/`POSTGRES_PWD`/`SQL_PASSWORD`、`BIND_ON_IP=0.0.0.0`、`DYNAMIC_CONFIG_FILE_PATH=config/dynamicconfig/development-sql.yaml`
  - 挂载 `./docker/temporal/dynamicconfig:/etc/temporal/config/dynamicconfig:ro`
  - 端口 7233 / 8233 保持不变
  - healthcheck `nc -z localhost 7233`
- `temporal-namespace-init`
  - `image: temporalio/admin-tools@sha256:4a95866e...`
  - `depends_on: temporal: service_healthy`
  - `entrypoint: /bin/sh`，`command: /scripts/create-namespace.sh`
  - env：`TEMPORAL_ADDRESS=temporal:7233`、`DEFAULT_NAMESPACE=default`

声明卷：`temporal_pg_data`、`temporal_schema_flag`。

### 1.2 `docker/temporal/scripts/setup-postgres.sh`

- `set -eu`，校验 `POSTGRES_SEEDS` / `POSTGRES_USER` / `SQL_PASSWORD`。
- `sql_tool()` 封装：`temporal-sql-tool --plugin postgres12 --ep <seed> -u <user> --pw <password> -p <port>`。
- 等待 PostgreSQL 端口可用。
- 依据 `/flags/temporal_schema_initialized` 分为首次初始化与幂等升级。
- 首次初始化：
  - `temporal`：`create` → `setup-schema -v 0.0` → `update-schema -d .../temporal/versioned -v 1.19`
  - `temporal_visibility`：同上，`-d .../visibility/versioned -v 1.14`
- 末尾无条件对两库再执行一次 `update-schema`，保证版本到位。

### 1.3 `docker/temporal/scripts/create-namespace.sh`

- 等待 `temporal operator cluster health` 成功（最多 60 次 × 2s）。
- `namespace describe` 判断 `default` 是否存在；不存在则 `namespace create --retention 24h`。
- 最后打印 namespace describe 结果。

### 1.4 `docker/temporal/dynamicconfig/development-sql.yaml`

- `system.forceSearchAttributesCacheRefreshOnRead: true`
- 首版曾包含 `frontend.enableClientVersionCheck: false`，实测 1.32.2 报 `unregistered key`，已移除；重启后无 dynamic config warning。

---

## 2. 修改文件

### 2.1 `.env`（本地 gitignored 文件，追加）

```env
TEMPORAL_SQL_USER=temporal
TEMPORAL_SQL_PASSWORD=temporal_dev_2026
TEMPORAL_SQL_DB=temporal
TEMPORAL_SQL_VISIBILITY_DB=temporal_visibility
POSTGRES_SQL_VERSION=16-alpine
TEMPORAL_VERSION=1.32.2
TEMPORAL_ADMINTOOLS_VERSION=1.32.2
```

说明：这些是本地 Dev 值，非提交配置；密码写在 gitignored 文件，compose 内仅引用变量。

---

## 3. 未修改项

- `docker-compose.yml`：原样，默认 `temporalio/temporal:latest` + `server start-dev` 保持可用。
- 业务代码：`src/` 未改动。
- 测试代码：`tests/` 未改动；未改断言、未删除、未临时 skip。
- API / Worker 连接配置：`TEMPORAL_ADDRESS=temporal:7233`、namespace `default`、task queue `agent-runtime` 均未变。
- 业务 PostgreSQL：未改动。

---

## 4. 审计日志与临时文件

通过 `run_pytest_audited.ps1` 生成（保留在项目根）：

- `b51_temporal_smoke.{out,err,meta.out}`
- `b51_non_temporal_full.{out,err,meta.out}`
- `b51_temporal_rest.{out,err,meta.out}`

审计脚本本身未改动。调查期间创建的三个临时诊断脚本（`b51_diag_worker.py`、`b51_net_probe.py`、`b51_tq_probe.py`）已删除，不属于交付物。

---

## 5. 变更影响评估

- 启用 overlay 后，Temporal 从 SQLite dev server 切换到 PostgreSQL 生产 server，History/Visibility 持久化到独立卷。
- 不启用 overlay 时，行为与 B5.1 之前完全一致（原 `start-dev`）。
- 端口、namespace、task queue 不变，对 API/Worker/Client 透明。
- 需人工验收后决定是否进入 B5.2。

# B5.1-R 技术可行性总报告

日期：2026-11-21
范围：调查、验证、文档整理。未实施 Temporal SQL Persistence，未修改项目配置，未启动新服务。

---

## 1. 最终结论

技术路线 **可行**，但实施仍须人工授权。

B5.1 初次 BLOCKED 的根因是选错了镜像路线：

- 错误路线：`temporalio/temporal:latest`、`temporalio/auto-setup:1.32.2`
- 正确路线：`temporalio/server:1.32.2` + `temporalio/admin-tools:1.32.2`

本次已本地验证：

- Server 镜像可获取。
- Admin Tools 镜像可获取。
- `temporal-sql-tool` 版本与 Server 匹配。
- PostgreSQL schema 路径、版本和命令均已确认。
- 官方 compose 示例确认了服务拆分与启动顺序。

状态：**PASS（技术可行性） / BLOCKED（实施授权）**

---

## 2. 必须回答的问题

### Q1：Server 1.32.2 镜像能否获取？实际 digest 是什么？

答：能。

命令：

```powershell
docker pull --platform linux/amd64 temporalio/server:1.32.2
docker inspect temporalio/server:1.32.2 --format "{{index .RepoDigests 0}}"
```

输出摘要：

```text
Digest: sha256:7bf69188a6232fea14869b9cef3d94aa82a1601d99517f7e86c71f22da4e0670
REPODIGEST=temporalio/server@sha256:7bf69188a6232fea14869b9cef3d94aa82a1601d99517f7e86c71f22da4e0670
OS=linux ARCH=amd64
com.temporal.server.version=1.32.2
```

状态：**PASS**

注意：多架构 manifest 全量检查因 registry 连接失败为 INCOMPLETE；本地 linux/amd64 已 PASS。

---

### Q2：对应版本 Admin Tools 能否获取？

答：能。

命令：

```powershell
docker pull --platform linux/amd64 temporalio/admin-tools:1.32.2
docker inspect temporalio/admin-tools:1.32.2 --format "{{index .RepoDigests 0}}"
```

输出摘要：

```text
Digest: sha256:4a95866efba29d7e17ab012df5232795e8b0ad0f7d5f9d1ec4df321f7792a754
com.temporal.server.version=1.32.2
com.temporal.cli.version=1.10.0
```

状态：**PASS**

---

### Q3：`temporal-sql-tool` 是否与 Server 版本匹配？

答：匹配。

命令：

```powershell
docker run --rm --entrypoint temporal-sql-tool temporalio/admin-tools@sha256:4a95... --version
docker inspect temporalio/admin-tools:1.32.2 --format "{{json .Config.Labels}}"
```

输出摘要：

```text
temporal-sql-tool version 1.32.2
revision=6d085822273f4336796e6959c73ab1d14a23eb77
```

Server 镜像同样：

```text
com.temporal.server.version=1.32.2
revision=6d085822273f4336796e6959c73ab1d14a23eb77
```

GitHub tag：

```text
refs/tags/v1.32.2 -> 6d085822273f4336796e6959c73ab1d14a23eb77
```

状态：**PASS**

---

### Q4：两套 PostgreSQL Schema 文件在哪里？

答：在 admin-tools 镜像内。

命令：

```powershell
docker run --rm --entrypoint sh temporalio/admin-tools@sha256:4a95... -c 'find /etc/temporal/schema/postgresql/v12 -maxdepth 3 -type d | sort'
```

输出摘要：

```text
/etc/temporal/schema/postgresql/v12/temporal/versioned
/etc/temporal/schema/postgresql/v12/temporal/versioned/v1.19
/etc/temporal/schema/postgresql/v12/visibility/versioned
/etc/temporal/schema/postgresql/v12/visibility/versioned/v1.14
```

版本文件：

```text
const Version = "1.19"
const VisibilityVersion = "1.14"
```

源码对应：

```text
schema/postgresql/v12/temporal/versioned
schema/postgresql/v12/visibility/versioned
schema/postgresql/v12/version.go
```

GitHub ref：`v1.32.2`

状态：**PASS**

---

### Q5：首次初始化和升级命令是什么？

答：

首次初始化：

```sh
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} --pw "${SQL_PASSWORD}" -p ${DB_PORT:-5432} --db temporal create
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} --pw "${SQL_PASSWORD}" -p ${DB_PORT:-5432} --db temporal setup-schema -v 0.0
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} --pw "${SQL_PASSWORD}" -p ${DB_PORT:-5432} --db temporal update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned

temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} --pw "${SQL_PASSWORD}" -p ${DB_PORT:-5432} --db temporal_visibility create
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} --pw "${SQL_PASSWORD}" -p ${DB_PORT:-5432} --db temporal_visibility setup-schema -v 0.0
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} --pw "${SQL_PASSWORD}" -p ${DB_PORT:-5432} --db temporal_visibility update-schema -d /etc/temporal/schema/postgresql/v12/visibility/versioned
```

升级：

```sh
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} --pw "${SQL_PASSWORD}" -p ${DB_PORT:-5432} --db temporal update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned -v 1.19
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} --pw "${SQL_PASSWORD}" -p ${DB_PORT:-5432} --db temporal_visibility update-schema -d /etc/temporal/schema/postgresql/v12/visibility/versioned -v 1.14
```

证据：

- Temporal Docs PostgreSQL visibility 页面。
- `temporalio/temporal-server-operations/compose/scripts/setup-postgres.sh`。
- 本地 admin-tools `temporal-sql-tool --help`。

关键注意：

- PostgreSQL `update-schema` 使用 `--schema-dir/-d`，不要假设 `--schema-name postgresql/...` 可用。
- 本阶段未执行上述命令。

状态：**PASS（命令格式确认） / NOT RUN（未执行）**

---

### Q6：当前是否具备开始 B5.1 实施的全部条件？

答：**技术条件具备，流程条件不具备。**

技术条件 PASS：

- 官方镜像可拉取。
- 版本和 digest 确认。
- Schema 工具匹配。
- Schema 路径和版本确认。
- 官方部署结构确认。

流程条件 BLOCKED：

- 需要人工审核本报告。
- 需要 B5.1 实施单独授权。
- 需要人工确认 `.env` 密码管理方案。
- 需要人工确认是否采用官方双 DB 结构：`temporal` + `temporal_visibility` 于同一独立 PostgreSQL 实例。

因此当前不得自动进入 B5.1 实施。

---

### Q7：如果仍然 BLOCKED，具体缺少什么？

答：缺少的不是技术镜像，而是实施授权与配置决策：

1. 人工批准新增 `docker-compose.temporal-sql.yml`。
2. 人工批准 `.env` 中的本地密码注入方案。
3. 人工批准创建独立 `temporal_pg_data` volume。
4. 人工批准创建 `temporal` / `temporal_visibility` 数据库与 `temporal` role。
5. 人工批准执行 schema init。
6. 人工批准执行 workflow、history、server restart、worker recovery 验证。

---

## 3. 状态总表

| 调查项 | 状态 |
|---|---|
| Server 1.32.2 拉取 | PASS |
| Server digest / platform / labels | PASS |
| Server 生产入口 `temporal-server start` | PASS |
| Admin Tools 1.32.2 拉取 | PASS |
| Admin Tools digest / labels | PASS |
| `temporal-sql-tool` 版本 | PASS |
| 工具与 Server revision 对齐 | PASS |
| PostgreSQL schema 路径 | PASS |
| Schema 版本 1.19 / 1.14 | PASS |
| 首次初始化命令确认 | PASS |
| 升级命令确认 | PASS |
| 官方 compose 启动顺序确认 | PASS |
| 多架构 manifest 全量验证 | INCOMPLETE |
| 实际数据库/schema 操作 | NOT RUN |
| B5.1 实施 | BLOCKED，等待授权 |

---

## 4. 交付文件

- `docs/phase2/B5_1R_FEASIBILITY_REPORT.md`
- `docs/phase2/B5_1R_IMAGE_VERIFICATION.md`
- `docs/phase2/B5_1R_SCHEMA_COMPATIBILITY.md`
- `docs/phase2/B5_1R_IMPLEMENTATION_PROPOSAL.md`
- `docs/phase2/B5_1R_RISK_AND_ROLLBACK.md`

B5.1-R 到此停止。不得自动实施 B5.1，不得进入 B5.2。

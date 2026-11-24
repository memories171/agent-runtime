# B5.1-R Schema 兼容性与工具链报告

日期：2026-11-21
范围：只读调查。未创建数据库、未执行 schema setup/update、未修改项目配置。

---

## 1. 结论

| 项 | 结论 | 状态 |
|---|---|---|
| Server 正确技术路线 | `temporalio/server:1.32.2` | PASS |
| 正确 schema 工具 | `temporalio/admin-tools:1.32.2` 内 `temporal-sql-tool 1.32.2` | PASS |
| 工具与 Server 版本匹配 | 二者 label `com.temporal.server.version=1.32.2`，revision 同为 `6d085822...` | PASS |
| 主库 schema 路径 | `/etc/temporal/schema/postgresql/v12/temporal/versioned` | PASS |
| Visibility schema 路径 | `/etc/temporal/schema/postgresql/v12/visibility/versioned` | PASS |
| 主库 schema 版本 | `1.19` | PASS |
| Visibility schema 版本 | `1.14` | PASS |
| 首次初始化命令 | `create` → `setup-schema -v 0.0` → `update-schema -d ...` | PASS |
| 升级命令 | `update-schema -d ... [-v x.x]` | PASS |
| 实际执行 schema migration | 未执行 | NOT RUN |

---

## 2. 镜像内证据

命令：

```powershell
docker run --rm --entrypoint sh temporalio/admin-tools@sha256:4a95866efba29d7e17ab012df5232795e8b0ad0f7d5f9d1ec4df321f7792a754 -c 'temporal-sql-tool --version 2>&1 || true'
docker run --rm --entrypoint sh temporalio/admin-tools@sha256:4a95866efba29d7e17ab012df5232795e8b0ad0f7d5f9d1ec4df321f7792a754 -c 'find /etc/temporal/schema/postgresql/v12 -maxdepth 3 -type d | sort | tail -30'
docker run --rm --entrypoint sh temporalio/admin-tools@sha256:4a95866efba29d7e17ab012df5232795e8b0ad0f7d5f9d1ec4df321f7792a754 -c 'cat /etc/temporal/schema/postgresql/v12/version.go'
```

输出摘要：

```text
temporal-sql-tool version 1.32.2

/etc/temporal/schema/postgresql/v12/temporal/versioned/v1.19
/etc/temporal/schema/postgresql/v12/visibility/versioned/v1.14

const Version = "1.19"
const VisibilityVersion = "1.14"
```

状态：**PASS**

---

## 3. 源码 tag 证据

GitHub API：

```text
GET https://api.github.com/repos/temporalio/temporal/git/ref/tags/v1.32.2
```

输出摘要：

```text
refs/tags/v1.32.2 -> 6d085822273f4336796e6959c73ab1d14a23eb77
object.type = commit
```

GitHub API schema 文件：

```text
GET https://api.github.com/repos/temporalio/temporal/contents/schema/postgresql/v12/version.go?ref=v1.32.2
```

输出摘要：

```text
Version = "1.19"
VisibilityVersion = "1.14"
file_sha = 77e0f761e09fab273b3a390e07708e1f9e47caf9
```

GitHub 目录：

```text
https://github.com/temporalio/temporal/tree/v1.32.2/schema/postgresql/v12/temporal/versioned
https://github.com/temporalio/temporal/tree/v1.32.2/schema/postgresql/v12/visibility/versioned
```

目录列表显示：

- temporal 最高版本目录：`v1.19`
- visibility 最高版本目录：`v1.14`

状态：**PASS**

---

## 4. 官方命令证据

来源：

- Temporal Docs: `https://docs.temporal.io/self-hosted-guide/visibility/postgresql`
- `temporalio/temporal-server-operations` compose:
  - `compose/docker-compose-postgres.yml`
  - `compose/scripts/setup-postgres.sh`
  - `compose/scripts/create-namespace.sh`

官方 `setup-postgres.sh` 摘要：

```sh
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} -p ${DB_PORT:-5432} --db temporal create
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} -p ${DB_PORT:-5432} --db temporal setup-schema -v 0.0
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} -p ${DB_PORT:-5432} --db temporal update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned

temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} -p ${DB_PORT:-5432} --db temporal_visibility create
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} -p ${DB_PORT:-5432} --db temporal_visibility setup-schema -v 0.0
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} -p ${DB_PORT:-5432} --db temporal_visibility update-schema -d /etc/temporal/schema/postgresql/v12/visibility/versioned
```

状态：**PASS**

说明：官方脚本未展示密码参数；`temporal-sql-tool --help` 明确支持 `--password/--pw` 和 `SQL_PASSWORD` 环境变量。实际实施时应通过环境变量注入密码，不写入 compose 明文。

---

## 5. CLI 参数确认

命令：

```powershell
docker run --rm --entrypoint temporal-sql-tool temporalio/admin-tools@sha256:4a95... update-schema --help
docker run --rm --entrypoint temporal-sql-tool temporalio/admin-tools@sha256:4a95... setup-schema --help
```

输出摘要：

```text
update-schema options:
  --version value, -v value
  --schema-dir value, -d value
  --schema-name value, -s value  one of: [mysql/v8/temporal mysql/v8/visibility]

setup-schema options:
  --version value, -v value
  --schema-file value, -f value
  --schema-name value, -s value  one of: [mysql/v8/temporal mysql/v8/visibility postgresql/v12/temporal postgresql/v12/visibility]
```

重要结论：

- `setup-schema` 的 `--schema-name` 支持 PostgreSQL。
- `update-schema` 的 `--schema-name` 输出未列出 PostgreSQL schema，因此 PostgreSQL 升级应使用官方脚本中的 `--schema-dir/-d /etc/temporal/schema/postgresql/v12/.../versioned` 方式。
- 不得猜测 `update-schema --schema-name postgresql/v12/...` 可用。

状态：**PASS**

---

## 6. 首次初始化与升级差异

### 首次初始化

前置条件：

- 独立 Temporal PostgreSQL 已启动且 healthy。
- 目标仅是新建的空 DB。
- 数据库/role 与业务库隔离。

命令：

```sh
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} --pw "${SQL_PASSWORD}" -p ${DB_PORT:-5432} --db temporal create
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} --pw "${SQL_PASSWORD}" -p ${DB_PORT:-5432} --db temporal setup-schema -v 0.0
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} --pw "${SQL_PASSWORD}" -p ${DB_PORT:-5432} --db temporal update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned

temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} --pw "${SQL_PASSWORD}" -p ${DB_PORT:-5432} --db temporal_visibility create
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} --pw "${SQL_PASSWORD}" -p ${DB_PORT:-5432} --db temporal_visibility setup-schema -v 0.0
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} --pw "${SQL_PASSWORD}" -p ${DB_PORT:-5432} --db temporal_visibility update-schema -d /etc/temporal/schema/postgresql/v12/visibility/versioned
```

### 后续升级

不执行 `create`，不执行 `setup-schema -v 0.0`。

仅执行：

```sh
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} --pw "${SQL_PASSWORD}" -p ${DB_PORT:-5432} --db temporal update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned -v 1.19

temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} --pw "${SQL_PASSWORD}" -p ${DB_PORT:-5432} --db temporal_visibility update-schema -d /etc/temporal/schema/postgresql/v12/visibility/versioned -v 1.14
```

如不指定 `-v`，工具默认升级到最新；实施时建议显式指定目标版本以便审计。

状态：**PASS（命令格式已核实）**

注意：本阶段未执行上述命令，状态不是“实施成功”。

---

## 7. 验证状态

| 验证项 | 状态 |
|---|---|
| 官方镜像内的 schema 路径存在 | PASS |
| schema 版本常量读取 | PASS |
| 源码 tag 与镜像 revision 一致 | PASS |
| 官方首次初始化命令确认 | PASS |
| 官方升级命令确认 | PASS |
| 实际 schema migration | NOT RUN |

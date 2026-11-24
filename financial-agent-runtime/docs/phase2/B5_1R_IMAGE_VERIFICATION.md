# B5.1-R 镜像验证报告

日期：2026-11-21
范围：只读调查与镜像拉取验证。未启动新服务，未创建数据库/volume，未修改项目配置。

---

## 1. 结论

| 镜像 | 目标 tag | 本地拉取 | digest | 平台 | 状态 |
|---|---|---|---|---|---|
| `temporalio/server` | `1.32.2` | 成功 | `sha256:7bf69188a6232fea14869b9cef3d94aa82a1601d99517f7e86c71f22da4e0670` | `linux/amd64` | **PASS** |
| `temporalio/admin-tools` | `1.32.2` | 成功 | `sha256:4a95866efba29d7e17ab012df5232795e8b0ad0f7d5f9d1ec4df321f7792a754` | `linux/amd64` | **PASS** |

之前的 B5.1 失败点（`temporalio/temporal:1.32.2`、`temporalio/auto-setup:1.32.2` 无法获取）不再适用于本次技术路线。官方推荐路线是：

- Server：`temporalio/server:1.32.2`
- Schema 工具：`temporalio/admin-tools:1.32.2`

---

## 2. Server 镜像拉取

命令：

```powershell
docker pull --platform linux/amd64 temporalio/server:1.32.2
```

输出摘要：

```text
Digest: sha256:7bf69188a6232fea14869b9cef3d94aa82a1601d99517f7e86c71f22da4e0670
Status: Image is up to date for temporalio/server:1.32.2
docker.io/temporalio/server:1.32.2
```

状态：**PASS**

说明：之前后台pull可能已经完成，本次 pull 返回 “Image is up to date”，仍证明本地已存在该固定 tag 镜像。

---

## 3. Server 镜像元数据

命令：

```powershell
docker inspect temporalio/server:1.32.2 --format "REPODIGEST={{index .RepoDigests 0}}"
docker inspect temporalio/server:1.32.2 --format "ID={{.Id}} OS={{.Os}} ARCH={{.Architecture}} CREATED={{.Created}} USER={{.Config.User}} WORKINGDIR={{.Config.WorkingDir}}"
docker inspect temporalio/server:1.32.2 --format "{{json .Config}}"
```

输出摘要：

```text
REPODIGEST=temporalio/server@sha256:7bf69188a6232fea14869b9cef3d94aa82a1601d99517f7e86c71f22da4e0670
ID=sha256:7bf69188a6232fea14869b9cef3d94aa82a1601d99517f7e86c71f22da4e0670
OS=linux ARCH=amd64
CREATED=2026-10-31T04:43:00.078288219Z
USER=temporal WORKINGDIR=/etc/temporal
```

Labels：

```json
{
  "com.temporal.server.version": "1.32.2",
  "org.opencontainers.image.revision": "6d085822273f4336796e6959c73ab1d14a23eb77",
  "org.opencontainers.image.source": "https://github.com/temporalio/temporal",
  "org.opencontainers.image.version": "1.32.2"
}
```

状态：**PASS**

---

## 4. Server 镜像内容

命令：

```powershell
docker run --rm --entrypoint sh temporalio/server@sha256:7bf... -c 'command -v temporal-server; command -v temporal-sql-tool; command -v tctl; command -v temporal'
docker run --rm --entrypoint temporal-server temporalio/server@sha256:7bf... start --help
docker run --rm --entrypoint sh temporalio/server@sha256:7bf... -c 'find /etc/temporal -maxdepth 2 -type f | sort | head -40'
docker run --rm --entrypoint sh temporalio/server@sha256:7bf... -c 'sed -n "1,120p" /etc/temporal/entrypoint.sh'
```

输出摘要：

```text
/usr/local/bin/temporal-server
```

`temporal-sql-tool`、`tctl`、`temporal` 未在该镜像中返回路径。

`/etc/temporal` 仅发现：

```text
/etc/temporal/entrypoint.sh
```

entrypoint 摘要：

```sh
: "${BIND_ON_IP:=$(getent hosts "$(hostname)" | awk '{print $1;}')}"
export BIND_ON_IP
...
exec temporal-server start
```

`temporal-server start --help`：

```text
--service value, --svc value [ ... ]  service(s) to start
[$TEMPORAL_SERVICES]
```

状态：**PASS**

结论：

- Server 镜像包含生产 `temporal-server`。
- 不包含 schema 工具，符合“Server 与 Schema 工具分离”的要求。
- Schema 工具应由 admin-tools 提供。

---

## 5. Admin Tools 镜像拉取

命令：

```powershell
docker pull --platform linux/amd64 temporalio/admin-tools:1.32.2
```

输出摘要：

```text
Digest: sha256:4a95866efba29d7e17ab012df5232795e8b0ad0f7d5f9d1ec4df321f7792a754
Status: Downloaded newer image for temporalio/admin-tools:1.32.2
docker.io/temporalio/admin-tools:1.32.2
```

状态：**PASS**

---

## 6. Admin Tools 镜像元数据

命令：

```powershell
docker inspect temporalio/admin-tools:1.32.2 --format "REPODIGEST={{index .RepoDigests 0}}"
docker inspect temporalio/admin-tools:1.32.2 --format "ID={{.Id}} OS={{.Os}} ARCH={{.Architecture}} CREATED={{.Created}} USER={{.Config.User}} WORKINGDIR={{.Config.WorkingDir}}"
docker inspect temporalio/admin-tools:1.32.2 --format "{{json .Config.Labels}}"
```

输出摘要：

```text
REPODIGEST=temporalio/admin-tools@sha256:4a95866efba29d7e17ab012df5232795e8b0ad0f7d5f9d1ec4df321f7792a754
ID=sha256:4a95866efba29d7e17ab012df5232795e8b0ad0f7d5f9d1ec4df321f7792a754
OS=linux ARCH=amd64
CREATED=2026-11-03T17:25:34.944058302Z
USER=temporal WORKINGDIR=/
```

Labels：

```json
{
  "com.temporal.cli.version": "1.10.0",
  "com.temporal.server.version": "1.32.2",
  "org.opencontainers.image.revision": "6d085822273f4336796e6959c73ab1d14a23eb77",
  "org.opencontainers.image.source": "https://github.com/temporalio/temporal"
}
```

状态：**PASS**

---

## 7. temporal-sql-tool 版本验证

命令：

```powershell
docker run --rm --entrypoint sh temporalio/admin-tools@sha256:4a95... -c 'command -v temporal-sql-tool; temporal-sql-tool --version 2>&1 || true'
docker run --rm --entrypoint temporal-sql-tool temporalio/admin-tools@sha256:4a95... --help
```

输出摘要：

```text
/usr/local/bin/temporal-sql-tool
temporal-sql-tool version 1.32.2
```

`--help` 显示命令：

```text
setup-schema
update-schema
create-database
drop-database
```

全局参数包含：

```text
--endpoint, --ep
--port, -p
--user, -u
--password, --pw
--database, --db
--plugin, --pl
```

状态：**PASS**

---

## 8. 版本交叉验证

GitHub tag：

命令：

```text
GET https://api.github.com/repos/temporalio/temporal/git/ref/tags/v1.32.2
```

输出摘要：

```text
refs/tags/v1.32.2 -> 6d085822273f4336796e6959c73ab1d14a23eb77
object.type = commit
```

与镜像 label `org.opencontainers.image.revision` 一致。

Server label：

```text
com.temporal.server.version=1.32.2
```

Admin Tools label：

```text
com.temporal.server.version=1.32.2
temporal-sql-tool version 1.32.2
```

状态：**PASS**

结论：`temporal-sql-tool` 与 Server 1.32.2 版本匹配。

---

## 9. 未验证项

| 项 | 状态 | 原因 |
|---|---|---|
| 多架构 manifest 全量验证 | INCOMPLETE | `docker buildx imagetools inspect` 访问 registry 失败；本地仅验证 linux/amd64 |
| 实际启动 server | NOT RUN | 本阶段禁止启动持久化环境 |
| 实际连接 PostgreSQL | NOT RUN | 本阶段禁止启动数据库 |
| schema migration 执行 | NOT RUN | 本阶段禁止执行 |

---

## 10. 最终回答

- Server 1.32.2 镜像能否获取？**能，已本地拉取成功。**
- 实际 digest？`sha256:7bf69188a6232fea14869b9cef3d94aa82a1601d99517f7e86c71f22da4e0670`
- 对应 Admin Tools 能否获取？**能，已本地拉取成功。**
- `temporal-sql-tool` 是否与 Server 版本匹配？**是，版本 1.32.2，revision 与 Server 镜像一致。**

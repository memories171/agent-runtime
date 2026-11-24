# B5.1 回滚指南

日期：2026-11-21
适用范围：Dev 环境 Temporal SQL Persistence overlay。回滚目标是恢复到 B5.1 之前的默认 `start-dev`（SQLite）状态。

---

## 1. 回滚原则

- overlay 是通过 `-f docker-compose.temporal-sql.yml` 显式启用的，默认 `docker-compose.yml` 未修改。
- 因此**最快且最安全的回滚是停止使用 overlay**，不做任何数据删除。
- 不删除 `temporal_pg_data` / `temporal_schema_flag` 卷，不删除 schema 数据，以便需要时重新启用 SQL persistence 并保留 History。
- 不回滚业务代码、API、Worker（B5.1 未修改它们）。

---

## 2. 快速回滚：回到默认 start-dev

当前处于 SQL overlay 状态。执行：

```powershell
cd "f:/AAA.个人项目/Agent runtime/financial-agent-runtime"

# 停止 overlay 管理的全部服务（含 temporal-postgres / schema-init / namespace-init）
docker compose -f docker-compose.yml -f docker-compose.temporal-sql.yml down

# 用原始 compose 启动默认 dev（temporalio/temporal:latest + server start-dev）
docker compose up -d temporal api worker
```

验证：

```powershell
docker exec financial-agent-runtime-temporal-1 temporal operator cluster describe --address localhost:7233
```

预期：

```text
ClusterName  PersistenceStore  VisibilityStore
active       sqlite            sqlite
```

说明：`down` 不带 `-v`，卷会被保留（数据不丢）。若带上 `-v` 会删除卷，仅在确认不再需要 SQL History 时使用。

---

## 3. 部分回滚：仅切换 Temporal 服务

若只想临时把 `temporal` 换回 dev server 而保留 `temporal-postgres` 容器：

```powershell
# 停止并移除 overlay 的 temporal（SQL server）
docker compose -f docker-compose.yml -f docker-compose.temporal-sql.yml stop temporal
docker rm -f financial-agent-runtime-temporal-1

# 启动原 dev server
docker compose up -d temporal
docker compose up -d api worker
```

注意：这种方式下 `temporal-postgres` 仍在运行但无 Server 连接；不如第 2 节干净，通常不建议。

---

## 4. 重新启用 SQL Persistence

需要恢复 SQL persistence 时（卷仍在、History 保留）：

```powershell
docker compose -f docker-compose.yml -f docker-compose.temporal-sql.yml up -d
```

- schema-init 检测到 `/flags/temporal_schema_initialized` 存在，只执行幂等 `update-schema`。
- Server 重启后从 `temporal_pg_data` 读取原有 History。
- namespace-init 检测到 `default` 已存在则跳过创建。

验证：

```powershell
docker run --rm --network financial-agent-runtime_default `
  --entrypoint temporal `
  temporalio/admin-tools@sha256:4a95866efba29d7e17ab012df5232795e8b0ad0f7d5f9d1ec4df321f7792a754 `
  operator cluster describe --address temporal:7233
```

预期 `PersistenceStore=postgres12`、`VisibilityStore=postgres12`。

---

## 5. 数据处置（谨慎）

- **保留**：`temporal_pg_data`、`temporal_schema_flag`（默认行为，强烈建议保留）。
- **仅当确认丢弃所有 SQL History 时**，才可删除卷：
  ```powershell
  docker compose -f docker-compose.yml -f docker-compose.temporal-sql.yml down -v
  ```
  删除后再次 `up` 会触发首次 schema 初始化与 namespace 重建。
- 业务库卷 `postgres_data` 与 B5.1 无关，**任何情况下不要删除**。

---

## 6. 故障回滚场景

| 场景 | 处理 |
|---|---|
| schema-init 首次 exit 1（flag 写入 Permission denied） | 已通过 `user: "0:0"` 修复；如复现，检查 `temporal_schema_flag` 卷属主，或 `docker volume rm financial-agent-runtime_temporal_schema_flag` 后重建（会触发重新初始化，仅在空库时安全） |
| Server 启动后 `Not enough hosts to serve the request` | 单节点 membership 启动瞬态错误；等待 ~30–60s 健康后自动消失；持续不消则重启 temporal 容器 |
| dynamic config `unregistered key` 告警 | 已移除无效 key；如复现，检查 `development-sql.yaml` 仅含 1.32.2 已注册 key |
| workflow 不被处理、worker 卡住 | 检查 `host.docker.internal:3001`（mock-external）是否停止；这是 MCP discovery 既有条件，与 SQL persistence 无关；Temporal IT 使用进程内 worker 不受影响 |
| 需完全恢复 B5.1 前状态 | 执行第 2 节快速回滚；卷保留；不删除配置overlay文件（仅为不被 compose 使用） |

---

## 7. 回滚验证清单

- [ ] `docker compose down`（overlay）执行成功
- [ ] `docker compose up -d temporal api worker`（默认）执行成功
- [ ] `cluster describe` 显示 `sqlite` / `sqlite`
- [ ] API `/health` 返回 200
- [ ] 卷 `temporal_pg_data` 仍存在（数据未删）
- [ ] 业务库 `agent_runtime` 未受影响

完成后按人工指示决定是否进入 B5.2。

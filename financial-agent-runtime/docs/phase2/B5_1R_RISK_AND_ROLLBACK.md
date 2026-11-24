# B5.1-R 风险与回滚设计

日期：2026-11-21
范围：只读调查与设计。未实施、未启动服务、未创建数据库。

---

## 1. 结论

技术风险较 B5.1 初次尝试显著下降：

| 原阻塞项 | 当前结论 | 状态 |
|---|---|---|
| `temporalio/server:1.32.2` 是否可获取 | 已拉取，digest 已记录 | PASS |
| `temporalio/admin-tools:1.32.2` 是否可获取 | 已拉取，digest 已记录 | PASS |
| `temporal-sql-tool` 是否匹配 Server | 版本 1.32.2，revision 相同 | PASS |
| PostgreSQL schema 路径 | 官方镜像内已确认 | PASS |
| 首次初始化 / 升级命令 | 官方文档和脚本已确认 | PASS |
| 是否可自动实施 | 不可以，需人工授权 | BLOCKED |

---

## 2. 主要风险

### R1：误用业务数据库

- 影响：业务数据污染，不可逆。
- 证据：业务库仍为 `agent_runtime`，角色 `agent` / `agent_app`。
- 缓解：
  - overlay 中独立 `temporal-postgres`。
  - 独立 role `temporal`。
  - 独立 DB `temporal` / `temporal_visibility`。
  - 独立 volume `temporal_pg_data`。
- 状态：提案缓解，实施时验证。

### R2：schema init 与 upgrade 混用

- 影响：重复 `setup-schema -v 0.0` 可能破坏已有版本链。
- 缓解：
  - 首次 init 与升级使用不同脚本/不同 compose profile。
  - 升级禁止 `create`、`setup-schema -v 0.0`。
  - 升级显式 `-v 1.19` / `-v 1.14`。
- 状态：设计完成，未执行。

### R3：镜像版本漂移

- 影响：history 兼容性、schema 兼容性不确定。
- 缓解：
  - 使用 digest 引用或固定 tag + digest 审计。
  - Server 与 admin-tools 同 tag，同 revision。
- 状态：PASS（已记录 digest）。

### R4：`update-schema --schema-name` 参数误用

- 影响：命令失败或误以为 PostgreSQL 内嵌 schema 可直接使用。
- 证据：`update-schema --help` 的 `--schema-name` 仅列出 MySQL 两项。
- 缓解：PostgreSQL 升级使用 `--schema-dir/-d /etc/temporal/schema/postgresql/v12/.../versioned`。
- 状态：PASS。

### R5：默认 dev 环境被破坏

- 影响：B0–B5.0 的既有测试入口失效。
- 缓解：
  - 不修改 `docker-compose.yml`。
  - 仅新增 overlay。
  - 默认命令仍为 `docker compose up -d temporal api worker`。
- 状态：设计完成，未实施。

### R6：namespace/retention 不一致

- 影响：workflow 可见性与 history 保留异常。
- 缓解：
  - 使用 `create-namespace.sh` 幂等创建 `default`。
  - 实施后执行 `temporal operator namespace describe default`。
- 状态：设计完成，未实施。

### R7：双 Worker 副作用

- 影响：相同 workflow 被两个集群/worker 执行，产生重复业务副作用。
- 缓解：
  - Dev 切换时只允许一个 active worker。
  - 不并行运行 start-dev worker 与 SQL worker。
- 状态：设计完成，未实施。

### R8：备份缺失

- 影响：schema upgrade 失败后无法回滚。
- 缓解：
  - 升级前备份 `temporal` 与 `temporal_visibility`。
  - 记录 dump 文件、checksum、时间、镜像版本。
- 状态：设计完成，未执行。

---

## 3. 回滚路径

### 3.1 回滚到默认 start-dev

适用条件：

- SQL overlay 启动失败。
- schema init 失败。
- Temporal server 不健康。
- Worker 连接异常。

步骤：

```powershell
docker compose -f docker-compose.yml -f docker-compose.temporal-sql.yml down
docker compose up -d temporal api worker
docker exec financial-agent-runtime-temporal-1 temporal operator cluster describe --address localhost:7233
```

预期：

- `PersistenceStore = sqlite`
- `VisibilityStore = sqlite`
- namespace `default` 可用

禁止：

- 不加 `-v`，不删除 volume。
- 不删除 `temporal_pg_data`。
- 不删除业务 `postgres_data`。

状态：设计完成；当前不需要回滚，因为未实施。

---

### 3.2 schema init 失败

步骤：

1. 保存 `temporal-schema-init` 完整日志。
2. 停止 overlay。
3. 不自动 drop DB。
4. 由人工确认是：
   - 等待 DB 未 ready；
   - 密码/用户错误；
   - schema tool 版本错误；
   - 数据库非空导致冲突。
5. 只有确认 `temporal_pg_data` 是新建且无有效数据后，人工决定是否重建。

---

### 3.3 schema upgrade 失败

步骤：

1. 停止 Worker，避免继续写入。
2. 保留 server 与 schema tool 日志。
3. 使用升级前备份恢复到隔离库验证。
4. 不得直接在运行库上手工改表。
5. 若必须恢复运行库，需要人工审批和停机窗口。

---

### 3.4 Worker 副作用异常

步骤：

1. 停止所有指向 SQL 集群的 Worker。
2. 查询 Temporal history。
3. 对比业务 `run_events` / `tool_invocations`。
4. 对重复副作用做人工对账。
5. 不修改 Temporal 内部表。

---

## 4. 审计要求

B5.1 实施时必须保存：

- 每个 docker compose 命令输出。
- `docker inspect` digest。
- `temporal operator cluster describe`。
- `temporal operator namespace describe default`。
- schema init 与 upgrade 日志。
- pytest 审计文件：
  - `.out`
  - `.err`
  - `.meta.out`
- 资源使用：
  - `docker stats --no-stream`
  - `pg_database_size`
  - workflow history 存储增量

结果分类：

- PASS：命令 exit 0 且证据完整。
- FAIL：命令非零或验证失败。
- BLOCKED：外部依赖不可获取。
- INCOMPLETE：日志/退出码/证据缺一项。
- NOT RUN：未执行。

---

## 5. 当前风险结论

当前技术路线可执行，但本阶段只调查。实施前仍需人工批准：

- 新增 overlay 文件。
- 使用 `.env` 管理本地密码。
- 创建 `temporal_pg_data`。
- 创建 `temporal` / `temporal_visibility` 数据库。
- 运行 schema init。
- 执行 workflow 与重启验证。

在完成批准前，状态保持 BLOCKED，不进入 B5.1 实施。

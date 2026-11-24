# B5.0 Temporal SQL Persistence 现状架构审查

日期：2026-11-21
范围：只读审查与方案设计。未修改 Compose、部署配置、数据库 Schema，未启动新数据库，未进入 B5.1。

---

## 1. 结论摘要

当前 `financial-agent-runtime` 使用 Temporal **开发服务器**：

- Compose 固定写法为 `temporalio/temporal:latest` + `server start-dev --ip 0.0.0.0`。
- 当前运行容器实测 CLI 为 `temporal version 1.10.1 (Server 1.32.2, UI 2.56.4)`，但镜像 tag 仍为 `latest`，不可复现。
- 当前集群描述明确：
  - `PersistenceStore = sqlite`
  - `VisibilityStore = sqlite`
- Temporal 没有数据卷、没有环境变量、没有 `depends_on`；容器重建后 history 丢失。
- 业务 PostgreSQL 与 Temporal 当前是隔离的：业务库是独立 `postgres:16-alpine`，Temporal dev server 不使用该 PostgreSQL。
- 但这种隔离不是 SQL Persistence 隔离，而是“Temporal 完全未持久化”。
- 应用侧 workflow id 稳定、execution timeout 已配置、outbox 幂等可用；但 client 无连接超时/RPC retry/TLS，workflow 无版本化保护，生产 workflow 无自动 Replayer 证据。
- 仓库没有 Temporal SQL schema 初始化、auto-setup、备份、恢复或演练脚本。

**定性：当前架构适合本地开发，不具备 Temporal history 持久化、灾备和生产切换条件。**

---

## 2. 当前架构图

```text
                         Host ports
                 7233 (gRPC) / 8233 (UI)
                         |
                         v
              +--------------------------+
              | temporal: latest         |
              | command: server start-dev|
              | persistence: sqlite      |
              | visibility: sqlite       |
              | no volume / no env       |
              +--------------------------+
                         ^
                         | TEMPORAL_ADDRESS=temporal:7233
                         |
        +----------------+-----------------+
        |                                  |
+-------+--------+                  +------+-------+
| api service    |                  | worker        |
| alembic upgrade|                  | Worker.run()  |
| uvicorn :8000  |                  | outbox loop   |
+----------------+                  +------+-------+
        |                                  |
        | DATABASE_URL                     | DATABASE_URL
        | DATABASE_MIGRATION_URL           | DATABASE_MIGRATION_URL
        v                                  v
+------------------------------------------------+
| postgres:16-alpine (business DB)               |
| POSTGRES_DB=agent_runtime                     |
| volume: postgres_data                          |
| init: ./docker/init                           |
| healthcheck: pg_isready                       |
+------------------------------------------------+

Additional volumes:
- snapshot_data: checkpoint local snapshots
- fi_data: fault-injection overlay only
```

说明：

- 业务 PostgreSQL 与 Temporal dev server 当前没有网络连接用于 persistence。
- Temporal UI/API 与 Worker 共享同一 dev server。

---

## 3. 配置清单

### 3.1 Temporal Server

| 项 | 当前值 | 证据 |
|---|---|---|
| 镜像 | `temporalio/temporal:latest` | `docker-compose.yml:30` |
| 实测版本 | CLI 1.10.1 / Server 1.32.2 / UI 2.56.4 | `docker exec ... temporal --version` |
| 启动命令 | `server start-dev --ip 0.0.0.0` | `docker-compose.yml:31` |
| PersistenceStore | `sqlite` | `temporal operator cluster describe` |
| VisibilityStore | `sqlite` | `temporal operator cluster describe` |
| 端口 | 7233、8233 | `docker-compose.yml:32-34` |
| 健康检查 | `temporal operator cluster health` | `docker-compose.yml:35-39` |
| environment | 无 | `docker inspect .Config.Env` 仅 PATH |
| volumes | 无 | `docker inspect .Mounts` 为空 |
| depends_on | 无 | `docker-compose.yml:29-39` |
| Namespace | `default` | `temporal operator namespace describe default` |
| Workflow retention | 24h | 同上 |
| Archival | history/visibility 均 Disabled | 同上 |

### 3.2 业务 PostgreSQL

| 项 | 当前值 | 证据 |
|---|---|---|
| 镜像 | `postgres:16-alpine` | `docker-compose.yml:14` |
| 用户/库 | `agent` / `agent_runtime` | `docker-compose.yml:15-18` |
| 应用账号 | `agent_app`（`DATABASE_URL`） | `docker-compose.yml:46,75` |
| 端口 | `127.0.0.1:5433:5432` | `docker-compose.yml:19` |
| 数据卷 | `postgres_data` | `docker-compose.yml:20-21` |
| 初始化脚本 | `./docker/init` | `docker-compose.yml:22` |
| 健康检查 | `pg_isready -U agent -d agent_runtime` | `docker-compose.yml:23-27` |

### 3.3 API / Worker

| 项 | 当前值 | 证据 |
|---|---|---|
| API 启动 | `alembic upgrade head && uvicorn agent_runtime.api:app` | `docker-compose.yml:43` |
| Worker 启动 | `python -m agent_runtime.worker` | `docker-compose.yml:72` |
| TEMPORAL_ADDRESS | `temporal:7233` | `docker-compose.yml:49,78` |
| DATABASE_URL | `postgresql+asyncpg://agent_app:agent_app@postgres:5432/agent_runtime` | `docker-compose.yml:46,75` |
| depends_on | postgres healthy + temporal healthy | `docker-compose.yml:63-67,91-95` |
| restart | `unless-stopped` | `docker-compose.yml:68,96` |

### 3.4 应用连接配置

| 项 | 当前值 | 证据 |
|---|---|---|
| 默认 address | `localhost:7233` | `src/agent_runtime/config.py:13` |
| namespace | `default` | `src/agent_runtime/config.py:14` |
| task queue | `agent-runtime` | `src/agent_runtime/config.py:15` |
| Client.connect | 无 timeout / retry / TLS | `src/agent_runtime/temporal_client.py:6-10` |
| Worker Client.connect | 同样无 timeout / retry / TLS | `src/agent_runtime/worker.py:128` |
| Worker 并发 | activities 50 / workflow tasks 100 | `src/agent_runtime/worker.py:177-178` |
| workflow id | `agent-run/{tenant_id}/{run_id}` | `src/agent_runtime/outbox/commands.py:117` |
| execution_timeout | `max_duration_seconds + 300` | B1 实现，`outbox/commands.py` `workflow_start_kwargs` |

### 3.5 Workflow / Replay

| 项 | 当前状态 | 证据 |
|---|---|---|
| 确定性 API | 使用 `workflow.now/uuid4/info/sleep/wait_condition` | `workflow.py`、`graph/runner.py` |
| 非确定性调用 | 未发现直接 `time.time`/`random` 等 | B1/B2 AST 守护 |
| `workflow.patched` / `get_version` | 0 命中 | B1 报告与全库检索 |
| 生产 `WorkflowReplayer` | 无自动化证据 | B1 报告 |
| 墙钟入口 Replay | 仅 `WallClockTimerWorkflow` | `tests/test_long_run_wall_clock.py` |
| 顺序敏感点 | 初始化、pause/resume、decide 失败分支、tool 分支、graph activity 链 | `workflow.py:491-671`、`graph/runner.py` |

### 3.6 测试与审计

| 项 | 当前状态 | 证据 |
|---|---|---|
| `RUN_TEMPORAL_IT` | 显式开关，默认跳过 | `tests/conftest.py:26-31` 与 4 个 IT 文件 |
| Client.connect 超时 | 10s | `tests/conftest.py:51-59` |
| IT 单测超时 | 900s | 4 个 Temporal IT 文件 `pytest.mark.timeout(900)` |
| 连接失败 | `pytest.fail`，不降级 SKIP | `tests/conftest.py:55-59` |
| 审计 runner | `scripts/run_pytest_audited.ps1` | B4.1 |
| 真实 Temporal B4 结果 | 24 passed / 382 warnings / 17:08 | `B4_TEST_RESULTS.md` |
| 退出码 | 原 B4 进程未捕获；B4.1 runner 已修复但未重跑完整 IT | `B4_1_TEST_RESULTS.md` |

### 3.7 部署 /备份现状

| 项 | 当前状态 |
|---|---|
| `docker/temporal/*` | 不存在 |
| auto-setup 容器 | 不存在 |
| `pg_dump` / `pg_restore` 实现 | 不存在 |
| Temporal admin/operator 备份脚本 | 不存在 |
| SQL schema 初始化脚本 | 不存在 |
| 备份恢复演练 | 不存在 |
| Makefile temporal target | 不存在 |
| README | 明确 dev server 不可直接用于生产 |

---

## 4. 关键风险结论

| 风险 | 严重性 | 当前证据 |
|---|---|---|
| `latest` 镜像漂移 | 高 | Compose 未 pin；实测版本仅当前容器状态 |
| Temporal history 容器重建即丢 | 高 | start-dev + sqlite + 无卷 |
| 当前无独立 Temporal DB | 高 | 无 SQL DSN、无 auto-setup |
| 无法备份/恢复 history | 高 | 无脚本、无卷 |
| Client 无连接超时/RPC retry | 中高 | `config.py` / `temporal_client.py` / `worker.py` |
| Workflow 无版本化保护 | 高 | 0 命中 `workflow.patched` / `get_version` |
| 生产 workflow 无自动 Replay | 中高 | 仅墙钟测试有 Replayer |
| 业务库/Temporal persistence 混用风险 | 未来高风险 | 当前隔离，但 B5 实施必须保持独立 DB/role |
| Namespace/retention 未固化为配置 | 中 | 当前 default / 24h 仅运行态，未写入 Compose |

---

## 5. B5.0 判定

现状满足：

- 本地开发、短时测试、B0–B4 的 Temporal IT。
- 应用级 outbox 幂等与 workflow id 稳定性验证。

现状不满足：

- 30 分钟以上任务的跨重启 history 持久化证明。
- Temporal Server 重建后 history 不丢。
- 备份/恢复/PITR。
- 固定版本与可复现部署。
- 生产级 Client 韧性。
- 旧 history replay 确定性保障。

因此 B5.1 必须以“独立 SQL Persistence + 固定版本 + 分阶段验证”为实施方向，且每次切换都必须单独审批。

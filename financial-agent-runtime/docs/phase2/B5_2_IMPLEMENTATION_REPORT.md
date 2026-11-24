# B5.2 实施报告：本地 Staging SQL Persistence 最小实现

日期：2026-11-23
状态：**IMPLEMENTED / PASS**。已完成本地 Staging 环境、双环境隔离、备份/恢复演练、Replay 验证、回滚演练。按授权边界停止，等待人工验收。

---

## 1. 结论

在本地单机完成 B5.2 的最小实现，覆盖 B5.0 规划中 Phase 2–5 的核心目标（不含生产 HA/多节点/TLS/密钥管理，这些属于后续治理）：

| 目标 | 状态 | 证据 |
|---|---|---|
| 独立 Staging Temporal + PostgreSQL | PASS | `finora-staging` compose 项目、独立端口/角色/卷/实例 |
| 固定镜像 digest（Server + Admin Tools） | PASS | 与 B5.1 相同 digest，`config` 校验通过 |
| SQL Persistence + Schema 初始化 | PASS | `PersistenceStore/VisibilityStore=postgres12`，schema-init completed |
| 7 天 retention | PASS | namespace `default` = `168h0m0s` |
| 双环境隔离（Dev + Staging 并存） | PASS | staging 7 个 workflow、dev 2 个 workflow，互不干扰 |
| 备份/恢复演练 | PASS | `RESULT=True`，两库行数与 namespace 配置 MD5 完全一致，隔离库已清理 |
| Replay 验证 | PASS | `REPLAY_OK`，1063 事件确定性 replay |
| 回滚演练（停/启 staging） | PASS | staging workflow 7→7 保留，dev 2→2 不受影响 |
| 回归测试 | PASS | 非 Temporal 221 passed；staging Temporal IT 7 passed |

---

## 2. 隔离设计（关键）

采用**实例级隔离**而非库名隔离：

| 维度 | Dev | Staging | 业务库 |
|---|---|---|---|
| compose 项目 | `financial-agent-runtime` | `finora-staging` | `financial-agent-runtime` |
| Temporal 端口 | 7233 / 8233 | **7234 / 8234** | — |
| PostgreSQL 端口 | 5433（host） | **5434（host，127.0.0.1）** | 5433（业务实例） |
| PG 角色 | `temporal` | **`temporal_staging`** | `agent` |
| 卷 | `temporal_pg_data` | **`temporal_staging_pg_data`** | `postgres_data` |
| 数据库 | `temporal` / `temporal_visibility` | `temporal` / `temporal_visibility`（独立实例内） | `agent_runtime` |

说明：首次尝试给 staging 使用不同 DB 名（`temporal_staging`）时，Server 镜像的 embedded config 固定连接 `temporal` 库，导致启动失败；改为实例级隔离后验证通过。隔离由**独立 PG 实例/端口/卷/项目/角色**保证，等价于不同物理库，且 schema 名一致便于运维。

---

## 3. 交付物

| 文件 | 类型 | 说明 |
|---|---|---|
| `docker-compose.staging.yml` | 新增 | 独立 compose 项目 `finora-staging`，4 个服务 + 2 个卷 |
| `.env` | 修改（gitignored） | 追加 `TEMPORAL_STAGING_SQL_*`、`TEMPORAL_STAGING_RETENTION=168h` |
| `scripts/backup_restore_drill.ps1` | 新增 | 双库 pg_dump → 隔离库恢复 → 行数/MD5 校验 → 清理 |
| `scripts/verify_staging_replay.py` | 新增 | 从 staging 导出真实 history，用 `Replayer` 验证 `AgentRunWorkflow` |
| `docs/phase2/B5_2_*.md` | 新增 | 6 份文档 |
| 审计日志 | 新增 | `b52_staging_temporal_it.*`、`b52_backup_restore.log`、`b52_final_regression.*` |

未修改 `docker-compose.yml`、`docker-compose.temporal-sql.yml`、`src/agent_runtime/**`、任何测试断言。

---

## 4. 使用方式

启动 Staging：

```powershell
docker compose -f docker-compose.staging.yml up -d
```

验证：

```powershell
docker run --rm --network finora-staging_default --entrypoint temporal `
  temporalio/admin-tools@sha256:4a95866efba29d7e17ab012df5232795e8b0ad0f7d5f9d1ec4df321f7792a754 `
  operator cluster describe --address temporal-staging:7233
```

备份恢复演练：

```powershell
./scripts/backup_restore_drill.ps1
```

Replay 验证：

```powershell
python scripts/verify_staging_replay.py
```

回滚（仅 staging，保留卷）：

```powershell
docker compose -f docker-compose.staging.yml stop temporal-staging temporal-staging-postgres
docker compose -f docker-compose.staging.yml start temporal-staging-postgres temporal-staging
```

详细回滚见 `B5_2_ROLLBACK_GUIDE.md`。

---

## 5. 边界

- 未实施生产 HA、多节点、TLS、密钥管理（超出本地最小实现范围）。
- 未修改 Dev 环境；Dev 的 SQL Persistence 与业务库保持原样。
- Staging 无常驻 worker（避免真实副作用），仅通过 IT 的进程内 worker 与 replay 验证。
- 备份演练恢复到同一 PG 实例的隔离库，未做跨主机恢复（本地最小实现）。
- 完成后停止，等待人工验收；不自动进入 B7 或治理项。

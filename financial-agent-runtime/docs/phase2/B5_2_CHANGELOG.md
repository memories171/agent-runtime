# B5.2 变更清单

日期：2026-11-23
范围：本地 Staging SQL Persistence 最小实现。

---

## 1. 新增文件

| 文件 | 说明 |
|---|---|
| `docker-compose.staging.yml` | 独立 compose 项目 `finora-staging`；服务 `temporal-staging-postgres` / `temporal-staging-schema-init` / `temporal-staging` / `temporal-staging-namespace-init`；端口 7234/8234/5434；卷 `temporal_staging_pg_data` / `temporal_staging_schema_flag`；固定 digest 镜像；retention 168h |
| `scripts/backup_restore_drill.ps1` | 双库（temporal + temporal_visibility）pg_dump 到 `backups/`，恢复到隔离库 `temporal_restore_test` / `temporal_visibility_restore_test`，校验行数与 namespace 配置 MD5，清理隔离库 |
| `scripts/verify_staging_replay.py` | 连接 staging:7234，找最近完成的 `AgentRunWorkflow`，`fetch_history()` 后用 `Replayer` 验证 |
| `docs/phase2/B5_2_IMPLEMENTATION_REPORT.md` | 实施报告 |
| `docs/phase2/B5_2_CHANGELOG.md` | 本清单 |
| `docs/phase2/B5_2_TEST_RESULTS.md` | 测试结果 |
| `docs/phase2/B5_2_RESOURCE_USAGE.md` | 资源与成本 |
| `docs/phase2/B5_2_ROLLBACK_GUIDE.md` | 回滚指南 |
| `docs/phase2/B5_2_LEFTOVER_ISSUES.md` | 遗留问题 |

审计产物（保留在项目根）：`b52_staging_temporal_it.{out,err,meta.out}`、`b52_backup_restore.log`、`b52_final_regression.{out,err,meta.out}`、`backups/temporal_staging_*.dump`（演练备份文件）。

---

## 2. 修改文件

### `.env`（本地 gitignored，追加）

```env
TEMPORAL_STAGING_SQL_USER=temporal_staging
TEMPORAL_STAGING_SQL_PASSWORD=temporal_staging_dev_2026
TEMPORAL_STAGING_SQL_DB=temporal_staging       # 注：compose 中默认库已改为 temporal（见下）
TEMPORAL_STAGING_SQL_VISIBILITY_DB=temporal_staging_visibility
TEMPORAL_STAGING_RETENTION=168h
```

注：调试中发现 Server 镜像 embedded config 只连 `temporal` 库，故 compose 中 staging PG 的 `POSTGRES_DB` 与 schema-init 的 `TEMPORAL_DB`/`TEMPORAL_VISIBILITY_DB` 固定为 `temporal`/`temporal_visibility`；`.env` 中的 `*_SQL_DB` 变量保留但不再被 compose 使用（可后续清理）。隔离靠实例/端口/卷/项目/角色实现。

---

## 3. 未修改项

- `docker-compose.yml`、`docker-compose.temporal-sql.yml`：原样。
- `src/agent_runtime/**`：0 改动。
- `tests/**`：0 改动（B6 的 replay 修复属上一步）。
- Dev Temporal、业务 PostgreSQL、Keycloak 配置：未改。
- `scripts/run_pytest_audited.ps1`：未改。

---

## 4. 修复记录

实施中修复的问题（均在 B5.2 新增文件内，不影响已有交付物）：

1. **DB 名不兼容**：初次用 `temporal_staging` 库名导致 Server 连不上（embedded config 固定 `temporal`）；改为实例级隔离。
2. **备份脚本 PowerShell `$Args` 保留变量冲突**：重命名为 `$PgArgs` 并重写返回值处理。
3. **校验误判**：visibility 表在独立库、namespace retention 为 protobuf 编码；改为分别备份恢复 visibility 库并用 `md5(data)` 校验配置一致性。
4. **Replay 脚本 SDK API**：`get_workflow` → `get_workflow_handle`（temporalio 1.36）。

---

## 5. 影响评估

- Dev 环境完全不受影响（回滚演练中 dev workflow 2→2 验证）。
- Staging 环境为新增，可独立启停/删除，不影响业务库。
- 所有验证通过审计脚本或可复查日志。

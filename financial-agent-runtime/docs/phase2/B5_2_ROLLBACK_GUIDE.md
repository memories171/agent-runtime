# B5.2 回滚指南

日期：2026-11-23
范围：本地 Staging 环境。Dev 与业务库不属于本回滚范围（从未被修改）。

---

## 1. 原则

- Staging 是独立 compose 项目 `finora-staging`，与 Dev 无共享卷/端口/数据库实例。
- 回滚只影响 staging，不影响 Dev 7233、业务库 `agent_runtime`、Keycloak。
- 默认保留卷（History 数据不丢）；只有确认丢弃时才用 `down -v`。

---

## 2. 日常停止 / 启动（保留数据）

```powershell
cd "f:/AAA.个人项目/Agent runtime/financial-agent-runtime"

# 停止（保留卷）
docker compose -f docker-compose.staging.yml stop temporal-staging temporal-staging-postgres

# 启动
docker compose -f docker-compose.staging.yml start temporal-staging-postgres temporal-staging
```

本次回滚演练即按此序列执行，结果：staging workflow 7→7、dev workflow 2→2、stores 保持 postgres12。

---

## 3. 完全移除 Staging（保留卷）

```powershell
docker compose -f docker-compose.staging.yml down
```

验证 Dev 不受影响：

```powershell
docker ps --filter "name=financial-agent-runtime-temporal-1"
docker run --rm --network financial-agent-runtime_default --entrypoint temporal `
  temporalio/admin-tools@sha256:4a95866efba29d7e17ab012df5232795e8b0ad0f7d5f9d1ec4df321f7792a754 `
  operator cluster describe --address temporal:7233
```

预期 Dev store 仍为 postgres12，workflow 数不变。

---

## 4. 彻底删除（含数据，谨慎）

```powershell
docker compose -f docker-compose.staging.yml down -v
```

**警告**：会删除 `finora-staging_temporal_staging_pg_data`（Staging History 全部丢失）。仅在确认不再需要时执行；不要对 `financial-agent-runtime` 项目执行 `-v`。

---

## 5. 数据恢复路径

若 Staging 数据损坏但卷仍在：

1. 停止 staging server（保留 PG）。
2. 从最近备份恢复（备份在 `backups/temporal_staging_*.dump`，另有 `.visibility` 文件）：
   ```powershell
   ./scripts/backup_restore_drill.ps1  # 演练脚本展示了完整 dump/restore 流程；实际生产恢复需按目标库调整
   ```
3. 启动 staging 并用 `workflow list` / replay 脚本验证。

说明：当前演练脚本恢复到**隔离测试库**并清理，不是覆盖运行库；真实灾难恢复需单独执行覆盖流程（B5.2 本地最小实现未包含覆盖式恢复，避免破坏验证环境）。

---

## 6. 故障场景

| 场景 | 处理 |
|---|---|
| Staging server 连不上 DB（启动失败） | 确认 `POSTGRES_DB=temporal`、角色 `temporal_staging`；不要用不同 DB 名（embedded config 固定 temporal） |
| schema-init exit 1（flag 权限） | 已用 `user: "0:0"` 修复；如复现仅在空库时删 `temporal_staging_schema_flag` 卷重建 |
| 备份校验 MATCH=False | 检查是否误把 visibility 表查到主库（应查 `temporal_visibility`）；namespace 用 `md5(data)` 而非文本匹配 |
| Replay 脚本报 `get_workflow` 不存在 | 用 `get_workflow_handle`（temporalio 1.36） |
| 业务 API 502 | 检查 `postgres`/`keycloak` 是否被停止；本批次期间出现过两次，手动 `docker start` 后恢复，与 staging 无关 |

---

## 7. 回滚验证清单

- [ ] staging stop/start 后 workflow 数不变
- [ ] dev workflow 数与 store 不变
- [ ] 业务 `/health` 200、`/auth/me` 401（正常）
- [ ] 卷 `finora-staging_temporal_staging_pg_data` 仍存在
- [ ] 如需完全移除，`down` 后确认 7234/5434 端口释放

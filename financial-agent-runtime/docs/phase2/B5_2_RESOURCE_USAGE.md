# B5.2 资源使用与成本

日期：2026-11-23
范围：本地 Staging 最小实现的实测资源与估算。

---

## 1. 实测新增容器资源

采样：`docker stats --no-stream`（Staging 4 容器稳定运行期间）

| 容器 | 说明 | 内存量级 |
|---|---|---|
| `finora-staging-temporal-staging-1` | Temporal Server | ~170 MiB |
| `finora-staging-temporal-staging-postgres-1` | PostgreSQL | ~150–160 MiB |
| `temporal-staging-schema-init-1` | 初始化后 Exited 0 | 0（不在运行） |
| `temporal-staging-namespace-init-1` | 初始化后 Exited 0 | 0（不在运行） |

常驻增量约 **320 MiB RAM**，与 B5.1 Dev SQL persistence 的增量相当。端口占用：7234、8234、127.0.0.1:5434。

---

## 2. 卷与磁盘

| 卷 | 用途 | 说明 |
|---|---|---|
| `finora-staging_temporal_staging_pg_data` | Staging History/Visibility | 7 个 workflow 后约数十 MB |
| `finora-staging_temporal_staging_schema_flag` | schema 初始化标记 | ~0 B |
| `backups/temporal_staging_*.dump` | 演练备份文件 | 主库 ~401 KB，visibility ~46 KB（单次） |

说明：备份演练每次生成一个带时间戳的 dump 文件并保留在 `backups/`；如长期运行需配合保留策略清理旧备份（见遗留问题）。

---

## 3. 镜像

与 B5.1 完全一致，固定 digest：

- `temporalio/server@sha256:7bf69188...`
- `temporalio/admin-tools@sha256:4a95866e...`
- `postgres:16-alpine`

不引入新镜像，不增加镜像拉取/存储成本。

---

## 4. 耗时

| 操作 | 实测耗时 |
|---|---|
| Staging 环境启动（含 schema 初始化） | < 1 分钟 |
| Staging Temporal IT（7 用例） | 04:08 |
| 备份 + 恢复到隔离库 + 校验 + 清理 | 5.86 秒 |
| Replay（1063 事件） | < 5 秒 |
| 回滚演练（stop/start + 验证） | < 1 分钟 |
| 非 Temporal 全量回归 | 04:06 |

---

## 5. 成本结论

本地 Staging 最小实现的资源与成本很低（常驻约 320 MiB、磁盘增量 <100 MB、无新镜像）。真正的成本与复杂度在生产 Staging（独立主机/托管 PG/备份存储/HA），属后续治理范围，本批次未采购/扩容任何云资源。

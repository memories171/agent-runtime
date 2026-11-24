# B5.1 资源使用与成本估算

日期：2026-11-21
范围：本地 Dev 环境 SQL Persistence 最小实现的实测资源占用与后续环境成本估算。

---

## 1. 实测资源占用（Dev，运行态）

命令：`docker stats --no-stream`（测试运行期间采样）

| 容器 | CPU% | 内存占用 / 上限 | 内存% |
|---|---|---|---|
| temporal（SQL Server） | 5.56% | 171.8 MiB / 7.638 GiB | 2.20% |
| temporal-postgres | 3.95% | 158.4 MiB / 7.638 GiB | 2.03% |
| api | 0.15% | 135.7 MiB / 7.638 GiB | 1.74% |
| worker（默认，卡在 MCP discovery） | 9.73% | 601.4 MiB / 7.638 GiB | 7.69% |
| keycloak | 0.13% | 787.6 MiB / 7.638 GiB | 10.07% |
| postgres（业务库） | 波动（测试期高） | 461.6 MiB / 7.638 GiB | 5.90% |

说明：

- Temporal Server + Temporal PostgreSQL 合计约 **330 MiB**、CPU 个位百分比，负载很轻。
- 相比原 SQLite `start-dev`，新增的主要是 `temporal-postgres` 进程（约 158 MiB）与独立卷。
- 测试期间业务 `postgres` CPU 波动是 pytest 全量读写所致，非 SQL overlay 引入。

---

## 2. 持久化卷

| 卷 | 用途 | 实测大小 |
|---|---|---|
| `financial-agent-runtime_temporal_pg_data` | Temporal History / Visibility 数据 | 85.31 MB（24 个 Temporal IT + 手工 workflow 之后） |
| `financial-agent-runtime_temporal_schema_flag` | schema 初始化标记 | 0 B |
| `financial-agent-runtime_postgres_data` | 业务库（既有，未改动） | 未统计 |

说明：24 个 Temporal IT + 手工验证后 Temporal 数据卷仅 ~85 MB；Dev 阶段磁盘成本可忽略。随 History 增长，卷会持续增大；生产需按 retention（当前 namespace 24h）与清理策略规划容量。

---

## 3. 镜像

| 镜像 | digest | 说明 |
|---|---|---|
| `temporalio/server@sha256:7bf69188...` | 固定 | 替代 `temporalio/temporal:latest`，生产 server，SQL persistence |
| `temporalio/admin-tools@sha256:4a95866e...` | 固定 | schema 初始化 + namespace 初始化 + CLI |
| `postgres:16-alpine` | tag | 与业务库同版本，项目既有 |

固定 digest 避免使用 `latest`，保证 Dev 与后续 Staging 使用同一 Server/工具版本。

---

## 4. 测试耗时（审计脚本实测）

| 测试批次 | 墙钟耗时 |
|---|---|
| Temporal IT 冒烟（7） | 03:56 |
| 非 Temporal 全量（219） | 04:10 |
| Temporal IT 其余（17） | 16:06 |
| 合计 pytest | 约 24 分钟 |

说明：24 个 Temporal IT 合计约 20 分钟，与 B4 原始 17 分钟量级一致（宿主机 + SQL 稍慢）。35 分钟墙钟测试未运行，不计入。

---

## 5. 后续环境成本估算（供 B5.2 参考，未实施）

| 环境 | 组件 | 估算 | 说明 |
|---|---|---|---|
| Dev（当前） | Temporal Server + PG + 2 卷 | ~330 MiB RAM，<200 MB 磁盘增量 | 实测 |
| Staging（B5.2 规划） | Temporal Server ×1 + PG ×1（建议独立实例或托管 PG） | 建议 1–2 GiB RAM / Server，2–4 GiB RAM / PG | 单节点，无 HA；按 IT 并发与 retention 调整 |
| 双环境（B5.2 规划） | 上述 ×2 + 备份存储 | 资源翻倍 + 备份空间 | 需独立 schema/DB，避免跨环境串数据 |

成本驱动因素：

- Workflow History 保留时长（当前 24h retention）。
- Visibility 数据量与高级搜索属性。
- Worker 并发与 activity 吞吐。
- 备份频率与保留周期（B5.2 规划）。

以上为估算，本批次未采购、未扩容、未申请任何云资源。

---

## 6. 结论

Dev 最小实现的资源开销很低（Temporal + 独立 PG 约 330 MiB），对现有开发机无实质影响。真正需要规划的是后续 Staging / 双环境的容量与备份策略，属于 B5.2 范围，本批次仅估算。

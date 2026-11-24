# B5.0 资源与成本评估

日期：2026-11-21
状态：基于当前架构审查的初步估算，等待 B5.1 实测校准。本批次未启动新数据库，未部署任何持久化服务。

---

## 1. 估算口径

假设：

- 单个 Run 最多 200 steps（B2 硬上限）。
- 单 step 约产生 5–10 个 Temporal history events。
- 单 event payload 平均 1–2 KB（含 activity input/output、事件元数据）。
- 单 Run history 估算：200 × 7.5 × 1.5KB ≈ **2.25 MB**。
- PostgreSQL 索引、WAL、page overhead 按 2–3 倍估算，即单 Run 落盘约 **5–7 MB**。
- 业务库与 Temporal Persistence 库分离，容量分别计算。

这些是规划估算，不是实测基准；B5.1 必须用真实 history 样本复核。

---

## 2. 最低本地 Docker 开发配置

| 组件 | CPU | 内存 | 磁盘 | 说明 |
|---|---:|---:|---:|---|
| 业务 PostgreSQL | 1 vCPU | 1 GB | 10 GB | 现有 `postgres_data` |
| Temporal PostgreSQL | 1 vCPU | 1 GB | 10–20 GB | 新增独立卷 |
| Temporal Server | 1 vCPU | 2 GB | 不计 | SQL persistence，非 dev |
| API + Worker | 1–2 vCPU | 2 GB | 不计 | 本地单实例 |
| 系统余量 | 1 vCPU | 2 GB | 20 GB | Docker/日志/镜像 |
| **合计建议** | **4 vCPU** | **8 GB** | **40–60 GB** | 低于此值可能测试不稳定 |

最低可运行配置：

- 2 vCPU / 4 GB RAM / 20–30 GB disk。
- 仅适合单测和短 Temporal IT，不适合长跑与并发验证。

本地 Docker 镜像与容器建议预留：

- Docker 镜像层：5–10 GB。
- Temporal SQL 测试数据：10–20 GB。
- 日志与 SQLite/dev 遗留：5 GB。

---

## 3. 最低 Dev SQL Persistence 配置

| 资源 | 建议 |
|---|---|
| CPU | 2–4 vCPU |
| Memory | 6–8 GB |
| Temporal PostgreSQL disk | 20–50 GB SSD |
| Temporal Server memory | 2 GB |
| Backup disk | 20–50 GB |
| Retention | 24h–72h |

适用：

- 单个 Worker。
- 少量并发 Run。
- 30 分钟以内任务验证。
- 不做真实负载压测。

---

## 4. 建议 Staging 配置

| 组件 | CPU | 内存 | 磁盘 | 备注 |
|---|---:|---:|---:|---|
| 业务 PostgreSQL | 2–4 vCPU | 4–8 GB | 100–200 GB SSD | 与现有业务库一致 |
| Temporal PostgreSQL | 4 vCPU | 8–16 GB | 500 GB SSD 起步 | history + visibility + WAL |
| Temporal Server | 2–4 vCPU | 4–8 GB | 不计 | 固定版本 |
| API | 1–2 vCPU | 2–4 GB | 不计 | 多副本可选 |
| Worker | 2–4 vCPU | 4–8 GB | 不计 | 按 activity 并发调整 |
| Backup target | 不计应用 CPU | 不计应用内存 | 2–4× Temporal DB | 本地 NAS 或对象存储 |

建议 Staging 总计：

- 8–16 vCPU。
- 24–48 GB RAM。
- Temporal DB 500 GB SSD 起步，按 retention 和数据增长调整。
- 备份空间 1–2 TB，视保留周期调整。

---

## 5. 预计数据增长

### 5.1 按 Run 数估算

单 Run 落盘约 5–7 MB。

| 日 Run 数 | 日新增（估） | 7 天 retained | 30 天 retained |
|---:|---:|---:|---:|
| 100 | 0.5–0.7 GB | 3.5–5 GB | 15–21 GB |
| 1,000 | 5–7 GB | 35–49 GB | 150–210 GB |
| 10,000 | 50–70 GB | 350–490 GB | 1.5–2.1 TB |

### 5.2 长任务影响

B6 将验证 35 分钟真实墙钟任务。单次 35 分钟任务按 200 steps 上限估算，与短任务 history 规模相近；但：

- activity timeout/retry 会增加 events。
- Worker 重启会增加 workflow task failure / retry events。
- UNKNOWN 人工对账增加人工操作审计与补写事件。

因此长任务建议按 **1.5–2×** 单 Run 估算，即 8–14 MB / Run。

### 5.3 Visibility 数据

若 SQL visibility 与 history 同库：

- 总量再增加 10–30%。
- Staging 容量按 1.3× history 估算。

---

## 6. 备份空间与保留周期

| 环境 | 全量备份频率 | 增量/WAL | 保留周期 | 估算空间 |
|---|---|---|---|---|
| Dev | 手动/每日 | 不强制 | 3–7 天 | 2× DB size |
| Staging | 每日全量 + WAL/增量 | 建议启用 | 14–30 天 | 2–4× DB size |
| 发布前 | 手动快照 | 不强制 | 至少到回滚窗口结束 | 1× DB size / 次 |

建议：

- 每日 dump 保留 14 天。
- 每周全量保留 4–8 周。
- 重大切换前额外快照保留至人工确认回滚窗口关闭。
- 每季度做一次恢复演练。

---

## 7. 成本构成

本批次不接入云端 CI/生产部署，因此成本以资源占用与运维工作量表示。

| 成本项 | Dev | Staging |
|---|---|---|
| 计算 | 本地 Docker，4 vCPU / 8 GB | 8–16 vCPU / 24–48 GB |
| 存储 | 40–60 GB | 500 GB+ SSD，备份 1–2 TB |
| 网络 | 本地 bridge | 应用/Temporal/DB 同 VPC，低延迟 |
| 运维 | 单次手动验证 | 监控、备份、恢复演练、版本升级 |
| 许可 | Temporal Server / PostgreSQL OSS | 无软件许可成本，基础设施成本另计 |
| 人力 | 低 | 中高：需要 DBA/平台值班与演练记录 |

---

## 8. 成本优化建议

1. Dev 不为 Temporal SQL 暴露宿主端口，减少攻击面。
2. Staging retention 从 7d 起步，按审计需求调整。
3. history 与 visibility 同库可先降低运维复杂度；如规模上升再拆分。
4. 备份压缩后存对象存储；保留周期按审计要求而非无限期。
5. Worker activity 并发不应盲目提高，避免 Temporal history 写入压力放大。

---

## 9. 需在 B5.1 校准的实测指标

- 每个真实 workflow 的 history events 数。
- 每个 event 的平均 payload size。
- SQL persistence 写入 QPS。
- 5000 Run 压测下 DB 文件大小与 WAL 增长。
- pg_dump 耗时与恢复耗时。
- Worker 重启后 history 追加增量。

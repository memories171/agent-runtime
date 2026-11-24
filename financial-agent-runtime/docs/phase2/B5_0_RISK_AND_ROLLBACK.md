# B5.0 风险与回滚设计

日期：2026-11-21
状态：设计完成，等待人工验收与 B5.1 单独授权。本批次未执行切换、迁移或备份恢复。

---

## 1. 风险矩阵

| ID | 风险 | 概率 | 影响 | 等级 | 缓解 |
|---|---|---|---|---|---|
| R1 | 业务库与 Temporal Persistence 混用 | 中 | 极高 | 高 | 独立 DB/role/volume/backup；部署前校验 DSN |
| R2 | `latest` 镜像升级导致 history 不兼容 | 中高 | 高 | 高 | 固定 Server/auto-setup/UI tag，版本记录入部署清单 |
| R3 | Schema 初始化失败或部分完成 | 中 | 高 | 高 | auto-setup 作为独立阶段；失败即停止；快照后再试 |
| R4 | Worker 重启后不确定恢复 | 中 | 高 | 高 | Worker restart recovery 测试；Run ID 不变断言；副作用审计 |
| R5 | Replay 非确定性 | 高（未来改 workflow 时） | 高 | 高 | 代表性 history 回放；必要时 `workflow.patched`，单独审批 |
| R6 | 双环境验证重复外部副作用 | 中高 | 极高 | 高 | 只允许一个 active worker；影子环境只 replay/只读 |
| R7 | 备份不可恢复 | 中 | 极高 | 高 | 恢复到隔离库演练；校验 history/namespace |
| R8 | 数据库不可用导致 Worker 无限重连 | 中 | 中高 | 中高 | 增加连接超时、告警、启动快速失败 |
| R9 | Namespace/retention 配置漂移 | 中 | 中 | 中 | 配置固化；部署后 `operator namespace describe` 验证 |
| R10 | 审计退出码缺失 | 中 | 中 | 中 | 使用 `scripts/run_pytest_audited.ps1`，保存 meta |
| R11 | 35 分钟任务误入 B5 | 低 | 中 | 中 | 明确归 B6；B5 不启动真实墙钟任务 |
| R12 | 权限/ACL 被误改 | 低 | 高 | 中 | B5 不改 Phase 1 ACL/JWT；回归 `test_security_regression.py` |

---

## 2. 通用回滚原则

1. **先停止写入，再回滚**
   - 停止新 worker / dispatch。
   - 不删除 workflow history。
   - 不直接删除 temporal DB volume。

2. **保留证据**
   - compose config 输出。
   - 容器日志。
   - `operator cluster describe`。
   - `operator namespace describe`。
   - 备份 dump 与 checksum。
   - 审计脚本 stdout/stderr/meta。

3. **回滚目标必须明确**
   - Dev：回到 `start-dev`。
   - Staging：回到上一条已批准 compose/config 与旧 Temporal address。
   - Worker：回到上一个已验证镜像/启动参数。

4. **不得用重跑成功用例覆盖失败记录**
   - FAIL 与 NOT RUN 必须保留。
   - 重跑必须使用审计脚本并产生新日志前缀。

5. **数据库不可逆操作需人工批准**
   - 不自动 drop schema。
   - 不自动 delete history。
   - 不自动 drop volume。

---

## 3. 分阶段回滚

### Phase 1：Dev SQL Persistence

触发条件：

- SQL 连接失败。
- auto-setup 非 0 退出。
- namespace 未创建。
- 业务库出现 Temporal schema（立即回滚）。

步骤：

1. `docker compose -f docker-compose.yml -f docker-compose.temporal-sql.yml down`。
2. 恢复仅使用 `docker-compose.yml`。
3. 用原 `server start-dev` 启动。
4. 校验 `cluster describe` 回到 sqlite。
5. 保留 `temporal_pg_data` 卷和失败日志。

不得：

- 不得自动删除 `temporal_pg_data`。
- 不得把 schema 初始化脚本指向业务库重试。

---

### Phase 2：Staging SQL Persistence

触发条件：

- 固定版本不符。
- healthcheck 失败。
- Namespace retention 不符。
- Worker 无法启动。
- 业务/Temporal DSN 校验失败。

步骤：

1. 停止新 Worker。
2. 将 `TEMPORAL_ADDRESS` 指回上一集群。
3. 部署上一版 compose/config。
4. 运行只读 cluster/namespace 检查。
5. 运行精简 smoke test 并审计留档。

不得：

- 不得直接丢掉新集群 history。
- 不得在旧集群重放同一 workflow id 触发副作用。

---

### Phase 3：Staging 双环境验证

触发条件：

- 出现重复副作用。
- SQL 环境 history 无法查询。
- Replay 失败。
- IT 非 0 退出码。

步骤：

1. 立即停用影子环境 worker（如果有）。
2. 保持 active worker 单一来源。
3. 导出失败 history 与日志。
4. 回滚 active worker 到上一集群。
5. 用 `run_pytest_audited.ps1` 重新做只读 smoke。

不得：

- 不得让两个 worker 使用同一 task queue 执行同一业务 workflow。

---

### Phase 4：Worker 重启与 Workflow 恢复

触发条件：

- Run ID 改变。
- workflow history 断裂。
- 业务 DB 出现重复终态。
- Activity 副作用重复。

步骤：

1. 停止新 Worker。
2. 查询受影响 workflow history。
3. 对比业务 `tool_invocations` 与 `run_events`。
4. 按 operation-specific runbook 人工对账。
5. 回退 Worker 镜像/配置并重新验证。

不得：

- 不得自动重置 `fencing_token`。
- 不得删除或覆盖业务终态。

---

### Phase 5：Replay 确定性验证

触发条件：

- `NonDeterministicError`。
- 历史 activity 顺序变化。
- 新代码无法 replay 旧 history。

步骤：

1. 停止新 workflow 版本部署。
2. 固定上一版本代码。
3. 保留失败 history 样本。
4. 评估是否必须 `workflow.patched` / `get_version`。
5. 任何 workflow 语义变更必须单独审批。

不得：

- 不得删除旧 history。
- 不得改断言让 replay 测试通过。

---

### Phase 6：备份与恢复演练

触发条件：

- dump 失败。
- restore 失败。
- 恢复库 namespace/history 校验失败。

步骤：

1. 保留失败 dump 和日志。
2. 重新执行 pg_dump，不得覆盖原成功备份。
3. 恢复到另一个隔离 DB name。
4. 校验 checksum 与关键表。
5. 演练失败不得影响运行库。

不得：

- 不得把恢复库直接切为运行库，除非完成完整审批。

---

## 4. 数据库不可用与恢复策略

| 场景 | 期望行为 |
|---|---|
| Temporal PostgreSQL 未 healthy | temporal-auto-setup / server 不启动 |
| Server 健康检查失败 | api/worker 不启动或启动后快速失败 |
| Worker 运行中 Temporal 不可用 | activity 已开始的可按 Temporal retry；新 workflow dispatch 失败可重试 outbox |
| 数据库短暂不可用 | PostgreSQL 恢复后 server 重连；worker 不应静默永久失联 |
| 数据文件损坏 | 停写入，使用最近可用备份恢复到隔离库，人工确认后切换 |

B5.1 需要补充：

- Client connect timeout。
- RPC retry/keepalive。
- 明确告警或启动失败日志。

---

## 5. 测试与审计留档

所有 B5.1 及后续验证必须使用：

```powershell
& ./scripts/run_pytest_audited.ps1 -LogPrefix "<run-name>" -MarkerExpression "<marker>" [-Temporal]
```

必须保存：

- `<run-name>.out`
- `<run-name>.err`
- `<run-name>.meta.out`

meta 必须包含：

- 启动时间
- 结束时间
- 耗时
- 真实退出码
- 实际命令
- marker
- Temporal 开关
- stdout/stderr 路径

结果分类：

| 情况 | 分类 |
|---|---|
| EXITCODE=0 且全部用例通过 | PASS |
| EXITCODE≠0 或存在 FAIL | FAIL |
| 明确开关未启用的用例 | SKIP |
| 未执行 | NOT RUN |
| 日志/退出码缺失 | INCOMPLETE |

退出码缺失时不得根据 pytest 输出推断。

---

## 6. 停止条件

B5.0 仅完成审查与设计。完成四份文档后立即停止，等待人工验收与 B5.1 单独授权。

未经授权：

- 不修改 Compose。
- 不执行 schema migration。
- 不启动 Temporal Persistence 数据库。
- 不进入 B5.1。

# B5.1 遗留问题

日期：2026-11-21
说明：记录 B5.1 实施中发现的、未在本批次修复或属于后续阶段范围的问题。区分「既有条件」「本批次已知限制」「B5.2 范围」。

---

## 1. 既有测试基础设施条件（非 B5.1 引入）

### 1.1 Docker `worker` 容器卡在 MCP discovery

- 现象：默认 `worker` 容器 CPU 占用但不处理 workflow；`/proc/net/tcp` 只有到业务 PostgreSQL（5432）的连接，无到 Temporal（7233）的连接。
- 根因：worker 启动时 `configure_mcp_servers()` 连接 `host.docker.internal:3001` 的 MCP 服务（mock-external），该容器已 Exited 44 小时；MCP SDK 的 `Client` 无超时，连接挂起。
- 影响：长期运行的 workflow 停在 `WorkflowTaskScheduled`，`verify_step3_e2e.py` 60s 超时。
- 非 B5.1 引入：此条件在 SQL persistence 之前即存在（同样的 MCP 配置、同样的 mock-external 停止状态）。
- B5.1 验证不受影响：所有 24 个 Temporal IT 使用进程内 worker，全部 PASS；手工用禁用 MCP（`MCP_SERVERS_JSON=[]`）的临时 worker 也确认能连接 SQL-backed Temporal 并轮询。
- 建议（B5.2 或独立任务）：为 `MCPToolClient` 增加连接超时；或在 Dev compose 中默认不注入不可达 MCP server；或保证 mock-external 可用。

### 1.2 pytest warnings（382 → 本批次 6/166/229）

- 分类与 B4.1 一致：
  - Pydantic `model_fields` deprecated（context/assembly.py）
  - asyncio `get/set_event_loop_policy` DeprecationWarning（pytest-asyncio / Python 3.14）
  - `PytestUnraisableExceptionWarning: Not in workflow event loop`（temporalio，既有）
  - `InsecureKeyLengthWarning`（JWT 测试短 key，既有）
- 修复状态：仅修复明确属于测试基础设施的问题；上述均为依赖弃用/既有测试设计，不在 B5.1 范围。

---

## 2. 本批次已知限制

### 2.1 未做多架构镜像验证

- 仅验证本地 linux/amd64 的 digest 与 manifest。
- `docker buildx imagetools inspect` 多次出现 registry 连接重置，未声称完成 multi-arch 验证。
- Docker 平台：Server 29.9.3，OS linux，ARCH amd64，WSL2。

### 2.2 单次持久化验证的边界

- Server 重启验证：停止并重启了 `temporal` 容器（保留卷），在同一 RunId 上确认 History 完整；未做多次连续重启或长时间稳定性测试。
- Worker 恢复验证：通过 Temporal IT 的 worker crash/replay 用例与临时 worker 重连验证；默认 worker 因 1.1 条件不处理任务，未做「业务默认 worker 端到端恢复」验证。
- 35 分钟墙钟测试：未运行（未授权）。

### 2.3 `BIND_ON_IP=0.0.0.0` 的广播地址

- Server entrypoint 在 `BIND_ON_IP=0.0.0.0` 时自动设置 `TEMPORAL_BROADCAST_ADDRESS` 为容器 IP。
- 重启瞬间出现 `Not enough hosts to serve the request` / `dial tcp <ip>:7235 connection refused` 的单节点 membership 瞬态错误，健康后自动消失（实测 last-15s error 计数为 0）。
- 单节点 Dev 可接受；B5.2 多节点/Staging 需重新评估广播地址与服务发现。

---

## 3. B5.2 及后续范围（本批次未实施）

| 项 | 说明 | 状态 |
|---|---|---|
| Staging 环境 SQL persistence | 独立环境、独立 schema/DB、固定镜像 | NOT RUN |
| 双环境（Dev + Staging）并存 | 资源、网络、数据安全隔离 | NOT RUN |
| 备份与恢复演练 | pg_dump / 恢复验证 / 保留策略 | NOT RUN |
| Replay 验证（专门化） | 除 IT 中的 replay 用例外的独立验证 | NOT RUN（IT 内已覆盖基础 replay） |
| 回滚演练（Staging 级） | 跨环境回滚流程验证 | NOT RUN |
| 生产级 HA / 多节点 | membership、广播地址、副本 | NOT RUN |
| MCP 连接超时修复 | 独立测试基础设施任务 | NOT RUN |

---

## 4. 配置与运维待确认（需人工）

- `.env` 中 `TEMPORAL_SQL_PASSWORD=temporal_dev_2026` 为本地 Dev 值；进入 Staging 前需替换为密钥管理方案。
- 是否将 `temporal-postgres` 端口映射到宿主（当前无映射，更安全）；如需调试，应绑定 `127.0.0.1` 而非 `0.0.0.0`。
- namespace retention 当前 24h（与 dev server 一致）；生产/Staging 需按合规要求重新确认。
- 是否引入 Temporal Web UI 的独立配置（当前沿用端口 8233，未单独验证 UI 功能）。

---

## 5. 结论

B5.1 的核心目标（Dev SQL Persistence 最小实现 + History 持久化 + Server 重启保留 + Worker 恢复 + 全量审计测试）均已完成。遗留项中：
- 1.1 为既有测试基础设施条件，不阻塞 B5.1 验收，但建议后续修复。
- 2.x 为本批次已知边界，均已记录证据。
- 3.x / 4.x 属于 B5.2 或人工决策范围，本批次不实施。

完成后停止，等待人工验收；不得自动进入 B5.2。

# B4 实施报告：两层测试体系与稳定性验证

日期：2026-11-21
范围：Phase 2 - B4。完成后停止，等待人工验收；未进入 B5。

---

## 1. 目标与结论

B4 的目标是建立清晰、稳定、可重复执行的两层测试体系：快速层不依赖真实 Temporal；真实集成层使用真实 PostgreSQL 与真实 Temporal；35 分钟墙钟测试作为独立 opt-in 入口，不替代 time-skipping，也不替代真实执行证据。

本批次完成了测试基础设施调整、资源泄漏修复、单次全量非 Temporal 验证、真实 Temporal IT 启动与结果收集、Linux Python 3.12 固定环境准备与验证入口固化。最终各项状态以 `B4_TEST_RESULTS.md` 为准。

---

## 2. 实施前审查结论

1. **Worker 生命周期**
   - 4 个 Temporal 集成测试文件的 Worker task 均有 `shutdown()` / `wait_for()` / `cancel()` / `await` 清理路径。
   - 未发现 Worker 启动后未清理的明确证据。

2. **子进程清理**
   - `test_graph_integration.py` 中 SIGKILL 测试对 `proc` / `proc2` 有 `kill()`、`terminate()`、`wait()` 兜底。
   - 未发现子进程泄漏证据。

3. **SQLAlchemy engine 泄漏**
   - `test_invocation_recovery.py` 与 `test_replay_reliability.py` 创建 async engine 后未统一 `dispose()`。
   - 本批次已修复。

4. **pytest-timeout 未生效**
   - `pyproject.toml` 仅声明 `pytest-timeout` 依赖，但 `[tool.pytest.ini_options]` 中没有 timeout 配置，仓库内也没有 `@pytest.mark.timeout`。
   - 这是此前会话级卡住无法被自动打断的重大风险。
   - 本批次新增全局 `timeout = 300`、`timeout_method = "thread"`；Temporal IT 单独 `timeout(900)`；墙钟入口 `timeout(2700)`。

5. **快速层收集错误**
   - `pyproject.toml` 的 `pythonpath = ["src"]` 使 pytest 无法导入项目根目录下的 `scripts.*`。
   - 本批次改为 `pythonpath = ["src", "."]`。

---

## 3. 测试体系分层

### 3.1 快速层：本地核心测试

运行条件：

- 不要求真实 Temporal。
- 可选使用真实 PostgreSQL；未设置 `TEST_DATABASE_URL` 时，真实 PG 用例按 conftest 明确 SKIP。
- B0 的 `RUN_TEMPORAL_IT` 开关保持不变。
- `wall_clock_30m` 用例默认不进入快速层。

命令：

```powershell
pytest -q -m "not temporal_it and not wall_clock_30m"
```

### 3.2 真实集成层：Temporal IT

运行条件：

- 真实 PostgreSQL。
- 真实 Temporal Server。
- 显式设置 `RUN_TEMPORAL_IT=1`。
- B0 机制保持：未开启则 SKIP；开启后连接失败必须 FAIL，不得伪装 SKIP。

命令：

```powershell
$env:RUN_TEMPORAL_IT="1"
pytest -q -m "temporal_it"
```

覆盖由现有 4 个 Temporal IT 文件承担：Workflow 执行、Replay、Worker 重启与恢复、子 Agent 超时/取消、父 Run 关闭策略等。

### 3.3 35 分钟墙钟层

入口：`tests/test_long_run_wall_clock.py`

- marker：`wall_clock_30m`
- 默认 SKIP 原因：`RUN_WALL_CLOCK_30M=1` 未设置。
- 真实启用条件：
  - `RUN_WALL_CLOCK_30M=1`
  - `RUN_TEMPORAL_IT=1`
  - 真实 Temporal 可达
- 使用 Temporal 真实 timer，默认 `WALL_CLOCK_SECONDS=2100`。
- 超时 `pytest.mark.timeout(2700)`。
- Worker 生命周期使用显式 shutdown/cancel/await 清理。
- 入口存在不等于真实 35 分钟验证通过。

---

## 4. 稳定性与固定环境

### 4.1 Windows / Python 3.14

本机单次完整非 Temporal 测试已执行一次。结果见 `B4_TEST_RESULTS.md`。

### 4.2 Linux 固定环境

使用本地 Docker 容器 `b4-stable-test`：

- Python：3.12.16
- 依赖锁文件：`requirements-b4-linux.lock.txt`
- `pip check`：`No broken requirements found.`
- 已核实锁定依赖数量与容器安装数量一致。
- 运行脚本：`scripts/run_b4_linux_tests.ps1`

该环境不上传代码、不配置云端 CI，属于本地 Linux 固定版本验证。

---

## 5. 约束合规

- 未实施 Temporal SQL persistence 迁移。
- 未执行数据库 Schema 迁移。
- 未修改 B1/B2/B3 已验收业务逻辑。
- 未修改 Phase 1 ACL/JWT 安全逻辑。
- 未删除测试或放宽断言。
- 未初始化 Git、未配置 remote、未上传代码。
- 未启动未批准的 35 分钟真实墙钟任务。

---

## 6. 回滚说明

回滚仅限测试基础设施：

1. 还原 `pyproject.toml` 中 `timeout`、`timeout_method`、`pythonpath` 改动。
2. 移除 4 个 Temporal 测试文件中的 `pytest.mark.timeout(900)`。
3. 移除 `tests/test_invocation_recovery.py` 和 `tests/test_replay_reliability.py` 的 engine dispose 修复。
4. 删除 `tests/test_long_run_wall_clock.py`、`requirements-b4-linux.lock.txt`、`scripts/run_b4_linux_tests.ps1` 及 B4 文档。

无生产业务逻辑与数据库结构回滚风险。

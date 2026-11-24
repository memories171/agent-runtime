# B4.1 实施报告：测试基础设施遗留修复

日期：2026-11-21
范围：仅处理 B4 验收中遗留的测试基础设施问题，为 B5 建立可靠测试基础。未进入 B5。

---

## 1. 结论

B4.1 完成三项遗留修复：

1. Linux 固定环境中 `MIGRATION_DSN` 硬编码 `localhost:5433` 的问题已修复，并保留 Windows 默认行为。
2. pytest 运行退出码无法审计的问题已修复，新增可复用脚本，能在 PASS 与 FAIL 场景可靠捕获真实退出码。
3. Temporal IT 的 382 个 warnings 已按类别记录并判断来源；未发现属于 B4.1 新增的警告，未为消除警告重构业务代码。

---

## 2. MIGRATION_DSN 环境兼容性

### 2.1 审查证据

`tests/test_agent_tree_postgres.py` 原定义：

```python
MIGRATION_DSN = "postgresql://agent:agent@localhost:5433/agent_runtime"
```

使用位置：

- `test_physical_orphan_defensive_detection_with_fk_bypassed`
- `test_migration_repair_traces_true_root_at_34_and_40_levels`
- `test_migration_repair_reports_cycles_ambiguous_and_cross_tenant_links`

B4 Linux 容器失败的 3 项均卡在 `asyncpg.connect(MIGRATION_DSN)`。容器内 `localhost:5433` 指向容器自身，而 PostgreSQL 在宿主机，因此连接失败。

### 2.2 修复方式

新增环境变量优先级：

1. `TEST_MIGRATION_DSN`
2. `DATABASE_MIGRATION_URL`
3. 默认值：`postgresql://agent:agent@localhost:5433/agent_runtime`

同时兼容 `postgresql+asyncpg://` 前缀，自动转换为 asyncpg 可连接的 `postgresql://`。

该修复：

- 不改变断言。
- 不删除测试。
- 不增加 skip。
- 不执行 migration。
- 保持 Windows 默认行为不变。

### 2.3 验证

- Windows 相关 3 项：3 passed / 5 warnings / 22.88s / EXITCODE=0。
- Linux 相关 3 项：3 passed / 12.85s / EXITCODE=0。
- Linux 完整非 Temporal：219 passed / 25 deselected / 1 warning / 69.72s / EXITCODE=0。

---

## 3. Temporal IT 退出码审计修复

### 3.1 原问题

B4 中 Temporal IT 由后台进程 PID 32188 执行，日志完整但最终捕获文件记录为 `EXITCODE=`（空值）。

原等待脚本 `scripts/wait_b4_temporal.ps1` 通过 `GetProcessById()` 附加已运行进程，无法可靠取得由另一个 PowerShell 会话启动的进程退出码。因此 B4 只能记录 pytest 汇总结果，不能记录真实进程退出码。

### 3.2 新方案

新增 `scripts/run_pytest_audited.ps1`：

- 在脚本内部直接 `& python -m pytest @args` 启动 pytest。
- 使用 `$LASTEXITCODE` 捕获真实退出码。
- 写入：
  - START
  - END
  - ELAPSED
  - EXITCODE
  - COMMAND
  - MARKER
  - TEMPORAL_SWITCH
  - WALL_CLOCK_SWITCH
  - STDOUT
  - STDERR
- stdout/stderr 分别落盘。

### 3.3 验证结果

- PASS 场景：`tests/test_step_budget.py`，EXITCODE=0，13 passed。
- FAIL 场景：不存在的测试文件，EXITCODE=4，审计文件正常写入。

新脚本具备作为 B5 Temporal 验证入口的条件。

本批次未重新执行完整 Temporal IT；B4 原始 24 passed 结果继续保留，但其退出码审计不完整状态不变。

---

## 4. Temporal IT warnings 分类

统计来源：`b4_temporal_wait.out` 的完整 logs。

pytest 报告总数：382 warnings。

| 类别 | 日志中显式出现次数 | 来源判断 | 本批次是否新增 | 处理 |
|---|---:|---|---|---|
| `DeprecationWarning` / `asyncio.get_event_loop_policy` | 5 条显式条目；文件级聚合 376（224+28+44+80） | pytest-asyncio 0.26 / Python 3.14 环境 | 否 | 未改依赖、未重构 |
| `PydanticDeprecatedSince211` | 1 条显式类别条目 | `src/agent_runtime/context/assembly.py` 访问实例 `model_fields` | 否 | 业务代码问题，不在 B4.1 范围 |
| `PytestUnraisableExceptionWarning` | 2 条显式条目 | `graph/runner.py` 生成器关闭时在 workflow loop 外调用 activity | 否 | 列入遗留，未改业务 |
| SQLAlchemy `ResourceWarning` | 0 | 未出现 | 否 | 无需处理 |
| SQLAlchemy `SAWarning` | 0 | 未出现 | 否 | 无需处理 |
| `InsecureKeyLengthWarning` | Temporal 0；Linux full 1 | JWT 测试短 HMAC key | 否 | 既有测试警告，未改 |

说明：pytest 摘要按文件聚合 376 条，总数 382；其余警告未逐条展开为独立类别。所有可见类别均早于 B4.1 存在，没有证据表明由 B4.1 修改引入。

---

## 5. 约束合规

- 未进入 B5。
- 未配置 Temporal SQL persistence。
- 未修改数据库 Schema。
- 未执行 migration。
- 未改 Phase 1 ACL/JWT。
- 未删除测试或降低断言。
- 未初始化 Git、未配置 remote、未上传代码。
- 未启动 35 分钟真实墙钟任务。

---

## 6. 回滚说明

1. 将 `tests/test_agent_tree_postgres.py` 的 `MIGRATION_DSN` 恢复为原硬编码字符串。
2. 删除 `scripts/run_pytest_audited.ps1`、`scripts/classify_b4_warnings.py`。
3. 将 `scripts/run_b4_linux_tests.ps1` 恢复为 B4 版本。
4. 删除 B4.1 四份文档与审计日志文件。

无数据库 schema 与生产业务逻辑回滚风险。

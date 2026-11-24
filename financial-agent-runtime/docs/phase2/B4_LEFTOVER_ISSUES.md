# B4 遗留问题与风险

日期：2026-11-21
状态：B4 收尾中，等待人工验收；未进入 B5。

---

## 1. 35 分钟真实墙钟测试未执行

- `tests/test_long_run_wall_clock.py` 已提供真实入口。
- 默认 SKIP 原因明确：`RUN_WALL_CLOCK_30M` 未设置。
- 本批次未启动 35 分钟真实任务。
- 因此状态是 **NOT RUN**，入口存在不等于真实墙钟验证通过。
- time-skipping 没有被写成真实墙钟证据。

---

## 2. Windows / Python 3.14 环境非标准

- 本机环境为 Windows + Python 3.14.7。
- 项目声明 `requires-python >= 3.11`，但未将 Python 3.14 作为固定验证版本。
- Linux 固定环境采用本地 Docker `python:3.12-slim`，Python 3.12.16。
- 两个环境结果必须分别记录，不得混写为同一环境证据。

---

## 3. 单次全量与分批结果必须分离

- Windows 单次完整非 Temporal 测试已执行并记录。
- Linux 固定环境单次完整非 Temporal 测试以容器日志与退出码为准。
- 此前 B1/B2/B3 的分批结果只作为历史参考，不能替代 B4 单次全量结果。
- 若任一次执行未完成或日志不完整，必须记录 INCOMPLETE，不得用分批 PASS 替代。

---

## 4. Temporal IT 通过但存在警告与退出码捕获缺口

- 真实 Temporal IT 已显式设置 `RUN_TEMPORAL_IT=1` 并实际执行。
- 结果：`24 passed, 220 deselected, 382 warnings in 1028.79s (0:17:08)`，分类 PASS。
- B0 机制保持：未开启 → SKIP；开启后连接失败 → FAIL。
- 退出码捕获脚本记录为 `EXITCODE=`（空值），进程对象未保留真实 code；该缺口不影响 pytest 汇总判定，但属于审计证据缺口。
- 警告包括：
  - Pydantic `model_fields` deprecated。
  - `test_cancel_after_child_complete_does_not_overwrite` 的 `PytestUnraisableExceptionWarning`：关闭生成器时 `graph/runner.py` 在 workflow loop 外调用 activity 状态更新，抛 `_NotInWorkflowEventLoopError`。
- 这些警告未导致 FAIL，但建议后续批次单独审查生成器清理顺序。

---

## 4b. Linux 固定环境单次完整验证因网络配置失败

- Linux 容器验证实际执行，结果：`3 failed, 216 passed, 25 deselected, 1 warning in 55.08s`，`EXITCODE=1`。
- 3 个失败均在 `tests/test_agent_tree_postgres.py` 的 `asyncpg.connect(MIGRATION_DSN)`。
- `MIGRATION_DSN` 在测试文件中硬编码为 `postgresql://agent:agent@localhost:5433/agent_runtime`。
- 容器内 `localhost:5433` 指向容器自身，PostgreSQL 实际在宿主机，因此连接失败。
- 分类：环境配置问题，不是 B4 修改引入的代码缺陷。
- 本批次未修改该测试、未临时 skip、未放宽断言。

---

## 5. engine 泄漏修复范围有限

- 本批次只修复测试代码中发现的 SQLAlchemy engine 未 `dispose()` 问题。
- 未审查或修改生产代码中的 engine 生命周期，因为本批次目标是测试基础设施。
- 若后续全量测试仍出现连接增长，需另行审查生产服务和测试夹具边界。

---

## 6. session-scoped event loop 风险仍在

- `pyproject.toml` 仍保留：
  - `asyncio_default_fixture_loop_scope = "session"`
  - `asyncio_default_test_loop_scope = "session"`
- 当前 Worker 清理逻辑完整，未发现直接泄漏证据。
- 但 session loop 会放大任何未来新增的 pending task 风险。
- 本批次不改动该配置，避免影响既有异步测试语义。

---

## 7. pytest-timeout 使用 thread 方法

- 为兼容 Windows 与容器环境，配置了 `timeout_method = "thread"`。
- thread 方法能在卡住时中断测试，但栈信息可能与 `signal` 方法不同。
- 若后续需要更精确的卡死定位，可在 Linux 单独评估 `signal` 方法。

---

## 8. 本地容器不是生产环境

- `b4-stable-test` 是本地 Linux 固定依赖验证容器。
- 未配置云端 CI，未上传代码。
- 未实施 Temporal SQL persistence 迁移。
- 该容器结果不能证明生产持久化能力。

---

## 9. 停止条件

B4 完成后停止，等待人工验收。未经人工明确批准，不进入 B5，不实施 Temporal 持久化迁移，不扩大测试范围。

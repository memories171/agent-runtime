# B6 实施报告：35 分钟真实墙钟验证

日期：2026-11-22
状态：**墙钟 PASS / replay 缺陷已修复并重跑验证**。B6 是 Phase 2 中长期标记为 NOT RUN 的真实 35 分钟验证，本批次完成执行并修复了验证链上的一个测试缺陷。

---

## 1. 目标与结果

目标：在真实 Temporal（非 time-skipping）上运行 35 分钟 wall-clock workflow，验证 `elapsed >= 2100s`、结果正确，并对真实 history 执行 replay。

结果：

| 项 | 状态 | 证据 |
|---|---|---|
| 真实 35 分钟墙钟 | PASS | 首轮 `2104.17s`（0:35:04），workflow `4c2e3e64` 在 SQL-backed Temporal 上真实 Completed |
| History 持久化 | PASS | `temporal.history_node` 写入（46 行） |
| Replay | 首轮无效 → 已修复并独立验证 | 首轮 `Replayer.replay_workflow` 未 await；修复后 `REPLAY_OK events=10` |
| 审计元数据 | PASS | `b6_wall_clock_35m.meta.out` 记录 START/END/ELAPSED/EXITCODE/开关/命令 |
| 修复后单次完整重跑 | 见 `B6_TEST_RESULTS.md` 第 5 节 | 保证单次运行同时具备墙钟与 replay 证据 |

---

## 2. 关键发现：replay 证据缺陷

首轮 pytest 报 `1 passed`，但输出含 `RuntimeWarning: coroutine 'Replayer.replay_workflow' was never awaited`。由于 `Replayer.replay_workflow` 是协程函数，未 await 意味着 replay 从未执行——这是"假 PASS"，属于必须修复的测试基础设施问题。

修复：`tests/test_long_run_wall_clock.py` 第 97 行改为 `await Replayer(...).replay_workflow(history)`。没有放宽断言，没有 time-skip，没有改生产代码。

---

## 3. 失败/中断记录（诚实）

- 第一次重跑因命令空闲超时被终止，pytest 进程被杀；其 workflow `761a9c4d` 在 Temporal 中继续到 TimedOut（无害遗留）。
- 改用 `Start-Process -WindowStyle Hidden` 完全后台启动后重跑稳定运行。
- 期间业务 `postgres`/`keycloak` 被管理命令停止导致 API 502；已恢复，与 B6 无关（B6 仅依赖 Temporal）。

---

## 4. 并行回归

B6 等待期间并行运行非 Temporal 全量回归：`221 passed, 25 deselected`（含本轮新增的 2 个 MCP 超时测试），EXITCODE=0（`b6_concurrent_regression.meta.out`）。证明 B6 修复与先前 MCP 修复未引入回归。

---

## 5. 边界

- 未进入 B5.2 / B7。
- 未修改 `SECONDS=2100`，未降低等待强度。
- 未将 wall-clock 测试纳入默认快速层；仍需 `RUN_WALL_CLOCK_30M=1` opt-in。
- 未改任何 `src/agent_runtime/**` 生产代码。

完成后停止，等待人工验收；下一步（B5.2 / B7 / 治理）需单独确认。

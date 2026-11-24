# B6 测试结果：35 分钟真实墙钟验证

日期：2026-11-22
说明：本报告严格区分 PASS / FAIL / SKIP / NOT RUN。B6 使用真实 Temporal timer（`workflow.sleep(2100)`），等待真实墙钟时间，然后 replay workflow history；绝不使用 time-skipping 环境的证据代替。全部通过 `scripts/run_pytest_audited.ps1` 运行并保留审计文件。

> 最终重跑结果见第 5 节。第 2/3 节记录首次运行与本轮发现并修复的 replay 缺陷。

---

## 1. 环境与入口

| 项 | 值 |
|---|---|
| 测试文件 | `tests/test_long_run_wall_clock.py` |
| 用例 | `test_real_35_minute_wall_clock_workflow_and_replay`（1 个） |
| 开启条件 | `RUN_WALL_CLOCK_30M=1` 且 `RUN_TEMPORAL_IT=1`（由 `-WallClock` 开关设置） |
| 真实等待 | `WALL_CLOCK_SECONDS=2100`（35 分钟） |
| 断言 | `elapsed >= 2100`、`result == {"slept_seconds": 2100}`、history replay |
| Temporal | SQL-backed Server `temporalio/server@sha256:7bf69188...`，`PersistenceStore/VisibilityStore=postgres12` |
| Python | 3.14.7（宿主机） |

审计脚本用法：

```powershell
& ./scripts/run_pytest_audited.ps1 -LogPrefix "b6_wall_clock_35m" -WallClock `
  -PytestArgs @("tests/test_long_run_wall_clock.py")
```

---

## 2. 首次运行（发现 replay 缺陷）

审计文件：`b6_wall_clock_35m.{out,err,meta.out}`（首轮，已被后续重跑覆盖）

元数据：

- START：`2026-11-22T21:18:47.7792347+08:00`
- END：`2026-11-22T21:53:56.4624763+08:00`
- ELAPSED：`00:35:08.6832416`
- EXITCODE：`0`
- WALL_CLOCK_SWITCH：`True`

pytest：`1 passed, 6 warnings in 2104.17s (0:35:04)`

墙钟证明：

- workflow `b4-wall-clock-4c2e3e64-f294-4ef2-ac31-8e64be7b423c` 在 SQL-backed Temporal 上真实运行 35 分钟后 Completed。
- SQL `temporal.history_node` 在测试期间写入（46 行），证明 history 落在 PostgreSQL。
- `elapsed >= SECONDS` 与 `result == {"slept_seconds": 2100}` 均通过。

### 缺陷

首轮输出包含 warning：

```text
RuntimeWarning: coroutine 'Replayer.replay_workflow' was never awaited
Replayer(workflows=[WallClockTimerWorkflow]).replay_workflow(history)
```

即第 97 行的 `Replayer.replay_workflow(history)` 是协程却未 `await`，**replay 实际未执行**。虽然 pytest 报 PASS，但 replay 证据无效。

---

## 3. 修复与独立验证

修复（测试代码）：

```python
history = await handle.fetch_history()
await Replayer(workflows=[WallClockTimerWorkflow]).replay_workflow(history)
```

- `read_lints`：0 diagnostics。
- SDK 校验：`inspect.iscoroutinefunction(Replayer.replay_workflow) == True`，确认必须 `await`。
- 独立 replay 验证：对首轮已完成的真实 history 使用测试模块中的 `WallClockTimerWorkflow` 执行 `await Replayer(...).replay_workflow(h)`，结果 `REPLAY_OK events=10`（10 个事件全部 replay 成功）。

因此 replay 逻辑的正确性已被单独证明；为使 B6 在**单次审计运行**中同时具备墙钟与 replay 的完整证据，进行了第 5 节的重跑。

---

## 4. 边界与真实时间问题

- 首次运行后尝试重跑，第一次重跑因命令空闲超时被终止（pytest 进程被杀，但其 workflow `761a9c4d` 继续在 Temporal 中运行至 TimedOut，属无害遗留）。
- 最终改用 `Start-Process -WindowStyle Hidden` 完全后台启动，重跑得以持续到完成。
- 期间业务 `postgres`/`keycloak` 曾被管理命令停止导致 API 502；已恢复，与 B6 无关（B6 只用 Temporal）。

---

## 5. 最终重跑（修复后单次完整运行）

审计文件：`b6_wall_clock_35m.{out,err,meta.out}`（重跑后）

元数据（`b6_wall_clock_35m.meta.out`）：

- START：`2026-11-22T22:40:58.4793661+08:00`
- END：`2026-11-22T23:17:04.6026439+08:00`
- ELAPSED：`00:36:06.1232778`
- EXITCODE：`0`
- COMMAND：`python -m pytest -q --tb=short tests/test_long_run_wall_clock.py`
- WALL_CLOCK_SWITCH：`True`
- TEMPORAL_SWITCH：`False`（由 `-WallClock` 自动置 `RUN_TEMPORAL_IT=1`）

pytest：`1 passed, 5 warnings in 2162.63s (0:36:02)`

关键确认：

- 输出中**不再有** `RuntimeWarning: coroutine 'Replayer.replay_workflow' was never awaited`；replay 在单次运行中真实执行。
- 5 个 warning 全是 pytest-asyncio 的 `asyncio.get/set_event_loop_policy` DeprecationWarning（Python 3.14 弃用），与 B6 无关。
- workflow `b4-wall-clock-3d33ba6d-865c-47f2-8fff-b2aadb6b2876` 真实运行 36 分钟后 Completed。
- stderr 为空。

---

## 6. 结论

- **B6：PASS**。真实 35 分钟墙钟（2162.63s ≥ 2100s）+ 真实 history replay 在**单次审计运行**中全部通过，EXITCODE=0。
- replay 缺陷已修复；首轮的假 PASS 已被本轮干净结果替代。
- 并行非 Temporal 回归 `221 passed`（`b6_concurrent_regression.meta.out`，EXITCODE=0）证明修复未引入回归。

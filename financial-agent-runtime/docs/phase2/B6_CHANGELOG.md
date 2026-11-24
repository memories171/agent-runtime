# B6 变更清单

日期：2026-11-22
范围：35 分钟真实墙钟测试执行与 replay 缺陷修复。

---

## 1. 变更文件

| 文件 | 类型 | 说明 |
|---|---|---|
| `tests/test_long_run_wall_clock.py` | 修改 | 第 97 行 `Replayer.replay_workflow(history)` 改为 `await Replayer(...).replay_workflow(history)`，修复 replay 协程未执行缺陷 |
| `docs/phase2/B6_TEST_RESULTS.md` | 新增 | B6 测试结果与 replay 缺陷分析 |
| `docs/phase2/B6_CHANGELOG.md` | 新增 | 本清单 |
| `b6_wall_clock_35m.{out,err,meta.out}` | 新增（审计产物） | 审计脚本生成，保留 START/END/ELAPSED/EXITCODE/命令/开关/日志路径 |

未新增/修改生产代码（`src/agent_runtime/**` 0 改动）。未改数据库 schema、未执行迁移。

---

## 2. 为什么改测试代码

B6 的目的是证明"真实 35 分钟墙钟 + history replay"。首次运行时 pytest 报 PASS，但输出含：

```text
RuntimeWarning: coroutine 'Replayer.replay_workflow' was never awaited
```

`Replayer.replay_workflow` 在 temporalio SDK 中是协程函数（`inspect.iscoroutinefunction(...) == True`）。未 `await` 时 replay 从未执行，因此"replay 证据"是假的。这属于明确的测试基础设施缺陷，修复不改变测试断言强度，只让原本应执行的 replay 真正执行。

---

## 3. 未做的事

- 未改 `SECONDS=2100`，B6 仍是真实 35 分钟（无 time-skip）。
- 未将 wall-clock 测试纳入默认快速层（`RUN_WALL_CLOCK_30M` 仍是 opt-in）。
- 未改 B0–B5.1 的任何实现或文档结论。
- 未进入 B5.2 / B7。

---

## 4. 验证产物

- 首轮：`1 passed, 6 warnings in 2104.17s`，EXITCODE=0，workflow 真实 Completed，SQL history 落库；随后发现 replay 未 await。
- 独立验证：对真实 history `await Replayer(...).replay_workflow(h)` → `REPLAY_OK events=10`。
- 最终重跑：见 `B6_TEST_RESULTS.md` 第 5 节与 `b6_wall_clock_35m.meta.out`。

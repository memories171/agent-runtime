# B6 遗留问题

日期：2026-11-22
说明：记录 B6（35 分钟真实墙钟验证）完成后的遗留项与边界。

---

## 1. 已解决

- 首轮 replay 未 await 的测试缺陷（`RuntimeWarning: coroutine was never awaited`）：已修复为 `await Replayer(...).replay_workflow(history)`，并用真实 history 独立验证 replay 成功（`REPLAY_OK events=10`）。

---

## 2. 遗留 / 边界

1. **首次重跑被中断的 workflow**：`b4-wall-clock-761a9c4d...` 因命令空闲超时导致 pytest 被杀，workflow 在 Temporal 中继续至 TimedOut。属无害遗留数据，保留在 `temporal` 命名空间；如需清理可单独归档，但不影响任何验收结论。
2. **B6 仍是 opt-in**：`RUN_WALL_CLOCK_30M=1` + `RUN_TEMPORAL_IT=1` 才执行；默认快速层不包含 35 分钟测试。这是有意设计，避免日常 CI 被 35 分钟阻塞。
3. **墙钟测试耗时**：单次约 35 分钟，B6 不作为高频回归；建议仅在里程碑验收/持久化切换后运行。
4. **业务服务需人工关注**：本轮期间业务 `postgres`/`keycloak` 被管理命令停止导致 API 502（已手动恢复）。这是环境运维问题，非代码缺陷；B6 只依赖 Temporal。
5. **未进入后续阶段**：B5.2、B7、生产上线治理均未在本批次实施。

---

## 3. 结论

B6 的核心验收目标已达成；剩余均为环境/流程层面的注意事项，不影响"真实 35 分钟墙钟 + replay"这一结论。等待人工验收后再决定是否进入下一阶段。

# B7 遗留问题

日期：2026-11-23
说明：记录 Checkpoint Restore 最小实现的遗留项与诚实边界。

---

## 1. 测试覆盖缺口

1. **API 级真实端到端未跑（NOT RUN）**：没有完成 "持久化一个真实 checkpoint → 调用 `POST /runs/{run_id}/restore` → 新 workflow 注入状态并复用已确认工具 → 断言无重复副作用" 的全链路测试。原因：需要完整 DB fixture（active tenant membership、真实 RunRecord、已持久化 checkpoint 与对象存储）。当前由契约/注入单测 + 静态守卫 + 全量回归覆盖核心逻辑。
2. **`record_checkpoint_restore_activity` 无独立单测**：其成员资格校验与幂等逻辑未经 pytest 覆盖；仅通过代码审查确认。
3. **`inject_restore_state` 的 workflow.patched 分支未做 history replay 实测**：静态守卫确认存在，但未构造"含 restore_contract 的历史"做 Replayer 实测。

---

## 2. 设计边界（最小实现的正常限制）

1. **token 拆分丢失**：快照 `budget_state` 只存合并的 `used_tokens`（input+output），恢复时统一放入 `token_input` 作为合计；原始 input/output 拆分不可恢复。
2. **confirmed key 的 index 回退**：无 `call_id` 的 tool_result 用 `idx-N` 作为 key；快照只保留最近 16 条 tool_result，index 在同一快照内稳定，但跨快照语义弱。建议后续在 checkpoint builder 中为每个 tool_result 写入稳定 `call_id`。
3. **最近 16 条限制**：快照的 `confirmed_tool_results` 来自 `history` 中最近 16 条 tool_result；更早的已确认工具调用无法复用，可能被重新执行。生产使用前需评估保留窗口是否足够。
4. **不恢复 workflow 内部控制态**：pause/resume/cancel signals、child registry、pending signals 不注入（由新 workflow 重建）。这是有意设计，但意味着恢复点不是"精确续跑"，而是"业务状态种子 + 新执行"。
5. **checkpoint 版本契约**：`RESTORE_CONTRACT_VERSION=1.0`；未来 snapshot schema 变更需同步升级契约并测试旧契约兼容。
6. **无跨环境 restore**：不能把 staging checkpoint 恢复到 dev 或反之（tenant/环境绑定）。

---

## 3. 上线前建议（供治理阶段处理）

- 补 API 级端到端 restore 测试与 DB fixture。
- 为 `run:restore` scope 配 RBAC 审计与限流。
- 明确 restore 的运维 SOP（何时用、谁批准、如何对账）。
- 稳定 tool_result call_id，扩大 confirmed 保留窗口。

---

## 4. 结论

B7 核心能力已实现且回归通过。遗留项集中在"更深层测试覆盖"与"生产级边界强化"，不影响本批次"最小实现 PASS"的结论。按用户指示，下一步进入上线治理。

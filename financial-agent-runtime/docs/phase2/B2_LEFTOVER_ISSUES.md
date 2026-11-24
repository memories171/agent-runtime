# B2 遗留问题清单

日期：2026-11-21

---

## 1. 环境/测试基础设施

1. **全量单次 192 项未通过**（NOT RUN / 会话卡住）：本环境（Windows/Python 3.14）全量非 Temporal 套件在约 37% 卡住，无 PG 活动、CPU 近 0。分批独立进程全部通过（0 FAIL）。需在稳定 CI（Linux + 固定依赖 + pytest-timeout线程模式）取得单次全量通过证据。未伪造 PASS、未删除测试。
2. **测试数据污染导致偶发 FAIL**：多次 kill pytest 进程后，固定 `delegation_key` / 固定 tenant 的测试（orchestration_rls、agent_tree_postgres）因残留行出现 `ON CONFLICT DO NOTHING` 导致断言失败。清理后重跑 PASS。建议测试用随机 key 或增强 fixture teardown（属测试改进，未在本批次做）。
3. **真实 Temporal 24 用例本批次 NOT RUN**：引用 B0 证据（24 passed / 19m01s）。未改 workflow 内部逻辑，风险低；建议 B4 nightly 重跑。

---

## 2. 设计权衡（B2 决策边界）

4. **seconds_per_step=60 是保守估算**：默认 2h（7200s）任务需 120 步，会从 50 自动上调；24h 任务需 1440 步，返回 HTTP 400（无法用 200 步覆盖）。如需支持更长 duration，需调整 `estimated_seconds_per_step` 或提高硬上限（需重新人工批准）。
5. **自动上调是"预算"而非"保证"**：`effective_steps` 是允许的上限，不保证任务一定跑满 duration。用户显式设置更大值（≤200）仍被尊重。
6. **Graph 的 `max_node_steps` 与 run 级 `max_steps` 是不同层级**：本批次让 `graph.limits.max_node_steps` 生效（默认 8，无 ge/le 契约约束）。若需给 Graph 也做 duration 联动，属后续增强，未在 B2 范围。

---

## 3. B1 遗留（延续）

7. `record_worker_recovery_activity` 仍为 dead code；workflow 级恢复审计缺 activity 证据（需 `workflow.patched`，B2+ 未做）。
8. 无 `WorkflowReplayer` 旧 history replay 自动化验证。
9. Activity retry / 恢复失败审计缺 pytest（仅手工脚本）。

---

## 4. 不在 B2 范围（后续批次）

10. UNKNOWN 自动对账（B3，需先分析现有字段充分性，未批准迁移）。
11. Temporal SQL 持久化迁移（B5，需单独方案与人工批准）。
12. 真实 30 分钟墙钟测试（B6）。

---

**结论**：B2 代码改动完整、测试通过（分批）；主要遗留是测试环境单次全量稳定性，属基础设施问题，需在 CI 阶段解决。未发现 B2 引入的功能缺陷。

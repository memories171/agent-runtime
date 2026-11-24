# B7 资源使用与成本

日期：2026-11-23
范围：Checkpoint Restore 最小实现的代码/运行/测试资源。

---

## 1. 代码规模

| 文件 | 说明 |
|---|---|
| `checkpoint/restore_contract.py`（新增） | ~130 行 |
| `tests/test_checkpoint_restore.py`（新增） | ~130 行 |
| `contracts.py` / `api.py` / `workflow.py` / `activities.py` / `worker.py` | 局部修改，合计新增约 190 行 |

未新增依赖、未新增镜像、未新增服务。

---

## 2. 运行态开销

- `WorkflowInput.restore_contract`：随 workflow input 进入 Temporal payload；包含完整 `AgentStateSnapshot`（history/tool_calls/llm_calls 等）。正常 Run（`restore_contract=None`）无额外开销。
- `record_checkpoint_restore_activity`：每次恢复写 1 条 `CHECKPOINT_RESTORE_STARTED` 事件（幂等，重复恢复同一 checkpoint 不重复写）。
- `confirmed_tool_results` 复用分支：恢复 workflow 中每个已确认 tool_call 少一次外部 activity 调用，节省副作用与工具成本。

整体无常驻资源增加；镜像重建后 api/worker 容器内存与之前相当。

---

## 3. 测试耗时

| 批次 | 耗时 |
|---|---|
| B7 单元（17 tests） | 5.36s |
| 全量非 Temporal 回归（227 tests） | 04:03 |
| 镜像重建 api/worker | < 1 分钟（本地缓存） |

---

## 4. 成本结论

B7 是纯代码能力，无新增基础设施成本。恢复时复用已确认工具结果反而**降低**重复副作用的潜在成本。主要成本是设计和测试（已完成），以及后续若做 API 级真实端到端测试所需的 DB fixture 搭建。

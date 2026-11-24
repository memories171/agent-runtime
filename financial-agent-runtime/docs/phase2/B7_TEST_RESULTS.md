# B7 测试结果

日期：2026-11-23
说明：严格区分 PASS / FAIL / SKIP / NOT RUN。pytest 均经 `scripts/run_pytest_audited.ps1`。

---

## 1. 汇总

| 验证项 | 结果 | 证据 |
|---|---|---|
| B7 契约/注入/幂等单元测试 | PASS | `17 passed`，EXITCODE=0 |
| 全量非 Temporal 回归 | PASS | `227 passed, 25 deselected`，EXITCODE=0 |
| API restore 路由注册 | PASS | OpenAPI `paths['/runs/{run_id}/restore']` 存在，POST + requestBody |
| 运行容器加载新代码 | PASS | api 容器 restore 路由存在；worker 连接 Temporal(7233)+PG(5432) |
| 服务健康 | PASS | API `/health` 200；Temporal `postgres12`/`postgres12` |
| API 级真实 checkpoint→restore→workflow 端到端 | **NOT RUN** | 需完整 DB fixture（租户成员、RunRecord、已持久化 checkpoint），最小实现以单测+静态守卫+回归覆盖 |

---

## 2. 单元测试

审计文件：`b7_restore_unit.{out,err,meta.out}`

```powershell
& ./scripts/run_pytest_audited.ps1 -LogPrefix "b7_restore_unit" `
  -PytestArgs @("tests/test_checkpoint_restore.py","tests/test_checkpoint_snapshot.py","tests/test_replay_reliability.py")
```

- START `10:05:07`，END `10:05:15`，ELAPSED `00:00:07`，EXITCODE `0`
- `17 passed, 5 warnings`

6 个 B7 新用例（`test_checkpoint_restore.py`）：
1. 契约版本与来源字段正确（v1.0 / checkpoint_id / state_digest）
2. `confirmed_tool_results` 排除"CONFIRMED 但 result=None"边界（`demo.noresult:tc-2` 不在映射中）
3. 状态注入只恢复业务字段（新 run_id 保留、caller user 保留、组织/角色能力带入、token 合计恢复）
4. RestoreContract Pydantic 往返一致
5. 静态守卫：`workflow.patched` 存在（replay 兼容）
6. 静态守卫：`_restored_confirmed_keys` 与复用分支、`TOOL_RESULT_RESTORED` 事件存在

5 warnings 为 Python 3.14 / pytest-asyncio 的 `get/set_event_loop_policy` 弃用，与 B7 无关。

---

## 3. 全量回归

审计文件：`b7_full_regression.{out,err,meta.out}`

- START `10:06:32`，END `10:10:38`，ELAPSED `00:04:06`，EXITCODE `0`
- `227 passed, 25 deselected, 6 warnings in 243.41s`
- 227 = 221（B5.2/B6 基线）+ 6 新 B7 用例；无失败、无新增 skip。

---

## 4. 运行态验证

- API 容器（重建后）：`'/runs/{run_id}/restore' in app.openapi()['paths'] == True`
- Worker 容器 TCP：`0x1C41`(7233 Temporal) 与 `0x1538`(5432 PG) 均为 ESTABLISHED
- API `/health` = 200；Temporal Dev `PersistenceStore/VisibilityStore = postgres12/postgres12`
- 使用 `docker compose -f docker-compose.yml up -d --no-deps --force-recreate api worker` 重建，**未影响** SQL Temporal 服务

---

## 5. 结论

B7 最小实现的契约、注入、副作用幂等、审计、路由、回归均通过。唯一未做的是 API 级真实端到端（标记 NOT RUN，原因见第 1 节），不影响"B7 逻辑正确且无回归"的结论。等待人工验收。

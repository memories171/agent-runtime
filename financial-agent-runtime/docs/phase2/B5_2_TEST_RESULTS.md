# B5.2 测试结果

日期：2026-11-23
说明：严格区分 PASS / FAIL / SKIP / NOT RUN。所有 pytest 通过 `scripts/run_pytest_audited.ps1`，审计文件保留。

---

## 1. 汇总

| 验证项 | 结果 | 证据 |
|---|---|---|
| Staging 集群启动 / Health | PASS | 4 容器 healthy / completed；`cluster describe` postgres12 |
| Namespace 7 天 retention | PASS | `Config.WorkflowExecutionRetentionTtl = 168h0m0s` |
| 双环境隔离 | PASS | staging 7 workflow vs dev 2 workflow；PG 实例/卷/端口独立 |
| Staging Temporal IT | PASS | `7 passed` on `localhost:7234`，EXITCODE=0 |
| 备份/恢复演练 | PASS | `RESULT=True`，全部行数/MD5 匹配，隔离库已清理 |
| Replay 验证 | PASS | `REPLAY_OK`，1063 事件确定性 replay |
| 回滚演练 | PASS | staging 7→7，dev 2→2，stores 保持 postgres12 |
| 非 Temporal 全量回归 | PASS | `221 passed, 25 deselected`，EXITCODE=0 |

---

## 2. Staging Temporal IT

审计文件：`b52_staging_temporal_it.{out,err,meta.out}`

```powershell
$env:TEMPORAL_ADDRESS="localhost:7234"
& ./scripts/run_pytest_audited.ps1 -LogPrefix "b52_staging_temporal_it" -Temporal `
  -PytestArgs @("tests/test_graph_integration.py")
```

- START `2026-11-23T09:21:43.3817394+08:00`，END `09:25:51+08:00`，ELAPSED `00:04:08`，EXITCODE `0`
- `7 passed, 229 warnings in 248.43s (0:04:08)`
- 包含 worker crash recovery；workflow 落在 staging 自己的 PG 实例（非 dev）

---

## 3. 备份/恢复演练

日志：`b52_backup_restore.log`（脚本 stdout 原样保留）

```
BACKUP_BYTES=401284
BACKUP_SHA256=CD2D4E...9639A2
DUMP_SECONDS=1.03  RESTORE_SECONDS=0.58
TABLE=namespaces        SOURCE_ROWS=2    RESTORE_ROWS=2    MATCH=True
TABLE=executions        SOURCE_ROWS=9    RESTORE_ROWS=9    MATCH=True
TABLE=history_node      SOURCE_ROWS=1617 RESTORE_ROWS=1617 MATCH=True
VIS_BACKUP_BYTES=46465  VIS_RESTORE_EXIT=0
TABLE=executions_visibility SOURCE_ROWS=9 RESTORE_ROWS=9 MATCH=True
NAMESPACE_DEFAULT_CONFIG_MD5_SOURCE=1998a4d7... RESTORE=1998a4d7... MATCH=True
TEST_DB_DROPPED=True VIS_TEST_DB_DROPPED=True
RESULT=True  ELAPSED_SECONDS=5.86
```

说明：备份文件写入 `backups/` 并保留；恢复到隔离库后校验行数与 namespace 配置字节 MD5，运行库从未被覆盖；隔离库演练后删除。

---

## 4. Replay 验证

命令：`python scripts/verify_staging_replay.py`

```
REPLAY_OK workflow_id=graph-it-89c377bc-a052-46b1-be1a-6d62f7973840 run_id=01a20601-f377-7a49-ae7d-016ac8b71f7b events=1063
```

说明：从 staging 真实 Completed workflow 拉取 history，用 `Replayer` + `AgentRunWorkflow` 确定性 replay，1063 事件全部通过。

---

## 5. 回滚演练

命令序列见 `B5_2_ROLLBACK_GUIDE.md`。结果：

| 指标 | 停止前 | 停/启后 | 结论 |
|---|---|---|---|
| staging workflow 数 | 7 | 7 | History 保留 |
| dev workflow 数 | 2 | 2 | Dev 完全不受影响 |
| staging PersistenceStore | postgres12 | postgres12 | Store 未降级 |

---

## 6. 最终回归

审计文件：`b52_final_regression.{out,err,meta.out}`

- START `09:50:03`，END `09:54:12`，ELAPSED `00:04:09`，EXITCODE `0`
- `221 passed, 25 deselected, 6 warnings in 246.37s (0:04:06)`
- 与 B6 回归一致，无新增失败。6 warnings 为既有的 JWT 短 key / asyncio 弃用。

---

## 7. 结论

B5.2 本地最小实现全部验证通过，无 FAIL、无临时 skip。生产 HA/TLS/密钥管理未实施（NOT RUN，超出本地最小范围）。

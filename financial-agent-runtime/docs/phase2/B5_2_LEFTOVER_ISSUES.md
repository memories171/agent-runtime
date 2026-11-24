# B5.2 遗留问题

日期：2026-11-23
说明：记录本地 Staging 最小实现完成后的遗留项与边界。

---

## 1. 已知边界（本地最小实现的正常范围外）

1. **生产 HA / 多节点 / TLS / 密钥管理**：未实施。本批次是单机本地验证，不等于生产 Staging。
2. **跨主机备份恢复**：演练恢复到同一 PG 实例的隔离库并清理；未做跨主机/跨环境恢复。
3. **覆盖式灾难恢复**：`backup_restore_drill.ps1` 刻意恢复到隔离库（不覆盖运行库），真实灾难恢复的覆盖流程未实施。
4. **Staging 无常驻 worker**：为避免真实副作用，staging 只用 IT 的进程内 worker + replay 验证。生产 Staging 需要独立 worker 配置。
5. **备份保留策略**：`backups/` 下会累积带时间戳的 dump，未做自动清理；长期运行需加保留策略。
6. **`.env` 冗余变量**：`TEMPORAL_STAGING_SQL_DB` / `TEMPORAL_STAGING_SQL_VISIBILITY_DB` 保留但 compose 不再使用（compose 固定 `temporal`/`temporal_visibility`），可后续清理。

---

## 2. 环境运维问题（非代码缺陷）

- 本批次期间业务 `postgres`/`keycloak` 多次被管理命令停止，导致 API 502 与 DNS 解析失败；每次都通过 `docker start` + 重启 api 恢复。这是环境层面的反复问题，建议单独排查（是否有外部脚本/定时任务停机）。
- 一次回归命令因 `Remove-Item Env:TEMPORAL_ADDRESS` 在变量不存在时抛错导致整条命令中止（Safe-Delete 保护），去掉该行后重跑成功。非测试失败。

---

## 3. 未进入的范围

- B7 完整 Checkpoint Restore（Phase 2 决策为默认暂缓，需单独授权）。
- README 中的生产上线治理项（数据治理、账务隔离、双人控制、合规边界等约 12 项）。
- Git 初始化 / lock 文件治理。

---

## 4. 结论

B5.2 本地最小实现的核心目标（隔离 Staging、双环境、备份恢复、Replay、回滚）全部 PASS。剩余为生产化/治理层面的未完成项与环境运维问题，不影响本批次验收结论。等待人工验收后再决定下一步。

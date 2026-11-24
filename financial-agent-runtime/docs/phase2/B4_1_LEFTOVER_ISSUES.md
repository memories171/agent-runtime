# B4.1 遗留问题与风险

日期：2026-11-21
状态：B4.1 完成，等待人工验收；未进入 B5。

---

## 1. 新增审计脚本尚未用于完整 Temporal IT

- `scripts/run_pytest_audited.ps1` 已通过 PASS / FAIL 自检，能捕获真实 `$LASTEXITCODE`。
- B4.1 未重新执行完整 Temporal IT。
- B4 原始 Temporal IT 结果仍是 `24 passed`，但退出码审计不完整。
- 后续 B5 必须使用新审计脚本执行完整 Temporal IT，不应再使用 `GetProcessById()` 附加已有进程的方式。

---

## 2. 382 个 Temporal warnings 未消除

- 全部 warnings 早于 B4.1 存在，未发现 B4.1 新增类别。
- 主要类别：
  - pytest-asyncio / asyncio DeprecationWarning。
  - Pydantic `model_fields` deprecated。
  - `PytestUnraisableExceptionWarning`（graph runner 生成器关闭时状态更新）。
- B4.1 未为消除警告重构业务代码，符合边界要求。
- 建议后续单独批次评估生成器清理与 Pydantic deprecation，不纳入 B5 默认范围。

---

## 3. Linux 固定环境依赖显式注入迁移 DSN

- 测试文件已保留 Windows 默认值兼容。
- Linux runner 必须注入 `TEST_MIGRATION_DSN=...host.docker.internal...`。
- 如果直接在容器内运行而不注入该变量，相关迁移测试仍会失败。
- 这是容器网络配置差异，不是代码断言问题。

---

## 4. session-scoped event loop 风险仍在

- `pyproject.toml` 仍保留 session 级 asyncio loop 配置。
- B4.1 未改动，避免影响既有测试语义。
- 后续如新增后台任务，应继续检查 pending task 清理。

---

## 5. 35 分钟真实墙钟验证仍未执行

- 入口已存在。
- 本批次未启动，状态 NOT RUN。
- 入口存在不等于真实墙钟验证通过。

---

## 6. B4 与 B4.1 结果必须分开记录

- B4 Windows 单次非 Temporal：PASS。
- B4 Linux 单次非 Temporal：原 FAIL（3 项环境 DSN 问题）。
- B4.1 Linux 单次非 Temporal：PASS（显式注入迁移 DSN 后）。
- 三者不能混写为同一结果。

---

## 7. 停止条件

B4.1 完成后停止，等待人工验收。未经人工明确批准，不进入 B5，不配置 Temporal SQL persistence，不执行 migration。

# 上线治理变更清单

日期：2026-11-23

## 新增文件

| 文件 | 说明 |
|---|---|
| `docs/governance/PRODUCTION_GOVERNANCE.md` | 12 项治理策略与门禁判定 |
| `docs/governance/DATA_CLASSIFICATION.md` | 数据分类表 |
| `scripts/check_production_governance.ps1` | 治理自动检查脚本 |
| `../../.github/workflows/ci.yml` | 最小 CI 骨架（相对 backend 根） |
| `../../SECURITY.md` | 安全策略 |
| `../../.github/CODEOWNERS` | 代码归属占位 |
| `../../.gitignore` | 根 gitignore |
| `requirements.lock.txt` | Python 依赖 lock（319 包，当前环境 freeze） |
| `docs/phase2/GOV_*.md` | 治理报告/清单/测试/遗留 |

## 修改文件

| 文件 | 说明 |
|---|---|
| `.env.example` | 追加生产治理配置占位段（JWT claims、快照加密、MCP TLS、监测审批、OTEL） |
| `src/agent_runtime/config.py` | 新增 `mcp_tls_required`、`finance_monitoring_production_rules`、`finance_monitoring_approval_ref`、`monitoring_rules_approval_ref`；`validate_runtime_config()` 生产监测规则审批门禁 |

## 环境变更

- 项目根执行 `git init`（空仓库，未 add/commit）。
- 生成 `financial-agent-runtime/requirements.lock.txt`。

## 未修改

- B0–B7 的实现、测试、部署配置。
- 前端代码。
- `scripts/run_pytest_audited.ps1`（治理检查脚本独立）。

## 实施中修复

- 治理检查脚本 `Check` 函数把 bool 格式化为 `True/False` 而非 `PASS/FAIL` → 新增规范化逻辑，bool → `PASS`/`FAIL`。

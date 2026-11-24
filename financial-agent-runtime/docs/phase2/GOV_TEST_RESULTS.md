# 上线治理测试结果

日期：2026-11-23

## 1. 治理检查脚本

命令：

```powershell
cd financial-agent-runtime
./scripts/check_production_governance.ps1 -Root (Get-Location).Path
```

结果（`gov_check.log`）：

```
FAIL_COUNT=0
GOVERNANCE_CHECK_END=2026-11-23T10:44:55+08:00
EXIT=0
```

分类统计：

| 判定 | 数量 | 说明 |
|---|---|---|
| PASS | 24 | 代码/配置护栏已就位且静态验证通过 |
| WARN | 1 | `DB01_create_all_coexists`：`init_db` 仍调用 `create_all`（诚实保留，生产须仅用 Alembic） |
| NOT_ENFORCEABLE | 5 | 签名验签、监测规则校准、MCP 沙箱、批准数据集、不可篡改审计存储 |
| FAIL | 0 | 无阻断项 |

5 项 NOT_ENFORCEABLE 必须人工签收，脚本不提供虚假 PASS。

---

## 2. 配置门禁验证

```text
NEW_FIELDS True False ''        # mcp_tls_required / finance_monitoring_production_rules / approval_ref
DEV_VALIDATE_OK                 # dev 配置通过
GATE_OK True                    # 生产 + 监测规则开启但无审批引用 → 正确拒绝
```

说明：`config.validate_runtime_config()` 的生产 monitoring 规则审批门禁按预期工作。

---

## 3. 全量回归

审计文件：`gov_full_regression.{out,err,meta.out}`

- START `10:45:16`，END `10:49:34`，ELAPSED `00:04:17`，EXITCODE `0`
- `227 passed, 25 deselected, 6 warnings in 253.36s`
- 与治理前基线一致，无回归（治理变更只加配置字段与检查脚本，未改业务逻辑）

---

## 4. 工程治理

- Git：`git rev-parse --is-inside-work-tree` 返回 `true`（项目根已初始化，未提交）。
- Lock：`requirements.lock.txt` 319 行；前端 `package-lock.json` 已存在。
- CI：`.github/workflows/ci.yml` 存在（骨架，未在真实 GitHub Actions 运行）。
- CODEOWNERS：存在（占位 handle，需人工替换）。

---

## 5. 结论

治理最小实现的自动检查全部通过（0 FAIL），配置门禁有效，工程治理基线建立。 WARN 与 NOT_ENFORCEABLE 项已诚实记录，等待人工验收签收。

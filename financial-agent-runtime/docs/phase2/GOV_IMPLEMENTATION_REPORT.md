# 上线治理最小实现报告

日期：2026-11-23
范围：README 第八节「生产上线前必须补齐」12 项的最小实现。
状态：**IMPLEMENTED / PASS（最小实现）**。治理最小实现完成，按约定停止等待人工验收。

---

## 1. 结论

对 12 项治理逐条提供了"可执行护栏 + 策略文档 + 明确 HUMAN/ORG 待办"的最小实现。没有以文档冒充真实合规能力：凡需真实机构流程（IdP、KMS、模型风险审批、合规校准、真实数据集、不可篡改审计）的项，均标注 `NOT_ENFORCEABLE`，必须人工签收。

自动化检查脚本 `scripts/check_production_governance.ps1` 覆盖全部 12 项：**24 PASS / 1 WARN / 5 NOT_ENFORCEABLE / 0 FAIL**，退出码 0。

---

## 2. 交付物

| 文件 | 类型 | 说明 |
|---|---|---|
| `docs/governance/PRODUCTION_GOVERNANCE.md` | 新增 | 12 项逐条策略：最小实现 / 检查脚本 / HUMAN/ORG 待办 / 上线门禁判定 |
| `docs/governance/DATA_CLASSIFICATION.md` | 新增 | 数据分类表（P0 禁止 / P1 高敏 / P2 内部 / P3 公开）与采集/存储/脱敏/留存规则 |
| `scripts/check_production_governance.ps1` | 新增 | 30 项自动检查，输出 PASS/FAIL/WARN/NOT_ENFORCEABLE，FAIL 非零退出 |
| `.env.example`（修改） | 配置占位 | 生产治理配置：JWT claims、快照加密、MCP TLS、监测规则审批、OTEL |
| `src/agent_runtime/config.py`（修改） | 代码护栏 | 新增 `mcp_tls_required` / `finance_monitoring_production_rules` / approval refs；生产监测规则无审批引用则拒绝启动 |
| `.github/workflows/ci.yml` | 新增 | 最小 CI：后端快检 + 治理静态检查 + 审计测试 + 前端构建 |
| `SECURITY.md` | 新增 | 安全策略、漏洞报告渠道、上线安全门禁、已知边界披露 |
| `.github/CODEOWNERS` | 新增 | 最小代码归属（@OWNER 占位，需替换真实 handle） |
| 根目录 `.gitignore` | 新增 | 密钥/依赖/备份/审计产物/历史 stray 文件 |
| `financial-agent-runtime/requirements.lock.txt` | 新增 | 当前环境 319 包 lock（可复现基线） |
| Git 仓库 | 新增 | 项目根 `git init`（未提交，符合"不主动 commit"约束） |

---

## 3. 12 项覆盖摘要

| # | 治理项 | 自动检查 | 剩余 HUMAN/ORG |
|---|---|---|---|
| 1 | 身份与租户安全 | OIDC/JWT/RLS 护栏 | 真实 IdP、Token Exchange、MFA |
| 2 | 数据治理 | 分类文档/加密/脱敏 | KMS、删除/跨境流程 |
| 3 | 核心账务隔离 | MCP 空默认/ACL/RLS | 只读服务、属性策略、实时撤销 |
| 4 | 双人控制 | nonce/参数哈希 | 真实签名验签、职责分离 |
| 5 | 合规决策边界 | disclaimer/只读边界 | 授权人员与机构系统 |
| 6 | 模型风险 | 版本冻结/benchmark 入口 | 数据治理/漂移/偏差/人工覆盖 |
| 7 | 监测规则验证 | demo 标记/生产审批门禁 | 机构校准验证审批 |
| 8 | 工具安全 | 授权/MCP 超时 | TLS/OAuth/出站控制/沙箱 |
| 9 | 数据库变更 | 16 迁移/AUTO_CREATE/备份脚本 | create_all 移除、PITR、灾备 |
| 10 | 韧性 | 审计证据/持久化 Temporal | 生产集群运维/演练排期 |
| 11 | Benchmark | 脚本/数据集 | 批准数据集/盲测/置信区间 |
| 12 | 审计与可观测性 | OTEL 依赖/审计表 | exporter/不可篡改存储 |

---

## 4. 关键代码护栏

`config.validate_runtime_config()` 新增生产门禁：

```python
if self.is_production and self.finance_monitoring_production_rules:
    if not (self.finance_monitoring_approval_ref or self.monitoring_rules_approval_ref):
        raise RuntimeError("production monitoring rules require an approval reference ...")
```

验证结果：dev 配置通过；生产 + 监测规则开启但无审批引用 → 正确拒绝（GATE_OK）。

---

## 5. 边界

- Git 已初始化但**未提交**任何变更（commit 需显式授权）。
- CI 为骨架（正确的 workflow 结构，未在真实 GitHub 环境运行）。
- CODEOWNERS 的 `@OWNER-*` 是占位，需替换真实 handle 才能启用 branch protection。
- 治理项 9 的 `create_all` 并存标为 WARN（诚实保留，未强行改造 `init_db`）。
- 5 项 NOT_ENFORCEABLE 必须人工签收，脚本不提供虚假 PASS。

完成后停止，等待人工验收。

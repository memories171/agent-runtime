# Security Policy（最小实现）

本项目为金融机构内部 Agent Runtime MVP。当前仅用于开发验证，不得作为自动交易、授信、客户准入、AML 定性或监管报告依据（见 `financial-agent-runtime/README.md` 第八节红线）。

## 支持的版本
- 开发分支（无正式发布版本）；生产上线前必须完成 12 项治理与人工验收。

## 报告安全漏洞
- 通过内部安全渠道报告；不要在公开 issue 中披露可利用细节。
- 报告需包含：影响组件、复现步骤、影响范围、建议修复。
- 响应时间（目标）：24h 内确认，5 个工作日内给出处置计划。

## 生产上线安全门禁
- 运行 `financial-agent-runtime/scripts/check_production_governance.ps1`，FAIL 必须清零。
- `NOT_ENFORCEABLE` / `HUMAN` 项必须人工签收。
- 必须 `APP_ENV=production` + `AUTH_MODE=oidc` + `SNAPSHOT_ENCRYPTION_KEYS` + 禁止 `TRUST_JWT_AUTHORIZATION_CLAIMS`。
- 详细策略见 `financial-agent-runtime/docs/governance/PRODUCTION_GOVERNANCE.md`。

## 已知边界（诚实披露）
- demo 监测规则、合成 benchmark 数据集、Resource Grant 简单映射、MCP 出站网络控制、真实签名验签、KMS 密钥管理均需 HUMAN/ORG 落地。

# 生产上线治理最小实现（12 项）

日期：2026-11-23
依据：`README.md` 第八节「生产上线前必须补齐」12 项。
定位：本项目是金融机构内部 Agent Runtime MVP。**治理最小实现只提供策略、代码级可执行护栏和检查脚本；任何需要真实机构流程（IdP 配置、模型风险审批、合规校准、真实数据集）的项都明确标注为 HUMAN/ORG 待办，不以文档冒充完成。**

---

## 总原则

- 每项的"最小实现"= 一条可执行护栏（配置校验 / 静态检查 / 检查脚本 / 代码边界）+ 一份策略说明 + 明确剩余的 HUMAN/ORG 项。
- 自动化检查脚本：`scripts/check_production_governance.ps1`，输出 PASS / FAIL / WARN / NOT ENFORCEABLE。
- 上线门禁：所有 FAIL 必须清零；HUMAN/ORG 项必须人工签收，脚本不能代替。

---

## 1. 身份与租户安全

**最小实现**：`config.py::validate_runtime_config()` 强制生产禁止 dev header、`AUTH_MODE=oidc` 时 OIDC 三要素必填、生产禁止 `TRUST_JWT_AUTHORIZATION_CLAIMS`、生产要求 `SNAPSHOT_ENCRYPTION_KEYS`。已有 RLS migration（`0003_enable_rls.py`）。
**检查脚本**：dev header/OIDC/JWT claims 配置静态检查 + RLS migration 存在性。
**HUMAN/ORG**：接入真实 IdP、Token Exchange、MFA。

## 2. 数据治理

**最小实现**：数据分类表（`DATA_CLASSIFICATION.md`）；checkpoint 加密（`checkpoint/crypto.py` AES-256-GCM + digest）；context pack 脱敏视图；`.env.example` 生产加密/留存配置占位。
**检查脚本**：加密密钥配置、快照后端、脱敏代码存在性。
**HUMAN/ORG**：真实密钥管理（KMS）、留存/删除执行、跨境数据审批、访问审计归档。

## 3. 核心账务隔离

**最小实现**：策略声明 Runtime 不直连可写核心账务；`MCP_SERVERS_JSON` 默认 `[]`；工具授权走 `authorize_tool_activity` + Resource ACL。
**检查脚本**：确认生产配置中无直连核心写通道的 MCP server；Resource ACL 代码存在。
**HUMAN/ORG**：接入组织/账户关系、属性策略、实时授权撤销、只读数据服务与受控命令服务。

## 4. 双人控制

**最小实现**：`approval/envelope.py` 已有 nonce / expires_at / arguments_hash / action_hash / policy_version；审批 binding 幂等 consume。
**检查脚本**：envelope 字段存在性检查（签名验签实现标注 NOT ENFORCEABLE）。
**HUMAN/ORG**：真实签名验签、审批人职责分离落地、审计归档。

## 5. 合规决策边界

**最小实现**：`finance/monitoring.py` disclaimer 明确"不构成 AML 定性/监管申报建议"；monitoring API 只读且逐项 ACL 过滤。
**检查脚本**：disclaimer 与只读边界静态检查。
**HUMAN/ORG**：最终决策由授权人员及机构正式系统负责（流程集成）。

## 6. 模型风险管理

**最小实现**：`MODEL_NAME` / `HARNESS_VERSION` 冻结在 `WorkflowInput`（replay 不依赖实时配置）；`benchmarks/` 与 `run_finance_benchmark.py` 提供版本化工评入口。
**检查脚本**：模型/版本字段存在性检查。
**HUMAN/ORG**：训练/评测数据治理、漂移/幻觉/偏差监控、可解释性、人工覆盖审批。

## 7. 监测规则验证

**最小实现**：demo 规则 docstring + `disclaimer` 明确不可用于生产；配置开关 `FINANCE_MONITORING_PRODUCTION_RULES`（默认 false，需显式开启并带审批引用）。
**检查脚本**：生产环境检查是否被显式开启且无审批引用则 FAIL。
**HUMAN/ORG**：金融机构模型风险/合规流程校准、验证、审批、持续监控。

## 8. 工具安全

**最小实现**：`MCP_SERVERS_JSON` 默认 `[]`、工具授权 `authorize_tool_activity`、`ApprovalRequired`/`ToolDenied` 边界；MCP 客户端超时（B5.1 修复）。
**检查脚本**：MCP 配置、授权代码、超时存在性检查。
**HUMAN/ORG**：MCP 身份固定、TLS/OAuth、出站网络控制、供应链审核、沙箱隔离。

## 9. 数据库变更

**最小实现**：16 个 Alembic 迁移；`AUTO_CREATE_DB=false` 默认；备份恢复演练脚本（`backup_restore_drill.ps1`）。
**检查脚本**：迁移数、`AUTO_CREATE_DB`、备份脚本存在性。
**HUMAN/ORG**：生产禁用 `create_all`（当前 `init_db` 仍调用，标注 WARN）、PITR、加密、灾备演练。

## 10. 韧性

**最小实现**：B0–B7 的测试证据（worker 故障、重复 activity、UNKNOWN 对账、checkpoint restore、持久化 Temporal、备份恢复）。
**检查脚本**：审计 meta 文件存在性 + marker 测试入口检查。
**HUMAN/ORG**：生产持久化集群运维、故障演练排期。

## 11. Benchmark

**最小实现**：`run_finance_benchmark.py` + 合成数据集 `transaction_screening.jsonl`，冻结任务入口。
**检查脚本**：脚本与数据集存在性检查。
**HUMAN/ORG**：批准的真实/脱敏数据集、独立标注、盲测、多次重复、置信区间、成本/安全硬门槛。

## 12. 审计与可观测性

**最小实现**：`opentelemetry-*` 可选依赖（observability extra）；context pack 脱敏；审计事件表 `RunEvent` / `AuthAuditLog` / `FinancialAuditLog`。
**检查脚本**：OTEL 依赖存在性、脱敏代码、审计表存在性。
**HUMAN/ORG**：OpenTelemetry 实际接入 exporter、不可篡改审计存储、Prompt/交易/个人数据限权。

---

## 上线门禁判定

| 判定 | 含义 |
|---|---|
| PASS | 代码/配置护栏已就位且检查脚本验证通过 |
| WARN | 部分护栏存在，但有已知缺口（如 `create_all` 并存） |
| FAIL | 必需护栏缺失，禁止上线 |
| NOT ENFORCEABLE / HUMAN | 需要真实机构流程或外部系统，脚本不能验证，必须人工签收 |

**红线**：README 行 261 —— 在完成合规、信息安全、模型风险和业务验收前，不得将输出作为自动交易、授信、客户准入、AML 定性或监管报告依据。

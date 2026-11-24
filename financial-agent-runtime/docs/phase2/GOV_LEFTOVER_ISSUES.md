# 上线治理遗留问题

日期：2026-11-23

---

## 1. 必须人工签收（NOT_ENFORCEABLE）

以下 5 项脚本无法验证真实落地，生产上线前必须人工确认：

1. **签名验签**：`ApprovalEnvelope` 有 nonce/hash，但真实签名/验签未实现（`signature_reference` 仅占位）。
2. **监测规则校准**：demo 规则不可用已由代码门禁保证，但机构级校准/验证/审批需外部流程。
3. **MCP 沙箱与出站控制**：身份固定、TLS/OAuth、网络控制、供应链审核需基础设施。
4. **批准数据集**：benchmark 目前是合成数据，需批准的真实/脱敏数据集与独立标注。
5. **不可篡改审计存储**：OTEL exporter 未实际接入，审计存储仍为普通 DB 表。

---

## 2. WARN（部分护栏）

- **数据库 create_all 并存**：`init_db()` 仍调用 `Base.metadata.create_all()`，与 Alembic 16 个迁移并存。生产必须禁用该路径，仅靠迁移建表；本次最小实现未强行改造，标 WARN。
- **CI 未实跑**：`.github/workflows/ci.yml` 是结构正确的骨架，未在真实 GitHub Actions 环境运行（无远端）。
- **CODEOWNERS 占位**：`@OWNER-*` 需替换真实 handle 才能启用 branch protection。

---

## 3. 工程治理

- Git 已 `init` 但**未提交**；当前所有变更为 untracked。首次提交需人工授权（遵循"不主动 commit"约束）。
- `requirements.lock.txt` 由当前宿主 Python 3.14 环境 `pip freeze` 生成；生产若用 Python 3.12（如 B4 Linux lock）需重新生成对应 lock。
- 根目录 `.gitignore` 忽略了 `*.out`/`*.err`/审计产物；如需将审计证据入库需人工调整。

---

## 4. 治理项 1 的 HUMAN 项

IdP 真实配置、Token Exchange、MFA 未接入；当前 OIDC 配置仍为占位。生产必须按机构 IdP 完成集成。

---

## 5. 红线（重申）

在完成合规、信息安全、模型风险和业务验收前，不得将输出作为自动交易、授信、客户准入、AML 定性或监管报告依据。治理最小实现不构成上述验收。

---

## 6. 结论

上线治理最小实现已提供全部 12 项的护栏与检查，0 FAIL。剩余为 HUMAN/ORG 签收、1 个 WARN、工程治理待办（commit / 真实 handle / CI 实跑）。这些不属于代码可自动完成的范围，等待人工处理。

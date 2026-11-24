# Agent Runtime

面向金融机构内部研究、运营和合规辅助场景的 Agent Runtime MVP，并附带 Finora Web 演示前端。项目用于开发与架构验证，不是可直接投入生产的金融系统。

> **重要边界：** 示例数据为合成数据。系统不执行交易、转账、授信、账户冻结或监管申报，也不作最终 KYC、AML、欺诈或信用决策。不得将演示规则或模型输出作为真实客户决策依据。生产使用前，必须完成组织、法务、合规、信息安全和业务风险评审。

## 项目组成

- **Durable Agent Runtime**：基于 FastAPI、Temporal 和 PostgreSQL，提供长任务执行、暂停/恢复、审批、事务 Outbox 和检查点恢复。
- **安全与隔离**：OIDC/JWT、RBAC/ABAC、租户隔离、资源授权、Tool Gateway 风险控制及审计能力。
- **金融辅助模块**：只读客户、账户和交易查询，演示交易筛查提示、内部复核案件及职责分离审批。
- **MCP、Skills 与 Memory**：按 allowlist 调用 MCP 工具，按需加载 Skills，并提供受 ACL 约束的记忆检索。
- **Finora 前端**：React、TypeScript、Vite 和 Ant Design。默认使用浏览器内的合成 Mock 数据，也可配置连接后端 API 与 OIDC。

## 目录结构

```text
financial-agent-runtime/  FastAPI Runtime、Temporal Worker、金融辅助模块及测试
finora-frontend/          Finora React Web 前端
SECURITY.md               安全政策与生产上线门禁
```

## 快速开始

### 后端

要求 Docker Compose；本地 Python 开发需要 Python 3.11 或更高版本。

```powershell
Set-Location financial-agent-runtime
Copy-Item .env.example .env
docker compose up --build
```

启动后可访问：

- Runtime API 文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/health>
- Temporal UI：<http://localhost:8233>

Compose 配置用于本地开发演示。不要将其中的演示凭据、开发身份认证或 Temporal 开发服务器用于生产；生产环境须按后端文档配置 OIDC、数据库、密钥管理、TLS 和持久化服务。

### 前端

要求 Node.js 20.19+ 或 22.12+，以及 npm。

```powershell
Set-Location finora-frontend
Copy-Item .env.example .env
npm install
npm run dev
```

打开 <http://localhost:5173>。默认 `VITE_DATA_MODE=mock`，不需要启动后端；要连接 API 和 OIDC，请按 `finora-frontend/README.md` 配置环境变量。

## 开发与测试

后端安装测试依赖并运行测试：

```powershell
Set-Location financial-agent-runtime
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
pytest -q
```

前端类型检查及生产构建：

```powershell
Set-Location finora-frontend
npm run build
```

特定集成测试可能需要运行中的 Temporal 或 PostgreSQL；请参阅后端 README 中的测试说明。

## 安全与治理

- 不要提交 `.env`、真实凭据、私钥或运行时检查点数据。
- 本地 Compose 中的开发配置仅供演示；生产部署须遵循 [SECURITY.md](SECURITY.md) 和 `financial-agent-runtime/docs/governance/PRODUCTION_GOVERNANCE.md`。
- 金融筛查结果只是供人工复核的提示，不是 AML、欺诈或违法结论。
- 公开可见不等于授予开源许可证。项目目前没有开源许可证；复用、分发或商用前，请先取得权利人许可。

更完整的后端能力、配置和 API 示例见 [后端 README](financial-agent-runtime/README.md)；前端说明见 [Finora README](finora-frontend/README.md)。

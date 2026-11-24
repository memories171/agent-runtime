# Finora 前端（独立版本）

Finora 是一个金融智能运营平台的 Web 前端。本项目**只包含前端代码**，不包含 FastAPI、Temporal、PostgreSQL、Agent Worker、MCP Server 或任何后端服务。

## 技术栈

- React 19
- TypeScript
- Vite 8
- Ant Design 6
- Ant Design Icons

## 当前页面

1. 运营总览：任务数量、运行状态、待复核案件和任务趋势
2. Agent 任务中心：创建任务、查看运行详情、事件轨迹及暂停/恢复/取消演示
3. 交易监测：选择演示账户并查看合成交易筛查提示
4. KYC / AML 复核：查看演示复核案件
5. Skills 与工具：展示金融 Skills 和 Tool Registry 示例
6. 运行审计：展示前端演示操作的审计日志
7. 模型评测：展示评测看板样例和评测能力说明

## 环境要求

- Node.js 20.19+ 或 22.12+
- npm 10 或兼容版本

## 安装和运行

在 VSCode 中打开本项目根目录（能看到 `package.json` 的目录），打开终端执行：

```powershell
Copy-Item .env.example .env
npm install
npm run dev
```

浏览器打开：

```text
http://localhost:5173
```

也可以不创建 `.env` 直接运行；默认数据模式就是 `mock`。

## 纯前端演示模式（默认）

默认 `VITE_DATA_MODE=mock`：

- 不需要启动后端、Docker、数据库或 Temporal。
- 初始页面使用内置的合成演示数据。
- 新建任务、审批、暂停、恢复、取消等交互由前端 Mock Adapter 模拟。
- 演示数据存储在当前浏览器的 `localStorage` 中，刷新页面后仍然保留。
- 更换浏览器或清理站点数据后，演示数据会恢复初始样例。
- 模拟结果不是真实模型推理，不会触发真实金融工具或真实审批。

## 对接后端与 OIDC 登录

API 模式编辑 `.env`：

```dotenv
VITE_DATA_MODE=api
VITE_API_PROXY_TARGET=http://localhost:8000
VITE_OIDC_AUTHORITY=https://your-idp.example.com/realms/finora
VITE_OIDC_CLIENT_ID=finora-web
VITE_OIDC_SCOPE=openid profile email
VITE_OIDC_REDIRECT_URI=http://localhost:5173/auth/callback
VITE_OIDC_POST_LOGOUT_URI=http://localhost:5173
```

重新启动 `npm run dev`。API 模式使用 OIDC Authorization Code Flow + PKCE：

- 未登录时显示统一 Login 页面；登录回调地址为 `/auth/callback`。
- Access Token 缓存在 `sessionStorage`（由 `oidc-client-ts` 管理），运行时在内存中用于请求鉴权，不写入 `localStorage`。
- 所有请求带 `Authorization: Bearer <access_token>`；`X-Tenant-ID` 仅作为当前租户选择提示，服务端会验证租户成员关系。
- 401 清除登录状态并重新显示登录页；403 显示权限拒绝提示。
- 顶部头像菜单可显示真实租户列表、切换租户和退出登录；Mock 模式仍保留演示身份切换。

新增认证接口：

- `GET /api/v1/auth/me`
- `GET /api/v1/auth/tenants`
- `POST /api/v1/auth/switch-tenant`
- `GET /api/v1/auth/permissions`
- `GET /api/v1/admin/users`
- `GET /api/v1/admin/roles`
- `GET /api/v1/admin/permissions`
- `GET /api/v1/admin/audit-logs`

原有业务接口保持不变。正式环境完全不信任浏览器自报的用户、租户和 scopes。

## 构建静态文件

```powershell
npm run build
```

产物位于 `dist/`，可以部署到 Nginx、静态网站托管服务或任意前端 Hosting。纯 Mock 模式部署时不需要配置 API 代理。

## 金融安全提示

本项目使用合成数据，用于前端交互演示和开发验证。交易规则命中仅代表待人工复核提示，不构成 AML、欺诈或违法认定；不能用于真实客户决策、资金操作或监管申报。

## 设计参考与致谢

Finora 的后台信息架构与组件使用方式参考 GitHub 上的 [Ant Design Pro](https://github.com/ant-design/ant-design-pro)。Finora 是独立实现，不直接复制 Ant Design Pro 页面源码。详见 [ATTRIBUTION.md](./ATTRIBUTION.md)。

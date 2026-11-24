import { useCallback, useEffect, useState } from 'react';
import { Avatar, Badge, Button, Dropdown, Layout, Menu, Tag, Tooltip, Typography, message } from 'antd';
import type { MenuProps } from 'antd';
import {
  AlertOutlined, ApiOutlined, AuditOutlined, BankOutlined, BellOutlined, BookOutlined,
  ClockCircleOutlined, CloudServerOutlined, DashboardOutlined, DatabaseOutlined,
  LineChartOutlined, MenuFoldOutlined, MenuUnfoldOutlined, ReloadOutlined,
  CheckSquareOutlined, RobotOutlined, SafetyCertificateOutlined, SettingOutlined, ThunderboltOutlined, UserOutlined,
} from '@ant-design/icons';
import { api, dataMode, identity, isMockMode, setDemoIdentity, setDemoTenant } from './api';
import { useAuth } from './auth/AuthProvider';
import { AppContext } from './state/AppContext';
import type { NotifyType } from './state/AppContext';
import { OverviewPage } from './pages/OverviewPage';
import { RunsPage } from './pages/RunsPage';
import { McpSkillsPage } from './pages/McpSkillsPage';
import { MemoryPage } from './pages/MemoryPage';
import { CheckpointPage } from './pages/CheckpointPage';
import { SecurityPage } from './pages/SecurityPage';
import { BenchmarkPage } from './pages/BenchmarkPage';
import { ChatWorkspace } from './pages/ChatWorkspace';
import { KnowledgePage } from './pages/KnowledgePage';
import { ApprovalPage } from './pages/ApprovalPage';
import { AuditPage, CasesPage, MonitoringPage, SkillsPage } from './pages/FinancialPages';

const { Header, Sider, Content } = Layout;
const { Text, Title } = Typography;

const navItems: MenuProps['items'] = [
  { type: 'group', label: '工作台', children: [
    { key: 'chat', icon: <RobotOutlined />, label: 'AI 对话工作台' },
    { key: 'knowledge', icon: <BookOutlined />, label: '知识库' },
    { key: 'overview', icon: <DashboardOutlined />, label: '运营总览' },
    { key: 'runs', icon: <ThunderboltOutlined />, label: 'Agent 长任务监控' },
  ] },
  { type: 'group', label: 'Agent Runtime', children: [
    { key: 'mcp', icon: <ApiOutlined />, label: 'MCP 服务 / 技能管理' },
    { key: 'memory', icon: <DatabaseOutlined />, label: '长期记忆 Memory' },
    { key: 'checkpoint', icon: <CloudServerOutlined />, label: 'Checkpoint 与恢复' },
  ] },
  { type: 'group', label: '企业治理', children: [
    { key: 'approval', icon: <CheckSquareOutlined />, label: '审批中心' },
    { key: 'security', icon: <SafetyCertificateOutlined />, label: '权限与安全中心' },
    { key: 'benchmark', icon: <LineChartOutlined />, label: 'Benchmark 实验' },
  ] },
  { type: 'group', label: '金融业务（保留）', children: [
    { key: 'monitoring', icon: <AlertOutlined />, label: '交易监测' },
    { key: 'cases', icon: <BookOutlined />, label: 'KYC / AML 复核' },
    { key: 'skills', icon: <SettingOutlined />, label: '技能与工具' },
    { key: 'audit', icon: <AuditOutlined />, label: '运行审计' },
  ] },
];

const pageTitle: Record<string, { title: string; subtitle: string }> = {
  chat: { title: 'AI 对话工作台', subtitle: '对话式入口：新建会话、上下文问答、长期记忆与知识库引用溯源。' },
  knowledge: { title: '知识库', subtitle: '文档解析清洗、语义切分、向量化与检索测试；AI 问答命中时自动标注引用。' },
  overview: { title: '运营总览', subtitle: '企业级 Agent Runtime 全局态势与核心指标。' },
  runs: { title: 'Agent 长任务监控中心', subtitle: '追踪 30 分钟以上 Run 的执行、心跳、成本、工具调用与实时 Timeline。' },
  mcp: { title: 'MCP 服务 / 技能管理中心', subtitle: '查看 MCP 服务连接状态、协议版本、工具与资源数量，以及技能版本和启用状态。' },
  memory: { title: '长期记忆 Memory Center', subtitle: '管理 Working / Episodic / Semantic / Procedural 四类记忆及其 Scope、Confidence 与 ACL。' },
  checkpoint: { title: 'Checkpoint 与 Recovery Center', subtitle: '查看检查点、Worker 故障、恢复路径、幂等保护与恢复成功率。' },
  approval: { title: '审批中心', subtitle: '查看高风险操作的 Action Envelope、参数 Hash、有效期与审批状态，执行独立审批。' },
  security: { title: '企业权限与安全中心', subtitle: '展示多租户、RBAC、ABAC、Resource ACL、代理身份与权限拦截记录。' },
  benchmark: { title: 'Benchmark 实验中心', subtitle: '通过 Model × Harness 矩阵、排行榜与消融实验解释 Agent 效果差异。' },
  monitoring: { title: '交易监测', subtitle: '保留原有合成交易筛查工作台。' },
  cases: { title: 'KYC / AML 复核', subtitle: '保留原有内部复核案件流程。' },
  skills: { title: '技能与工具', subtitle: '保留原有金融技能目录与工具注册表视图。' },
  audit: { title: '运行审计', subtitle: '保留原有金融审计日志视图。' },
};

export default function App() {
  const auth = useAuth();
  const [messageApi, contextHolder] = message.useMessage();
  const [collapsed, setCollapsed] = useState(false);
  const [activePage, setActivePage] = useState('overview');
  const [refreshToken, setRefreshToken] = useState(0);
  const [apiHealthy, setApiHealthy] = useState<boolean | null>(null);

  const requestRefresh = useCallback(() => setRefreshToken((value) => value + 1), []);

  const notify = useCallback((type: NotifyType, content: string) => {
    if (type === 'success') void messageApi.success(content);
    else if (type === 'error') void messageApi.error(content);
    else if (type === 'warning') void messageApi.warning(content);
    else void messageApi.info(content);
  }, [messageApi]);

  useEffect(() => {
    let active = true;
    api.health().then((result) => {
      if (active) setApiHealthy(result.status === 'ok' || result.status === 'mock');
    }).catch(() => { if (active) setApiHealthy(false); });
    return () => { active = false; };
  }, [refreshToken]);

  const switchIdentity = useCallback((role: 'analyst' | 'compliance' | 'auditor') => {
    if (isMockMode) setDemoIdentity(role);
  }, []);

  const switchTenant = useCallback((tenantId: string) => {
    if (isMockMode) {
      setDemoTenant(tenantId);
      requestRefresh();
      return;
    }
    void auth.switchTenant(tenantId)
      .then(() => { notify('success', `已切换到租户 ${tenantId}`); requestRefresh(); })
      .catch((error: unknown) => notify('error', error instanceof Error ? error.message : '租户切换失败'));
  }, [auth, notify, requestRefresh]);

  const headerMenu: MenuProps['items'] = isMockMode
    ? [
        { key: 'identity-analyst', icon: <UserOutlined />, label: '切换为分析人员（analyst-01）' },
        { key: 'identity-compliance', icon: <SafetyCertificateOutlined />, label: '切换为合规复核员（compliance-01）' },
        { key: 'identity-auditor', icon: <AuditOutlined />, label: '切换为审计观察员（auditor-01）' },
        { type: 'divider' },
        { key: 'refresh', icon: <ReloadOutlined />, label: '刷新数据' },
        { key: 'reset-demo', icon: <ReloadOutlined />, label: '重置本地 DEMO 数据' },
      ]
    : [
        ...auth.tenants.map((tenant) => ({
          key: `tenant:${tenant.tenant_id}`,
          icon: <BankOutlined />,
          label: `切换租户：${tenant.display_name || tenant.tenant_id}`,
        })),
        { type: 'divider' },
        { key: 'refresh', icon: <ReloadOutlined />, label: '刷新数据' },
        { key: 'api', icon: <CloudServerOutlined />, label: '打开 API 文档' },
        { key: 'logout', icon: <SafetyCertificateOutlined />, label: '退出登录' },
      ];

  const handleHeaderMenu = ({ key }: { key: string }) => {
    if (key.startsWith('tenant:')) {
      switchTenant(key.slice('tenant:'.length));
    } else if (key === 'identity-analyst' || key === 'identity-compliance' || key === 'identity-auditor') {
      const role = key === 'identity-compliance' ? 'compliance' : key === 'identity-auditor' ? 'auditor' : 'analyst';
      switchIdentity(role);
      notify('info', `已切换为 ${role} Demo Identity（非真实认证）`);
      requestRefresh();
    } else if (key === 'logout') {
      void auth.signOut();
    } else if (key === 'refresh') {
      requestRefresh();
    } else if (key === 'reset-demo') {
      api.resetMockData();
      requestRefresh();
      notify('success', '本地 DEMO 数据已重置');
    } else if (key === 'api') {
      window.open('/api/docs', '_blank', 'noopener,noreferrer');
    }
  };

  const currentPage = pageTitle[activePage] || pageTitle.overview;

  const renderPage = () => {
    if (activePage === 'chat') return <ChatWorkspace onOpenMemory={() => setActivePage('memory')} onOpenCheckpoint={() => setActivePage('checkpoint')} />;
    if (activePage === 'knowledge') return <KnowledgePage />;
    if (activePage === 'runs') return <RunsPage />;
    if (activePage === 'mcp') return <McpSkillsPage />;
    if (activePage === 'memory') return <MemoryPage />;
    if (activePage === 'checkpoint') return <CheckpointPage />;
    if (activePage === 'approval') return <ApprovalPage />;
    if (activePage === 'security') return <SecurityPage />;
    if (activePage === 'benchmark') return <BenchmarkPage />;
    if (activePage === 'monitoring') return <MonitoringPage />;
    if (activePage === 'cases') return <CasesPage />;
    if (activePage === 'skills') return <SkillsPage />;
    if (activePage === 'audit') return <AuditPage />;
    return <OverviewPage onOpenRuns={() => setActivePage('runs')} />;
  };

  return <AppContext.Provider value={{
    isMockMode, dataMode, identity, apiHealthy, refreshToken,
    requestRefresh, notify, switchIdentity, switchTenant,
  }}>
    <Layout className="app-shell">
      {contextHolder}
      <Sider className="app-sider" width={250} collapsedWidth={78} collapsed={collapsed} trigger={null}>
        <div className="brand-lockup">
          <div className="brand-mark"><BankOutlined /></div>
          {!collapsed && <div className="brand-copy"><b>Finora</b><span>AGENT RUNTIME PLATFORM</span></div>}
        </div>
        <div className="tenant-switch">
          <div className="tenant-avatar">DB</div>
          {!collapsed && <div className="tenant-copy"><small>当前数据租户</small><b>{isMockMode ? identity.tenantId : (auth.currentTenant || '-')} <span>⌄</span></b></div>}
        </div>
        <Menu className="side-menu" theme="dark" mode="inline" selectedKeys={[activePage]} items={navItems}
          onClick={({ key }) => setActivePage(key)} inlineCollapsed={collapsed} />
        <div className="sider-bottom">
          <div className="sider-status"><span className="live-dot" />{!collapsed && <><span>{isMockMode ? '本地演示环境' : '开发环境'}</span><Tag color={isMockMode ? 'processing' : 'green'}>{isMockMode ? 'MOCK' : 'API'}</Tag></>}</div>
          <div className="user-block">
            <Avatar size={36} icon={<UserOutlined />} />
            <div className="user-copy">
              <b>{isMockMode ? identity.userId : (auth.profile?.user_id || auth.user?.profile?.name || '')}</b>
              <span>{isMockMode ? `${identity.displayName} · Demo Identity` : `${auth.profile?.authentication_method === 'oidc' ? 'OIDC 已认证' : '认证中'} · ${auth.profile?.roles?.join(', ') || ''}`}</span>
            </div>
            <Tooltip title="用户设置"><Button type="text" icon={<SettingOutlined />} className="sider-setting" /></Tooltip>
          </div>
        </div>
      </Sider>
      <Layout className="main-layout">
        <Header className="top-header">
          <div className="header-left">
            <Button className="collapse-btn" type="text" icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setCollapsed(!collapsed)} />
            <div className="breadcrumb"><span>Finora</span><span>/</span><b>{currentPage.title}</b></div>
          </div>
          <div className="header-right">
            <div className="connection-pill"><span className={`connection-dot ${isMockMode ? '' : apiHealthy ? 'ok' : apiHealthy === false ? 'bad' : ''}`} />
              {isMockMode ? 'DEMO / MOCK' : apiHealthy ? 'Runtime 已连接' : apiHealthy === false ? 'Runtime 离线' : '连接检查中'}
            </div>
            {!isMockMode && <Tooltip title="打开 API 文档"><Button type="text" icon={<CloudServerOutlined />} onClick={() => window.open('/api/docs', '_blank', 'noopener,noreferrer')} /></Tooltip>}
            <Tooltip title="通知"><Badge dot><Button type="text" icon={<BellOutlined />} /></Badge></Tooltip>
            <Dropdown menu={{ items: headerMenu, onClick: handleHeaderMenu }}>
              <Avatar className="header-avatar" size={34}>{(isMockMode ? identity.displayName : (auth.profile?.user_id || auth.user?.profile?.name || 'U')).slice(0, 1)}</Avatar>
            </Dropdown>
          </div>
        </Header>
        <Content className="main-content">
          <div className="page-heading">
            <div><Title level={3}>{currentPage.title}</Title><Text type="secondary">{currentPage.subtitle}</Text></div>
            <div className="page-date"><ClockCircleOutlined /> {new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' })}</div>
          </div>
          {renderPage()}
          <footer className="app-footer">
            <span>Finora · 企业级 Agent Runtime 可视化平台</span>
            <span>DEMO / MOCK 数据不可作为模型验收、恢复能力证明、安全评测或合规结论</span>
          </footer>
        </Content>
      </Layout>
    </Layout>
  </AppContext.Provider>;
}

import { Form, Input, Alert, Button, Card, Col, Descriptions, Drawer, Empty, Modal, Row, Space, Statistic, Table, Tag, Typography, message } from 'antd';
import type { TableColumnsType } from 'antd';
import { useState } from 'react';
import {
  AuditOutlined, BookOutlined, CheckCircleOutlined, LineChartOutlined, PlayCircleOutlined,
  ReloadOutlined, WalletOutlined,
} from '@ant-design/icons';
import { api } from '../api';
import { useAsyncData } from '../hooks/useAsyncData';
import { useApp } from '../state/AppContext';
import { ApiUnavailable, DemoBanner, formatTime, riskLabel, shortId, StatusTag, toolLabel } from '../components/Ui';
import type { AuditLog, ReviewCase, RunRecord } from '../types';

const { Paragraph, Text, Title } = Typography;

export function MonitoringPage() {
  const { refreshToken, notify, requestRefresh, isMockMode } = useApp();
  const [form] = Form.useForm<{ account_id: string }>();
  const runsQuery = useAsyncData(() => api.listRuns(100), [refreshToken]);
  const runs = (runsQuery.data || []).filter((run) => run.prompt.includes('financial.screen_transactions') || run.prompt.includes('金融监测演示'));

  const submit = async (values: { account_id: string }) => {
    const prompt = `金融监测演示：请对账户 ${values.account_id.trim()} 运行 financial.screen_transactions，并解释命中记录；不要将提示描述为洗钱结论。`;
    try {
      await api.createRun({ prompt, skill_ids: ['transaction-monitoring'] });
      notify('success', '交易监测任务已提交');
      requestRefresh();
      form.resetFields();
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '提交失败');
    }
  };

  const columns: TableColumnsType<RunRecord> = [
    { title: 'Run ID', dataIndex: 'run_id', width: 150, render: (value: string) => <Text code>{shortId(value)}</Text> },
    { title: '任务', dataIndex: 'prompt', ellipsis: true },
    { title: '状态', dataIndex: 'status', width: 110, render: (status: RunRecord['status']) => <StatusTag status={status} /> },
    { title: '创建时间', dataIndex: 'created_at', width: 160, render: formatTime },
  ];

  return <>
    <DemoBanner note="交易筛查规则、账户与命中证据均为合成演示数据，不构成 AML / 欺诈结论。" />
    <div className="monitor-hero">
      <div><div className="eyebrow">TRANSACTION INTELLIGENCE</div><Title level={3}>交易监测工作台</Title><Paragraph>保留原有金融业务能力：选择演示账户启动交易筛查，并查看任务结果。</Paragraph></div>
      <div className="monitor-icon"><LineChartOutlined /></div>
    </div>
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={10}>
        <Card className="panel-card" bordered={false} title="发起交易筛查">
          <Form form={form} layout="vertical" onFinish={(values) => void submit(values)} initialValues={{ account_id: 'ACC-DEMO-001' }}>
            <Form.Item label="演示账户 ID" name="account_id" rules={[{ required: true, message: '请输入账户 ID' }]}>
              <Input size="large" prefix={<WalletOutlined />} placeholder="ACC-DEMO-001" />
            </Form.Item>
            <Alert type="warning" showIcon message="仅限合成演示数据" description={isMockMode ? '当前由前端 Mock Adapter 模拟，无真实数据库或交易数据。' : '筛查规则仅供演示，不构成 AML 或欺诈结论。'} />
            <Button className="full-button" type="primary" htmlType="submit" icon={<PlayCircleOutlined />}>运行交易监测 Agent</Button>
          </Form>
        </Card>
      </Col>
      <Col xs={24} xl={14}>
        <Card className="panel-card" bordered={false} title="监测流程">
          <div className="process-list">
            {[
              ['01', '读取账户与交易', '仅查询当前租户且已授权的金融资源'],
              ['02', '执行可解释规则', '检查短时高频借记、重复交易参考号'],
              ['03', '整理命中证据', '列出交易 ID、时间和命中原因'],
              ['04', '生成调查摘要', '输出人工复核提示，不自动定性'],
            ].map(([number, title, description]) => <div className="process-item" key={number}>
              <div className="process-no">{number}</div>
              <div><b>{title}</b><span>{description}</span></div>
              <CheckCircleOutlined className="process-check" />
            </div>)}
          </div>
        </Card>
      </Col>
    </Row>
    <Card className="panel-card table-card" bordered={false} title="最近监测任务">
      <Table rowKey="run_id" columns={columns} dataSource={runs.slice(0, 8)} pagination={false}
        locale={{ emptyText: <Empty description="尚无交易监测任务" /> }} />
    </Card>
  </>;
}

export function CasesPage() {
  const { refreshToken, requestRefresh } = useApp();
  const casesQuery = useAsyncData(() => api.listCases(), [refreshToken]);
  const [selectedCase, setSelectedCase] = useState<ReviewCase | null>(null);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [reviewReason, setReviewReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const statusLabels: Record<string, string> = {
    OPEN: '待处理', PENDING: '待处理', IN_REVIEW: '复核中',
    APPROVED: '已通过', REJECTED: '已驳回', ESCALATED: '已升级', ARCHIVED: '已归档',
  };
  const typeLabels: Record<string, string> = {
    TRANSACTION_MONITORING: '交易监测', KYC_REVIEW: '客户身份复核',
    RECONCILIATION: '对账差异', AML: '反洗钱', FRAUD: '欺诈排查',
  };
  const actionLabels: Record<string, string> = {
    START_REVIEW: '开始复核', APPROVE: '通过', REJECT: '驳回',
    ESCALATE: '升级', ARCHIVE: '归档',
  };
  const statusColors: Record<string, string> = {
    OPEN: 'orange', PENDING: 'gold', IN_REVIEW: 'blue', APPROVED: 'green',
    REJECTED: 'red', ESCALATED: 'volcano', ARCHIVED: 'default',
  };
  const terminalStatuses = new Set(['APPROVED', 'REJECTED', 'ARCHIVED']);
  const cases = Array.isArray(casesQuery.data) ? casesQuery.data : [];
  const stats = {
    pending: cases.filter((row) => ['OPEN', 'PENDING'].includes(row.status)).length,
    reviewing: cases.filter((row) => row.status === 'IN_REVIEW').length,
    escalated: cases.filter((row) => row.status === 'ESCALATED').length,
    closed: cases.filter((row) => terminalStatuses.has(row.status)).length,
  };

  const submitReview = async () => {
    if (!selectedCase || !pendingAction) return;
    if (!reviewReason.trim()) {
      message.warning('请填写处理理由');
      return;
    }
    setSubmitting(true);
    try {
      const updated = await api.reviewCase(selectedCase.case_id, pendingAction, reviewReason.trim());
      setSelectedCase(updated);
      setPendingAction(null);
      setReviewReason('');
      requestRefresh();
      message.success('复核操作已提交');
    } catch {
      message.error('复核操作失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  const columns: TableColumnsType<ReviewCase> = [
    { title: '案件编号', dataIndex: 'case_id', render: (value: string) => <Text code>{shortId(value)}</Text> },
    { title: '案件类型', dataIndex: 'case_type', render: (value: string) => <Tag color="blue">{typeLabels[value] || value}</Tag> },
    { title: '关联主体', dataIndex: 'subject_id' },
    { title: '复核说明', dataIndex: 'rationale', ellipsis: true },
    { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={statusColors[value] || 'default'}>{statusLabels[value] || value}</Tag> },
    { title: '创建时间', dataIndex: 'created_at', render: formatTime },
    { title: '', key: 'action', render: (_, record) => <Button type="link" onClick={() => setSelectedCase(record)}>查看</Button> },
  ];

  return <>
    <DemoBanner note="KYC / AML 复核案件为内部演示流程，不构成监管定性或申报。" />
    <Row gutter={[12, 12]} className="case-stats">
      {[
        ['待处理', stats.pending, 'orange'],
        ['复核中', stats.reviewing, 'blue'],
        ['已升级', stats.escalated, 'volcano'],
        ['已办结', stats.closed, 'green'],
      ].map(([title, value, color]) => (
        <Col xs={12} md={6} key={String(title)}>
          <Card size="small" bordered={false} className="panel-card">
            <Statistic title={title} value={Number(value)} valueStyle={{ color: String(color) }} />
          </Card>
        </Col>
      ))}
    </Row>
    <Card className="panel-card table-card" bordered={false} title={<div className="card-title"><span>内部复核案件</span><Button icon={<ReloadOutlined />} onClick={() => requestRefresh()}>刷新</Button></div>}>
      <Paragraph type="secondary" className="compliance-note">本系统仅提供内部复核线索和风险提示，不构成监管定性、法律结论或申报决策。最终处理由授权人员及正式系统完成。</Paragraph>
      <Table rowKey="case_id" columns={columns} dataSource={cases} loading={casesQuery.loading} pagination={{ pageSize: 10 }}
        onRow={(record) => ({ onClick: () => setSelectedCase(record) })}
        locale={{ emptyText: <Empty description="暂无复核案件" /> }} />
    </Card>

    <Drawer title={`案件详情 · ${selectedCase ? shortId(selectedCase.case_id) : ''}`} width={720}
      open={Boolean(selectedCase)} onClose={() => setSelectedCase(null)}>
      {selectedCase && <>
        <Descriptions column={1} bordered size="small">
          <Descriptions.Item label="案件类型">{typeLabels[selectedCase.case_type] || selectedCase.case_type}</Descriptions.Item>
          <Descriptions.Item label="关联主体">{selectedCase.subject_id}</Descriptions.Item>
          <Descriptions.Item label="当前状态"><Tag color={statusColors[selectedCase.status] || 'default'}>{statusLabels[selectedCase.status] || selectedCase.status}</Tag></Descriptions.Item>
          <Descriptions.Item label="创建人">{selectedCase.created_by}</Descriptions.Item>
          <Descriptions.Item label="创建时间">{formatTime(selectedCase.created_at)}</Descriptions.Item>
          <Descriptions.Item label="最近复核人">{selectedCase.reviewer || '—'}</Descriptions.Item>
          <Descriptions.Item label="最近复核时间">{formatTime(selectedCase.reviewed_at)}</Descriptions.Item>
        </Descriptions>

        <div className="section-label">复核说明</div>
        <Paragraph>{selectedCase.rationale}</Paragraph>

        <div className="section-label">证据交易</div>
        {Array.isArray(selectedCase.evidence_transaction_ids) && selectedCase.evidence_transaction_ids.length
          ? <Space wrap>{selectedCase.evidence_transaction_ids.map((id) => <Tag key={id}>{shortId(id)}</Tag>)}</Space>
          : <Text type="secondary">暂无证据交易</Text>}

        <div className="section-label">复核历史</div>
        {Array.isArray(selectedCase.review_history) && selectedCase.review_history.length
          ? selectedCase.review_history.map((item, index) => (
            <Paragraph key={`${item.at}-${index}`} type="secondary">
              {item.actor} · {actionLabels[item.action] || item.action} · {statusLabels[item.status] || item.status} · {formatTime(item.at)}
              <br />{item.reason}
            </Paragraph>
          ))
          : <Text type="secondary">暂无复核记录</Text>}

        <div className="section-label">执行复核</div>
        {terminalStatuses.has(selectedCase.status)
          ? <Paragraph type="secondary">案件已办结，不能再执行复核操作。</Paragraph>
          : <Space wrap>{Object.entries(actionLabels).map(([key, label]) => (
            <Button key={key} type={key === 'APPROVE' ? 'primary' : 'default'} onClick={() => { setPendingAction(key); setReviewReason(''); }}>
              {label}
            </Button>
          ))}</Space>}
      </>}
    </Drawer>

    <Modal open={Boolean(pendingAction)} title={`确认${pendingAction ? actionLabels[pendingAction] : ''}`}
      onOk={submitReview} onCancel={() => setPendingAction(null)} okText="确认提交" cancelText="取消"
      confirmLoading={submitting} okButtonProps={{ disabled: !reviewReason.trim() }}>
      <Paragraph type="secondary">请填写处理理由，提交后将写入审计日志。</Paragraph>
      <Input.TextArea rows={4} value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} placeholder="请输入复核理由" />
    </Modal>
  </>;
}

export function SkillsPage() {
  const { refreshToken } = useApp();
  const skillsQuery = useAsyncData(() => api.listSkills(), [refreshToken]);
  const toolsQuery = useAsyncData(() => api.listTools(), [refreshToken]);
  const tools = toolsQuery.data || [];

  const columns: TableColumnsType<{ name: string; display_name?: string; description: string; required_scopes: string[]; risk_level: string; idempotent: boolean }> = [
    { title: '工具名称', key: 'name', render: (_, record) => <b>{toolLabel(record.name, record.display_name)}</b> },
    { title: '功能说明', dataIndex: 'description' },
    { title: '风险级别', dataIndex: 'risk_level', width: 110, render: (value: string) => <Tag color={value === 'high' || value === 'critical' ? 'volcano' : value === 'medium' ? 'gold' : 'green'}>{riskLabel(value)}</Tag> },
    { title: '幂等性', dataIndex: 'idempotent', width: 110, render: (value: boolean) => value ? <Tag color="success">支持幂等</Tag> : <Tag color="warning">需谨慎</Tag> },
  ];

  return <>
    <DemoBanner note="金融技能与工具注册表为演示目录；详细管理能力见 MCP 服务 / 技能管理中心。" />
    <Row gutter={[16, 16]}>
      {(skillsQuery.data || []).map((skill) => <Col xs={24} md={12} xl={8} key={skill.skill_id}>
        <Card className="capability-card" bordered={false}>
          <div className="capability-icon"><BookOutlined /></div>
          <Title level={5}>{skill.skill_id}</Title>
          <Paragraph>{skill.description || '金融领域任务指引'}</Paragraph>
          <Text type="secondary" className="mono-text">{skill.path || '技能注册表'}</Text>
        </Card>
      </Col>)}
    </Row>
    <Card className="panel-card table-card" bordered={false} title={<div className="card-title"><span>工具网关 · 工具注册表</span><Text type="secondary">运行时真实权限由后端校验</Text></div>}>
      <Table rowKey="name" dataSource={tools} pagination={false} columns={columns} locale={{ emptyText: <Empty description="工具列表暂不可用" /> }} />
    </Card>
  </>;
}

function auditActorLabel(userId: string): string {
  if (userId === 'analyst-01') return '分析人员';
  if (userId === 'compliance-01') return '合规复核员';
  if (userId === 'auditor-01') return '审计观察员';
  if (userId === 'platform-admin') return '平台管理员';
  return userId;
}

function auditActionLabel(action: string): string {
  const value = action || '';
  if (value === 'TRANSACTION_SCREENING_RUN' || value.includes('screen_transactions')) return '执行交易筛查';
  if (value === 'REVIEW_CASE_CREATED') return '创建复核案件';
  if (value.includes('review_cases')) return '处理复核案件';
  if (value.startsWith('DELETE_knowledge_documents')) return '删除知识库文档';
  if (value.startsWith('POST_knowledge_documents')) return '上传知识库文档';
  if (value.startsWith('POST_knowledge_search')) return '检索知识库';
  if (value.startsWith('POST_knowledge_stats')) return '查看知识库统计';
  if (value.includes('messages_stopped')) return '停止智能问答生成';
  if (value.includes('messages_stream') || value.includes('messages')) return '发起智能问答';
  if (value.startsWith('POST_chat_conversations')) return '发起智能问答';
  if (value.startsWith('POST_approvals')) return '执行审批操作';
  if (value.startsWith('DELETE_approvals')) return '撤销审批';
  if (value.includes('pause')) return '暂停运行任务';
  if (value.includes('resume')) return '恢复运行任务';
  if (value.includes('cancel')) return '取消运行任务';
  if (value.startsWith('DELETE_runs')) return '删除运行任务';
  if (value.startsWith('POST_runs')) return '创建运行任务';
  if (value.startsWith('POST_')) return '提交业务操作';
  if (value.startsWith('DELETE_')) return '删除业务数据';
  return '执行系统操作';
}

function auditTargetLabel(record: AuditLog): string {
  const details = record.details || {};
  const path = typeof details.path === 'string' ? details.path : '';
  let target = '系统操作';
  if (path.includes('/knowledge/documents')) target = '知识库文档';
  else if (path.includes('/knowledge/search')) target = '知识库检索';
  else if (path.includes('/chat/')) target = '智能问答会话';
  else if (path.includes('/approvals')) target = '审批中心';
  else if (path.includes('/runs')) target = '运行任务';
  else if (record.resource_type === 'financial_account') target = '金融账户';
  else if (record.resource_type === 'financial_review_case') target = '复核案件';
  const status = Number(details.status_code);
  if (status >= 200 && status < 400) return `${target} · 操作成功`;
  if (status) return `${target} · 操作失败（${status}）`;
  return target;
}

export function AuditPage() {
  const { refreshToken, requestRefresh } = useApp();
  const auditQuery = useAsyncData(() => api.listAudit(), [refreshToken]);
  const columns: TableColumnsType<AuditLog> = [
    { title: '时间', dataIndex: 'created_at', render: formatTime, width: 170 },
    { title: '操作人', dataIndex: 'actor_user_id', render: (value: string) => auditActorLabel(value), width: 130 },
    { title: '干了什么', dataIndex: 'action', render: (_value: string, record: AuditLog) => (
      <Space direction="vertical" size={0}>
        <Text>{auditActionLabel(record.action)}</Text>
        <Text type="secondary">{auditTargetLabel(record)}</Text>
      </Space>
    ) },
  ];

  return <>
    <DemoBanner note="审计日志为演示记录，不是不可篡改的真实审计存储。" />
    <Card className="panel-card table-card" bordered={false} title={<div className="card-title"><span><AuditOutlined /> 金融审计日志</span><Button icon={<ReloadOutlined />} onClick={() => requestRefresh()}>刷新</Button></div>}>
      {auditQuery.error && <ApiUnavailable error={auditQuery.error} feature="审计日志" />}
      <Table rowKey="audit_id" dataSource={auditQuery.data || []} loading={auditQuery.loading} pagination={{ pageSize: 10 }} columns={columns}
        locale={{ emptyText: <Empty description="暂无审计记录，或当前身份没有读取权限" /> }} />
    </Card>
  </>;
}

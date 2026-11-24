import { useCallback, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { Alert, Button, Descriptions, Input, Modal, Space, Table, Tag, Tooltip, Typography } from 'antd';
import { CheckOutlined, CloseOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons';
import { api, isMockMode } from '../api';
import { useAuth } from '../auth/AuthProvider';
import { ApiUnavailable, DemoBanner, formatTime, shortId } from '../components/Ui';
import { useAsyncData } from '../hooks/useAsyncData';
import type { ApprovalActionEnvelope, ApprovalRecord, ApprovalStatus } from '../types';

const { Text, Paragraph } = Typography;

const statusColor: Record<ApprovalStatus, string> = {
  PENDING: 'processing', APPROVED: 'success', CONSUMED: 'green', REJECTED: 'error',
  EXPIRED: 'default', REVOKED: 'default', INVALIDATED: 'warning',
};

const statusLabel: Record<string, string> = {
  PENDING: '待审批', APPROVED: '已通过', CONSUMED: '已使用', REJECTED: '已驳回',
  EXPIRED: '已过期', REVOKED: '已撤销', INVALIDATED: '已失效',
};
const userLabel: Record<string, string> = {
  'analyst-01': '分析人员', 'compliance-01': '合规复核员', 'auditor-01': '审计观察员',
  'fi-requester': '金融业务申请人', 'platform-admin': '平台管理员',
};
const toolLabel: Record<string, string> = {
  'fixture.external_write': '外部系统写入（测试）',
  'financial.screen_transactions': '交易筛查',
  'financial.create_review_case': '创建复核案件',
  'financial.get_account_summary': '查询账户摘要',
  'financial.list_transactions': '查询交易流水',
  'financial.reconcile': '执行对账',
  'mcp.finance.read': '读取金融数据',
};
const operationLabel: Record<string, string> = {
  'fixture.write': '写入测试数据', screen: '筛查交易',
  create_review_case: '创建复核案件', reconcile: '执行对账', read: '读取数据',
};
const riskLabel: Record<string, string> = { HIGH: '高风险', MEDIUM: '中风险', LOW: '低风险' };
const argumentLabel: Record<string, string> = {
  account: '账户', account_id: '账户编号', amount: '金额', subject_id: '主体编号',
  case_type: '案件类型', rationale: '申请理由', evidence_transaction_ids: '证据交易',
  velocity_count: '短时交易次数', velocity_window_minutes: '统计窗口（分钟）',
  lookback_days: '回溯天数', start_at: '开始时间', end_at: '结束时间',
  limit: '返回条数', x: '测试参数', text: '文本内容',
};
const caseTypeLabel: Record<string, string> = {
  KYC: 'KYC 身份复核', AML: 'AML 反洗钱复核', TRANSACTION_REVIEW: '交易复核',
};

function runLabel(value: string): string {
  return `运行 ${shortId(value)}`;
}

function formatArgumentValue(key: string, value: unknown): string {
  if (key === 'case_type') return caseTypeLabel[String(value)] || String(value);
  if (Array.isArray(value)) return value.map((item) => String(item)).join('、');
  if (value && typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function humanArguments(raw?: string | null): ReactNode {
  if (!raw) return '—';
  let parsed: Record<string, unknown> | null = null;
  try {
    parsed = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    const entries: Record<string, unknown> = {};
    const pattern = /['"]?([A-Za-z0-9_]+)['"]?\s*:\s*('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|\[[^\]]*\]|\{[^}]*\}|[^,}]+)/g;
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(raw))) {
      const valueText = match[2].trim();
      if ((valueText.startsWith("'") && valueText.endsWith("'")) || (valueText.startsWith('"') && valueText.endsWith('"'))) {
        entries[match[1]] = valueText.slice(1, -1);
      } else if (valueText.startsWith('[')) {
        try { entries[match[1]] = JSON.parse(valueText.replace(/'/g, '"')) as unknown; }
        catch { entries[match[1]] = valueText; }
      } else {
        entries[match[1]] = valueText;
      }
    }
    if (Object.keys(entries).length) parsed = entries;
  }
  if (!parsed) return <Text code style={{ fontSize: 11 }}>{raw}</Text>;
  return <Space direction="vertical" size={2}>
    {Object.entries(parsed).map(([key, value]) => (
      <Text key={key} type="secondary">{argumentLabel[key] || key}：{formatArgumentValue(key, value)}</Text>
    ))}
  </Space>;
}

const APPROVAL_READ_PERMISSIONS = new Set([
  'approval:read', 'approval:approve', 'runs:manage:tenant',
  'audit:read', 'audit:read:tenant', 'financial:audit:read',
]);

export function ApprovalPage() {
  const auth = useAuth();
  const [reason, setReason] = useState('');
  const [feedback, setFeedback] = useState<string | null>(null);
  const query = useAsyncData(() => api.listApprovals(), [auth.profile?.permissions?.join(',')]);
  const approvals = query.data || [];
  const approvalOrder = useMemo(
    () => new Map(approvals.map((item, index) => [item.approval_id, index + 1])),
    [approvals],
  );
  const permissions = auth.profile?.permissions ?? auth.profile?.scopes ?? [];
  const canApprove = isMockMode || permissions.includes('approval:approve');
  const canRead = isMockMode || permissions.some((item) => APPROVAL_READ_PERMISSIONS.has(item));

  const reload = useCallback(() => { query.reload(); }, [query]);

  const act = async (record: ApprovalRecord, action: 'approve' | 'reject' | 'revoke') => {
    if (!canApprove) {
      setFeedback('当前账号缺少 approval:approve 权限，无法作出审批决策；请使用审批人账号登录后重试。');
      return;
    }
    try {
      if (action === 'revoke') await api.revokeApproval(record.approval_id);
      else await api.decideApproval(record.approval_id, action === 'approve', reason);
      setFeedback(`${action === 'approve' ? '审批已记录，Workflow Signal 经 Outbox 可靠派发；工具实际执行结果以任务详情为准。' : action === 'reject' ? '已拒绝，操作不会执行。' : '审批已撤销。'}`);
      setReason('');
      reload();
    } catch (error) {
      const message = error instanceof Error ? error.message : '未知错误';
      setFeedback(message.includes('scope required')
        ? `权限不足：${message}。请使用具有 approval:approve 权限的账号登录。`
        : `操作失败：${message}`);
    }
  };

  const showEnvelope = (record: ApprovalRecord) => {
    Modal.info({
      title: `审批单 #${approvalOrder.get(record.approval_id) || shortId(record.approval_id)} · 查看内容`,
      width: 720,
      content: <EnvelopeView approvalId={record.approval_id} record={record} />,
    });
  };

  if (query.error) return <ApiUnavailable error={query.error} feature="审批中心" />;

  const columns = [
    { title: '审批单', dataIndex: 'approval_id', width: 120, render: (v: string) => (
      <Tooltip title={v}><Text>审批单 #{approvalOrder.get(v) || shortId(v)}</Text></Tooltip>
    ) },
    { title: '所属运行', dataIndex: 'run_id', width: 150, render: (v: string) => <Tooltip title={v}><Text code>{runLabel(v)}</Text></Tooltip> },
    { title: '申请人', dataIndex: 'requested_by', width: 120, render: (_: unknown, record: ApprovalRecord) => {
      const v = record.requested_by || record.requester;
      return v ? (userLabel[v] || v) : '—';
    } },
    { title: '审批人', dataIndex: 'approver_id', width: 120, render: (_: unknown, record: ApprovalRecord) => {
      const v = record.approver_id || record.approver;
      return v ? (userLabel[v] || v) : '—';
    } },
    { title: '申请事项', dataIndex: 'tool_name', width: 160, render: (v: string) => <Text>{toolLabel[v] || v || '—'}</Text> },
    { title: '操作类型', dataIndex: 'operation_type', width: 130, render: (v: string) => operationLabel[v] || v || '—' },
    { title: '操作对象', dataIndex: 'target_resource_id', width: 120, render: (v: string) => v || '未指定' },
    { title: '风险等级', dataIndex: 'risk_level', width: 100, render: (v: string) => <Tag color={v === 'HIGH' ? 'error' : 'warning'}>{riskLabel[v] || v || '高风险'}</Tag> },
    { title: '参数摘要', dataIndex: 'canonical_arguments_hash', width: 130, render: (v: string) => v ? <Tooltip title={v}><Text code>{shortId(v)}</Text></Tooltip> : '—' },
    { title: '策略版本', dataIndex: 'policy_version', width: 100, render: (v: string) => v || 'v1' },
    { title: '申请时间', dataIndex: 'issued_at', width: 150, render: (v: string) => v ? formatTime(v) : '—' },
    { title: '过期时间', dataIndex: 'expires_at', width: 150, render: (v: string) => v ? formatTime(v) : '—' },
    { title: '状态', dataIndex: 'approval_status', width: 110, render: (v: ApprovalStatus) => <Tag color={statusColor[v] || 'default'}>{statusLabel[v] || v}</Tag> },
    {
      title: '操作', key: 'action', fixed: 'right' as const, width: 200,
      render: (_: unknown, record: ApprovalRecord) => (
        <Space size={0}>
          <Button type="link" size="small" disabled={!canRead} title={canRead ? undefined : '缺少审批读取权限'} onClick={() => showEnvelope(record)}>查看内容</Button>
          {record.approval_status === 'PENDING' && <Button type="link" size="small" icon={<CheckOutlined />} disabled={!canApprove} title={canApprove ? undefined : '缺少 approval:approve 权限'} onClick={() => void act(record, 'approve')}>批准</Button>}
          {record.approval_status === 'PENDING' && <Button type="link" size="small" danger icon={<CloseOutlined />} disabled={!canApprove} title={canApprove ? undefined : '缺少 approval:approve 权限'} onClick={() => void act(record, 'reject')}>拒绝</Button>}
          {['PENDING', 'APPROVED'].includes(record.approval_status || '') && <Button type="link" size="small" icon={<DeleteOutlined />} disabled={!canApprove} title={canApprove ? undefined : '缺少 approval:approve 权限'} onClick={() => void act(record, 'revoke')}>撤销</Button>}
        </Space>
      ),
    },
  ];

  return <div className="page-stack">
    {isMockMode && <DemoBanner note="审批数据为合成演示。" />}
    <div className="page-toolbar">
      <Paragraph type="secondary" style={{ margin: 0 }}>审批内容来自真实授权请求；批准仅记录决策并向运行任务派发信号，工具实际执行结果以任务详情为准。</Paragraph>
      <Button icon={<ReloadOutlined />} onClick={reload}>刷新</Button>
    </div>
    {feedback && <Alert type="info" message={feedback} closable onClose={() => setFeedback(null)} />}
    {!isMockMode && (!canApprove || !canRead) && <Alert type="warning" showIcon message={!canRead
      ? '当前账号没有审批读取权限，无法查看审批内容；请登录具有 approval:read 或审批权限的账号。'
      : '当前账号没有 approval:approve 权限，只能查看审批内容，不能批准或拒绝；请使用审批人账号登录。'} />}
    <div style={{ marginBottom: 12, maxWidth: 520 }}>
      <Input.TextArea rows={2} placeholder="审批理由（可选）" value={reason} onChange={(e) => setReason(e.target.value)} />
    </div>
    <Table rowKey="approval_id" loading={query.loading} dataSource={approvals} columns={columns} pagination={{ pageSize: 12}} scroll={{ x: 1900 }} />
  </div>;
}

function EnvelopeView({ approvalId, record }: { approvalId: string; record: ApprovalRecord }) {
  const query = useAsyncData(() => api.getApprovalActionEnvelope(approvalId), [approvalId]);
  if (query.error) return <ApiUnavailable error={query.error} feature="审批详情" />;
  const env: ApprovalActionEnvelope | null = query.data;
  if (query.loading || !env) return <Text type="secondary">加载中…</Text>;
  const approver = record.approver_id || record.approver;
  return <Descriptions column={1} size="small" bordered>
    <Descriptions.Item label="申请事项">{toolLabel[env.tool_name] || env.tool_name}</Descriptions.Item>
    <Descriptions.Item label="操作对象">{env.target_resource_id || '未指定'}</Descriptions.Item>
    <Descriptions.Item label="操作类型">{operationLabel[env.operation_type] || env.operation_type || '—'}</Descriptions.Item>
    <Descriptions.Item label="申请参数">{humanArguments(env.canonical_arguments)}</Descriptions.Item>
    <Descriptions.Item label="参数摘要">{env.canonical_arguments_hash ? <Tooltip title={env.canonical_arguments_hash}><Text code>{shortId(env.canonical_arguments_hash)}</Text></Tooltip> : '—'}</Descriptions.Item>
    <Descriptions.Item label="动作摘要">{env.action_hash ? <Tooltip title={env.action_hash}><Text code>{shortId(env.action_hash)}</Text></Tooltip> : '—'}</Descriptions.Item>
    <Descriptions.Item label="策略版本">{env.policy_version || 'v1'}</Descriptions.Item>
    <Descriptions.Item label="风险等级"><Tag color={env.risk_level === 'HIGH' ? 'error' : 'warning'}>{riskLabel[env.risk_level] || env.risk_level || '高风险'}</Tag></Descriptions.Item>
    <Descriptions.Item label="申请人">{userLabel[env.requested_by] || env.requested_by}</Descriptions.Item>
    <Descriptions.Item label="审批人">{approver ? (userLabel[approver] || approver) : '待分配'}</Descriptions.Item>
    <Descriptions.Item label="有效期">{env.issued_at ? formatTime(env.issued_at) : '—'} → {env.expires_at ? formatTime(env.expires_at) : '—'}</Descriptions.Item>
    <Descriptions.Item label="状态"><Tag color={statusColor[env.approval_status] || 'default'}>{statusLabel[env.approval_status] || env.approval_status}</Tag></Descriptions.Item>
    {record.reason && <Descriptions.Item label="审批理由">{record.reason}</Descriptions.Item>}
  </Descriptions>;
}

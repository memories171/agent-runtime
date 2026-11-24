import { useMemo, useState } from 'react';
import {
  Alert, Button, Card, Col, Descriptions, Drawer, Empty, Row, Select, Space, Steps, Table, Tabs, Tag, Tooltip, Typography,
} from 'antd';
import type { TableColumnsType } from 'antd';
import { ApiOutlined, ClockCircleOutlined, ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { api, isMockMode } from '../api';
import { useAsyncData } from '../hooks/useAsyncData';
import { useApp } from '../state/AppContext';
import { ApiUnavailable, DemoBanner, formatTime, MetricCard, shortId, StatusTag } from '../components/Ui';
import type {
  ApprovalRecord, CheckpointDetail, CheckpointRecord, RecoveryStage, RunRecord,
  ToolInvocationRecord, WorkerFailure,
} from '../types';

const { Text } = Typography;

const persistenceColor: Record<string, string> = {
  AVAILABLE_FOR_RECOVERY: 'success', PERSISTED: 'success', VERIFYING: 'processing',
  UPLOADING: 'processing', SERIALIZING: 'processing', CREATED: 'default',
  CHECKPOINT_FAILED: 'error', ORPHANED: 'error', RETRY: 'warning',
};

const invocationColor: Record<ToolInvocationRecord['status'], string> = {
  CONFIRMED: 'success', FAILED: 'error', UNKNOWN: 'warning',
  RECONCILIATION_REQUIRED: 'warning', DISPATCHED: 'processing', CREATED: 'default',
};

const approvalColor: Record<string, string> = {
  CONSUMED: 'success', APPROVED: 'success', PENDING: 'processing',
  REJECTED: 'error', EXPIRED: 'default', REVOKED: 'default', INVALIDATED: 'warning',
};

const persistenceLabel: Record<string, string> = {
  AVAILABLE_FOR_RECOVERY: '可用于恢复', PERSISTED: '已持久化', VERIFYING: '校验中',
  UPLOADING: '上传中', SERIALIZING: '序列化中', CREATED: '已创建', SAVED: '已保存',
  CHECKPOINT_FAILED: '快照失败', ORPHANED: '已孤立', RETRY: '重试中',
};
const checkpointStatusLabel: Record<string, string> = {
  RESTORED: '已恢复', CORRUPTED: '已损坏', SAVED: '已保存', CREATED: '已创建',
};
const recoveryStatusLabel: Record<string, string> = {
  RECOVERED: '已恢复', RECOVERING: '恢复中', FAILED: '恢复失败', PENDING: '待恢复',
};
const invocationStatusLabel: Record<string, string> = {
  CONFIRMED: '已确认', FAILED: '失败', UNKNOWN: '未知', RECONCILIATION_REQUIRED: '需要对账',
  DISPATCHED: '已派发', CREATED: '已创建',
};
const approvalStatusLabel: Record<string, string> = {
  CONSUMED: '已使用', APPROVED: '已通过', PENDING: '待审批', REJECTED: '已驳回',
  EXPIRED: '已过期', REVOKED: '已撤销', INVALIDATED: '已失效',
};
const reconciliationStatusLabel: Record<string, string> = {
  RESOLVED: '已处理', PENDING: '待处理', FAILED: '失败',
};
const pauseStateLabel: Record<string, string> = {
  NONE: '无', REQUESTED: '暂停请求已提交', PAUSING: '正在安全暂停', PAUSED: '已暂停',
  RESUME_REQUESTED: '恢复请求已提交', RESUMING: '正在恢复',
};
const runStatusLabel: Record<string, string> = {
  CREATED: '已创建', QUEUED: '排队中', RUNNING: '运行中', PAUSED: '已暂停',
  WAITING_INPUT: '等待输入', WAITING_APPROVAL: '等待审批', SUCCEEDED: '已完成',
  FAILED: '执行失败', CANCELLED: '已取消', TIMED_OUT: '已超时',
};
const stageLabel: Record<string, string> = {
  RUNNING: '工作流已启动', CHECKPOINT_SAVED: '快照已保存', WORKER_FAILED: '工作节点故障',
  TEMPORAL_HISTORY_REPLAY: '历史事件重放', WORKER_RECONNECTED: '工作节点重连',
  RECOVERING: '恢复中', WORKFLOW_RESUMED: '工作流已恢复', SUCCEEDED: '执行成功',
};

function cn(labels: Record<string, string>, value?: string | null): string {
  return value ? labels[value] || value : '—';
}

function zhText(value: string): string {
  return value
    .replace(/TEMPORAL_HISTORY_REPLAY/g, '历史重放')
    .replace(/WORKER_RECONNECTED/g, '工作节点重连')
    .replace(/WORKFLOW_RESUMED/g, '工作流已恢复')
    .replace(/CHECKPOINT_SAVED/g, '快照已保存')
    .replace(/WORKER_FAILED/g, '工作节点故障')
    .replace(/SUCCEEDED/g, '已完成')
    .replace(/RECOVERED/g, '已恢复')
    .replace(/RECOVERING/g, '恢复中')
    .replace(/FAILED/g, '失败')
    .replace(/RUNNING/g, '运行中')
    .replace(/Temporal/g, '时序引擎')
    .replace(/Workflow/g, '工作流')
    .replace(/Worker/g, '工作节点')
    .replace(/\bRun\b/g, '运行');
}

function snapshotLabel(record: CheckpointRecord): string {
  const sequence = record.checkpoint_sequence ?? record.step;
  return sequence === undefined || sequence === null ? `快照 ${shortId(record.checkpoint_id)}` : `快照 #${sequence}`;
}

function runLabel(runId: string): string {
  return `运行 ${shortId(runId)}`;
}

function jsonRows(value: unknown): React.ReactNode {
  if (value === null || value === undefined || (Array.isArray(value) && value.length === 0) || (typeof value === 'object' && Object.keys(value as object).length === 0)) {
    return <Text type="secondary">—</Text>;
  }
  return <pre style={{ margin: 0, maxHeight: 180, overflow: 'auto', fontSize: 11 }}>{JSON.stringify(value, null, 2)}</pre>;
}

export function CheckpointPage() {
  const { refreshToken, identity, requestRefresh } = useApp();
  const [runFilter, setRunFilter] = useState<string>('ALL');
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedCheckpointId, setSelectedCheckpointId] = useState<string | null>(null);

  const runsQuery = useAsyncData(() => api.listRuns(100), [refreshToken, identity.tenantId]);
  const checkpointQuery = useAsyncData(() => api.listCheckpoints(), [refreshToken, identity.tenantId]);
  const failureQuery = useAsyncData(() => api.listWorkerFailures(), [refreshToken, identity.tenantId]);
  const detailQuery = useAsyncData(
    () => selectedCheckpointId ? api.getCheckpoint(selectedCheckpointId) : Promise.resolve(null),
    [selectedCheckpointId, refreshToken],
  );
  const stagesQuery = useAsyncData(
    () => selectedRunId ? api.getRecoveryStages(selectedRunId) : Promise.resolve([]),
    [selectedRunId, refreshToken],
  );
  const invocationQuery = useAsyncData(
    () => selectedRunId ? api.listToolInvocations(selectedRunId) : Promise.resolve([]),
    [selectedRunId, refreshToken],
  );
  const approvalQuery = useAsyncData(
    () => selectedRunId ? api.listRunApprovals(selectedRunId) : Promise.resolve([]),
    [selectedRunId, refreshToken],
  );

  const runs = runsQuery.data || [];
  const checkpoints = checkpointQuery.data || [];
  const failures = failureQuery.data || [];
  const filteredCheckpoints = useMemo(
    () => runFilter === 'ALL' ? checkpoints : checkpoints.filter((item) => item.run_id === runFilter),
    [checkpoints, runFilter],
  );
  const filteredFailures = useMemo(
    () => runFilter === 'ALL' ? failures : failures.filter((item) => item.run_id === runFilter),
    [failures, runFilter],
  );
  const recovered = failures.filter((item) => item.recovery_status === 'RECOVERED').length;
  const recoveryRate = failures.length ? Math.round((recovered / failures.length) * 100) : 0;
  const avgRetries = failures.length ? (failures.reduce((sum, item) => sum + item.retry_count, 0) / failures.length).toFixed(1) : '—';
  const selectedRun: RunRecord | null = runs.find((run) => run.run_id === selectedRunId) || null;
  const detail: CheckpointDetail | null = detailQuery.data || null;

  const checkpointColumns: TableColumnsType<CheckpointRecord> = [
    { title: '快照编号', dataIndex: 'checkpoint_id', width: 150, render: (value: string, record) => (
      <Tooltip title={value}><Button type="link" size="small" onClick={() => setSelectedCheckpointId(value)}>{snapshotLabel(record)}</Button></Tooltip>
    ) },
    { title: '运行编号', dataIndex: 'run_id', width: 150, render: (value: string) => (
      <Tooltip title={value}><Button type="link" size="small" onClick={() => setRunFilter(value)}>{runLabel(value)}</Button></Tooltip>
    ) },
    { title: '创建时间', dataIndex: 'created_at', width: 160, render: (value: string) => formatTime(value) },
    { title: '序号', dataIndex: 'checkpoint_sequence', width: 64, render: (value: number | undefined, r) => value ?? r.step },
    { title: '步骤', dataIndex: 'step', width: 60 },
    {
      title: '快照大小', dataIndex: 'blob_size_bytes', width: 110,
      render: (value: number | undefined, r) => value ? `${(value / 1024).toFixed(1)} KB` : `${r.size_kb} KB`,
    },
    {
      title: '状态摘要', dataIndex: 'state_digest', width: 130,
      render: (value: string | undefined) => value ? <Text code>{shortId(value)}</Text> : '—',
    },
    {
      title: '持久化状态', dataIndex: 'persistence_status', width: 150,
      render: (value: string | undefined) => <Tag color={persistenceColor[value || ''] || 'default'}>{cn(persistenceLabel, value || 'SAVED')}</Tag>,
    },
    { title: '结构版本', dataIndex: 'schema_version', width: 80, render: (v: string | undefined) => v || '—' },
    {
      title: '状态', dataIndex: 'status', width: 90,
      render: (value: string) => <Tag color={value === 'RESTORED' ? 'success' : value === 'CORRUPTED' ? 'error' : 'processing'}>{cn(checkpointStatusLabel, value)}</Tag>,
    },
    { title: '工作节点', dataIndex: 'worker_id', width: 110 },
    {
      title: '幂等键', dataIndex: 'idempotency_key', width: 120,
      render: (value: string) => <Text code>{shortId(value)}</Text>,
    },
    {
      title: '操作', key: 'action', width: 150,
      render: (_, record) => <Space size={0}>
        <Button type="link" size="small" onClick={() => setSelectedCheckpointId(record.checkpoint_id)}>快照详情</Button>
        <Button type="link" size="small" onClick={() => setSelectedRunId(record.run_id)}>恢复历史</Button>
      </Space>,
    },
  ];

  const failureColumns: TableColumnsType<WorkerFailure> = [
    { title: '故障编号', dataIndex: 'failure_id', width: 140, render: (value: string) => <Text code>{shortId(value)}</Text> },
    { title: '运行编号', dataIndex: 'run_id', width: 150, render: (value: string) => (
      <Tooltip title={value}><Button type="link" size="small" onClick={() => setRunFilter(value)}>{runLabel(value)}</Button></Tooltip>
    ) },
    { title: '工作节点', dataIndex: 'worker_id', width: 110 },
    { title: '故障时间', dataIndex: 'occurred_at', width: 160, render: (value: string) => formatTime(value) },
    { title: '原因', dataIndex: 'reason', ellipsis: true },
    { title: '恢复状态', dataIndex: 'recovery_status', width: 100, render: (value: string) => <Tag color={value === 'RECOVERED' ? 'success' : value === 'FAILED' ? 'error' : 'processing'}>{cn(recoveryStatusLabel, value)}</Tag> },
    { title: '恢复次数', dataIndex: 'recovery_count', width: 80 },
    { title: '活动重试', dataIndex: 'activity_retry_count', width: 100 },
    { title: '恢复尝试', dataIndex: 'recovery_attempt', width: 90 },
    { title: '恢复开始', dataIndex: 'recovery_started_at', width: 150, render: (v: string | null) => v ? formatTime(v) : '—' },
    { title: '恢复完成', dataIndex: 'recovery_completed_at', width: 150, render: (v: string | null) => v ? formatTime(v) : '—' },
    { title: '防护令牌', dataIndex: 'fencing_token', width: 80 },
    { title: '恢复位置', dataIndex: 'recovery_position', width: 140 },
    { title: '来源快照', dataIndex: 'recovered_from_checkpoint_id', width: 140, render: (v: string | null) => v ? <Text code>{shortId(v)}</Text> : '—' },
  ];

  const invocationColumns: TableColumnsType<ToolInvocationRecord> = [
    { title: '工具', dataIndex: 'tool_name', render: (v: string) => <Text code>{v}</Text> },
    { title: '状态', dataIndex: 'status', width: 150, render: (v: ToolInvocationRecord['status']) => <Tag color={invocationColor[v]}>{cn(invocationStatusLabel, v)}</Tag> },
    { title: '尝试次数', dataIndex: 'attempt_count', width: 80 },
    { title: '防护令牌', dataIndex: 'fencing_token', width: 80 },
    { title: '幂等键', dataIndex: 'idempotency_key', width: 130, render: (v: string) => <Text code>{shortId(v)}</Text> },
    { title: '参数哈希', dataIndex: 'canonical_arguments_hash', width: 130, render: (v: string) => v ? <Text code>{shortId(v)}</Text> : '—' },
    { title: '对账状态', dataIndex: 'reconciliation_status', width: 110, render: (v: string) => <Tag color={v === 'RESOLVED' ? 'success' : v === 'PENDING' ? 'warning' : 'default'}>{cn(reconciliationStatusLabel, v)}</Tag> },
    { title: '对账结果', dataIndex: 'reconciliation_note', ellipsis: true, render: (v: string | null) => v || '—' },
    { title: '外部引用', dataIndex: 'external_reference', width: 120, render: (v: string | null) => v ? <Text code>{shortId(v)}</Text> : '—' },
    { title: '错误', dataIndex: 'last_error', ellipsis: true, render: (v: string | null) => v || '—' },
  ];

  const approvalColumns: TableColumnsType<ApprovalRecord> = [
    { title: '审批编号', dataIndex: 'approval_id', width: 140, render: (v: string) => <Text code>{shortId(v)}</Text> },
    { title: '工具', dataIndex: 'tool_name', width: 150, render: (v: string) => <Text code>{v || '—'}</Text> },
    { title: '目标资源', dataIndex: 'target_resource_id', width: 120, render: (v: string) => v || '—' },
    { title: '请求人', dataIndex: 'requester', width: 110 },
    { title: '审批人', dataIndex: 'approver', width: 110, render: (v: string) => v || '—' },
    { title: '参数哈希', dataIndex: 'canonical_arguments_hash', width: 130, render: (v: string) => v ? <Text code>{shortId(v)}</Text> : '—' },
    { title: '策略版本', dataIndex: 'policy_version', width: 80, render: (v: string) => v || 'v1' },
    { title: '过期时间', dataIndex: 'expires_at', width: 150, render: (v: string) => v ? formatTime(v) : '—' },
    { title: '状态', dataIndex: 'approval_status', width: 110, render: (v: string) => <Tag color={approvalColor[v] || 'default'}>{cn(approvalStatusLabel, v || 'PENDING')}</Tag> },
  ];

  return <>
    <DemoBanner note="快照、恢复次数与幂等状态均为演示数据。" />
    <Row gutter={[16, 16]} className="metrics-row">
      <Col xs={24} sm={12} xl={6}><MetricCard title="快照数量" value={checkpointQuery.error ? '—' : checkpoints.length} hint="当前租户可见" icon={<ApiOutlined />} /></Col>
      <Col xs={24} sm={12} xl={6}><MetricCard title="工作节点故障" value={failureQuery.error ? '—' : failures.length} hint="含已恢复与恢复中" icon={<ApiOutlined />} tone="blue" /></Col>
      <Col xs={24} sm={12} xl={6}><MetricCard title="恢复成功率" value={failureQuery.error ? '—' : recoveryRate} suffix={failureQuery.error ? '' : '%'} hint="已恢复 / 总故障" icon={<SafetyCertificateOutlined />} tone="violet" /></Col>
      <Col xs={24} sm={12} xl={6}><MetricCard title="平均重试次数" value={failureQuery.error ? '—' : avgRetries} hint="当前故障记录平均" icon={<ClockCircleOutlined />} tone="amber" /></Col>
    </Row>
    <Card className="panel-card filter-card" bordered={false}>
      <Space wrap className="filter-row">
        <Select value={runFilter} onChange={setRunFilter} style={{ width: 260 }} placeholder="按运行过滤" options={[
          { label: '全部运行', value: 'ALL' },
          ...runs.map((run) => ({ label: `${shortId(run.run_id)} · ${run.agent_id}`, value: run.run_id })),
        ]} />
        <Button icon={<ReloadOutlined />} onClick={() => requestRefresh()}>刷新</Button>
        <Text type="secondary">{isMockMode ? '演示模式：快照详情需切换到真实接口。' : '接口模式：全部数据来自真实后端。'}</Text>
      </Space>
    </Card>
    {checkpointQuery.error && <ApiUnavailable error={checkpointQuery.error} feature="快照列表" />}
    {failureQuery.error && <ApiUnavailable error={failureQuery.error} feature="工作节点故障与恢复" />}
    {runsQuery.error && <ApiUnavailable error={runsQuery.error} feature="运行列表" />}
    <Card className="panel-card table-card" bordered={false} title={<div className="card-title"><span>快照列表与元数据</span><Text type="secondary">{filteredCheckpoints.length} 条</Text></div>}>
      <Table rowKey="checkpoint_id" columns={checkpointColumns} dataSource={filteredCheckpoints} loading={checkpointQuery.loading} pagination={{ pageSize: 8 }} scroll={{ x: 1_750 }}
        locale={{ emptyText: <Empty description="没有快照记录" /> }} />
    </Card>
    <Card className="panel-card table-card" bordered={false} title={<div className="card-title"><span>工作节点故障与恢复历史</span><Text type="secondary">{filteredFailures.length} 条</Text></div>}>
      <Table rowKey="failure_id" columns={failureColumns} dataSource={filteredFailures} loading={failureQuery.loading} pagination={{ pageSize: 8 }} scroll={{ x: 1_650 }}
        locale={{ emptyText: <Empty description="没有工作节点故障记录" /> }} />
    </Card>

    <Drawer width={760} open={Boolean(selectedCheckpointId)} onClose={() => setSelectedCheckpointId(null)}
      title={<div className="drawer-title"><div><span>快照详情</span><Text code>{selectedCheckpointId ? shortId(selectedCheckpointId) : ''}</Text></div></div>}>
      {detailQuery.error && <ApiUnavailable error={detailQuery.error} feature="快照详情" />}
      {detailQuery.loading && <Text type="secondary">加载快照元数据…</Text>}
      {!detailQuery.loading && !detailQuery.error && !detail && <Empty description="未找到快照" />}
      {detail && <div className="run-detail">
        <Descriptions bordered size="small" column={2} className="drawer-descriptions">
          <Descriptions.Item label="结构版本">{detail.schema_version || '—'}</Descriptions.Item>
          <Descriptions.Item label="持久化状态"><Tag color={persistenceColor[detail.persistence_status || ''] || 'default'}>{cn(persistenceLabel, detail.persistence_status || detail.status || 'SAVED')}</Tag></Descriptions.Item>
          <Descriptions.Item label="状态摘要"><Text code>{detail.state_digest || '—'}</Text></Descriptions.Item>
          <Descriptions.Item label="快照哈希"><Text code>{detail.blob_sha256 || '—'}</Text></Descriptions.Item>
          <Descriptions.Item label="压缩算法">{detail.compression_algorithm || 'zlib'}</Descriptions.Item>
          <Descriptions.Item label="加密算法">{detail.encryption_algorithm || 'AES-256-GCM'} / {detail.encryption_key_id || '—'}</Descriptions.Item>
          <Descriptions.Item label="对象引用" span={2}><Text code>{detail.state_blob_ref || '—'}</Text></Descriptions.Item>
        </Descriptions>
        <div className="section-label">计划状态 — 当前计划 / 已完成 / 待执行</div>
        <Tabs size="small" items={[
          { key: 'plan', label: '计划状态', children: <div>
            <Text type="secondary">当前计划</Text>{jsonRows(detail.envelope?.current_plan)}
            <Text type="secondary">已完成步骤</Text>{jsonRows(detail.envelope?.completed_steps)}
            <Text type="secondary">待执行步骤</Text>{jsonRows(detail.envelope?.pending_steps)}
          </div> },
          { key: 'context', label: '上下文', children: <div>
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="上下文摘要引用">{detail.envelope?.context_summary_ref || '—'}</Descriptions.Item>
              <Descriptions.Item label="消息历史引用">{detail.envelope?.message_history_ref || '—'}</Descriptions.Item>
              <Descriptions.Item label="最后完成步骤">{detail.envelope?.last_completed_step || '—'}</Descriptions.Item>
              <Descriptions.Item label="模型 / 测试框架">{detail.envelope?.model_version || '—'} / {detail.envelope?.harness_version || '—'}</Descriptions.Item>
            </Descriptions>
            <Text type="secondary">记忆引用</Text>{jsonRows(detail.envelope?.memory_references)}
            <Text type="secondary">工具意图</Text>{jsonRows(detail.envelope?.tool_intents)}
            <Text type="secondary">审批绑定</Text>{jsonRows(detail.envelope?.approval_bindings)}
          </div> },
          { key: 'execution', label: '执行状态', children: <div>
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="当前步骤编号">{detail.envelope?.current_step_id || '—'}</Descriptions.Item>
              <Descriptions.Item label="应用事件序号">{detail.envelope?.last_application_event_sequence ?? '—'}</Descriptions.Item>
            </Descriptions>
            <Text type="secondary">当前步骤状态</Text>{jsonRows(detail.envelope?.current_step_state)}
          </div> },
          { key: 'budget', label: '预算', children: <div>
            <Text type="secondary">令牌用量</Text>{jsonRows(detail.envelope?.token_usage)}
            <Text type="secondary">成本用量</Text>{jsonRows(detail.envelope?.cost_usage)}
            <Text type="secondary">执行预算</Text>{jsonRows(detail.envelope?.execution_budget)}
          </div> },
        ]} />
        <Alert className="drawer-alert" type="info" showIcon message="敏感信息已脱敏"
          description="快照详情仅展示元数据；原始令牌与密钥在写入前已脱敏，完整加密快照保存在对象存储。" />
      </div>}
    </Drawer>

    <Drawer width={860} open={Boolean(selectedRun)} onClose={() => setSelectedRunId(null)}
      title={<div className="drawer-title"><div><span>恢复历史</span><Text code>{selectedRun ? shortId(selectedRun.run_id) : ''}</Text></div>{selectedRun && <StatusTag status={selectedRun.status} pauseState={selectedRun.pause_state} />}</div>}>
      {selectedRun && <div className="run-detail">
        <Descriptions bordered size="small" column={2} className="drawer-descriptions">
          <Descriptions.Item label="运行状态">{cn(runStatusLabel, selectedRun.status)}</Descriptions.Item>
          <Descriptions.Item label="暂停状态">{cn(pauseStateLabel, selectedRun.pause_state || 'NONE')}</Descriptions.Item>
          <Descriptions.Item label="智能体编号"><Text code>{selectedRun.agent_id}</Text></Descriptions.Item>
          <Descriptions.Item label="当前步骤">{selectedRun.current_step || '—'}</Descriptions.Item>
        </Descriptions>
        <div className="section-label">历史重放与工作流恢复时间线</div>
        {stagesQuery.error ? <ApiUnavailable error={stagesQuery.error} feature="恢复时间线" /> :
          stagesQuery.loading ? <Text type="secondary">加载时间线…</Text> :
          (stagesQuery.data || []).length === 0 ? <Empty description="无恢复事件" /> :
          <Steps direction="vertical" size="small" items={(stagesQuery.data || []).map((stage: RecoveryStage) => ({
            title: cn(stageLabel, stage.label),
            status: stage.status,
            description: <div className="stage-desc">
              <span>{zhText(stage.time)}</span><span>{zhText(stage.description)}</span>
              {stage.recovery_attempt !== undefined && <span>恢复尝试 {stage.recovery_attempt}</span>}
              {stage.activity_retry_count !== undefined && <span>活动重试 {stage.activity_retry_count}</span>}
              {stage.fencing_token !== undefined && <span>防护令牌 {stage.fencing_token}</span>}
            </div>,
          }))} />}
        <div className="section-label">工具调用记录</div>
        {invocationQuery.error ? <ApiUnavailable error={invocationQuery.error} feature="工具调用记录" /> :
          <Table rowKey="invocation_id" size="small" pagination={false} dataSource={invocationQuery.data || []} columns={invocationColumns}
            scroll={{ x: 1_200 }} loading={invocationQuery.loading} locale={{ emptyText: <Empty description="无工具调用记录" /> }} />}
        <div className="subtable-gap" />
        <div className="section-label">审批绑定 / 参数哈希 / 有效期</div>
        {approvalQuery.error ? <ApiUnavailable error={approvalQuery.error} feature="审批绑定" /> :
          <Table rowKey="approval_id" size="small" pagination={false} dataSource={approvalQuery.data || []} columns={approvalColumns}
            scroll={{ x: 1_100 }} loading={approvalQuery.loading} locale={{ emptyText: <Empty description="无审批绑定记录" /> }} />}
        <div className="subtable-gap" />
        <div className="section-label">该运行的快照</div>
        <Table rowKey="checkpoint_id" size="small" pagination={false} dataSource={checkpoints.filter((item) => item.run_id === selectedRun.run_id)} columns={checkpointColumns.slice(0, 8)} scroll={{ x: 1_200 }} locale={{ emptyText: <Empty description="无快照" /> }} />
        <div className="subtable-gap" />
        <div className="section-label">该运行的工作节点故障</div>
        <Table rowKey="failure_id" size="small" pagination={false} dataSource={failures.filter((item) => item.run_id === selectedRun.run_id)} columns={failureColumns.slice(0, 9)} scroll={{ x: 1_100 }} locale={{ emptyText: <Empty description="无工作节点故障" /> }} />
      </div>}
    </Drawer>
  </>;
}

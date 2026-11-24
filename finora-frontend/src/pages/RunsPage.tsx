import { useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Descriptions, Drawer, Empty, Input, Popconfirm, Progress, Row, Col, Select,
  Space, Switch, Table, Tabs, Tag, Timeline, Typography,
} from 'antd';
import { DeleteOutlined, EyeOutlined, PauseCircleOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons';
import { api } from '../api';
import { useAuth } from '../auth/AuthProvider';
import { useAsyncData } from '../hooks/useAsyncData';
import { useApp } from '../state/AppContext';
import { CreateRunModal } from '../components/CreateRunModal';
import { ContextInspector } from '../components/ContextInspector';
import { RunGraphTab } from '../components/RunGraphTab';
import {
  ApiUnavailable, DemoBanner, formatDuration, formatTime, money, shortId, StatusTag,
} from '../components/Ui';
import type { CheckpointRecord, RunRecord, RunStatus, ToolInvocationRecord, WorkerFailure } from '../types';

const { Text, Paragraph } = Typography;

const statusOptions: Array<{ label: string; value: RunStatus | 'ALL' }> = [
  { label: '全部状态', value: 'ALL' },
  { label: '创建中', value: 'CREATED' },
  { label: '排队中', value: 'QUEUED' },
  { label: '运行中', value: 'RUNNING' },
  { label: '已暂停', value: 'PAUSED' },
  { label: '等待输入', value: 'WAITING_INPUT' },
  { label: '等待审批', value: 'WAITING_APPROVAL' },
  { label: '已完成', value: 'SUCCEEDED' },
  { label: '失败', value: 'FAILED' },
  { label: '已取消', value: 'CANCELLED' },
  { label: '已超时', value: 'TIMED_OUT' },
];

export function RunsPage() {
  const { refreshToken, identity, isMockMode, notify, requestRefresh } = useApp();
  const auth = useAuth();
  const [keyword, setKeyword] = useState('');
  const [statusFilter, setStatusFilter] = useState<RunStatus | 'ALL'>('ALL');
  const [longOnly, setLongOnly] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [approvalReason, setApprovalReason] = useState('已完成独立复核，请记录审批意见。');
  const [inputReason, setInputReason] = useState('');
  const [submittingInput, setSubmittingInput] = useState(false);
  const [clock, setClock] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  const runsQuery = useAsyncData(() => api.listRuns(100), [refreshToken, identity.tenantId]);
  const telemetryQuery = useAsyncData(() => api.listRunTelemetries(), [refreshToken]);
  const checkpointQuery = useAsyncData(() => api.listCheckpoints(), [refreshToken, identity.tenantId]);
  const failureQuery = useAsyncData(() => api.listWorkerFailures(), [refreshToken, identity.tenantId]);
  const eventQuery = useAsyncData(() => selectedId ? api.getEvents(selectedId) : Promise.resolve([]), [selectedId, refreshToken]);
  const invocationQuery = useAsyncData(() => selectedId ? api.listToolInvocations(selectedId) : Promise.resolve([]), [selectedId, refreshToken]);

  const runs = runsQuery.data || [];
  const telemetry = telemetryQuery.data?.find((item) => item.run_id === selectedId);
  const selectedRun = runs.find((run) => run.run_id === selectedId) || null;
  const checkpoints = (checkpointQuery.data || []).filter((item) => item.run_id === selectedId);
  const failures = (failureQuery.data || []).filter((item) => item.run_id === selectedId);

  // 只有真正在运行的状态才实时走秒；等待审批/暂停/排队都不算运行，时长交给后端（为 0）。
  const TIMED_STATUSES = ['RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT'];
  const liveDuration = (run: RunRecord): number => {
    const reported = run.duration_seconds || 0;
    if (run.status !== 'RUNNING') return reported;
    const started = run.created_at ? Date.parse(run.created_at) : Number.NaN;
    if (Number.isNaN(started)) return reported;
    return Math.max(reported, Math.max(0, (clock - started) / 1_000));
  };

  const filteredRuns = useMemo(() => runs.filter((run) => {
    const matchKeyword = !keyword || run.run_id.toLowerCase().includes(keyword.toLowerCase())
      || run.prompt.toLowerCase().includes(keyword.toLowerCase())
      || (run.agent_id || '').toLowerCase().includes(keyword.toLowerCase());
    const matchStatus = statusFilter === 'ALL' || run.status === statusFilter;
    const matchLong = !longOnly || liveDuration(run) >= 1_800;
    return matchKeyword && matchStatus && matchLong;
  }), [runs, keyword, statusFilter, longOnly, clock]);

  const actOnRun = async (run: RunRecord, action: 'pause' | 'resume' | 'cancel') => {
    try {
      if (action === 'pause') await api.pauseRun(run.run_id);
      else if (action === 'resume') await api.resumeRun(run.run_id);
      else await api.runAction(run.run_id, action);
      notify('success', action === 'pause' ? '暂停请求已提交' : action === 'resume' ? '恢复请求已提交' : '取消信号已发送');
    } catch (error) {
      const message = error instanceof Error ? error.message : '操作失败';
      notify('error', message.includes('workflow signal failed')
        ? '该任务的执行流程已结束或丢失，无法通过信号取消；请联系管理员清理，或删除该任务。'
        : message);
    } finally {
      requestRefresh();
    }
  };

  const provideInput = async (run: RunRecord) => {
    const value = inputReason.trim();
    if (!value) {
      notify('error', '请输入补充内容');
      return;
    }
    setSubmittingInput(true);
    try {
      await api.provideRunInput(run.run_id, value);
      setInputReason('');
      notify('success', '补充说明已提交');
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '提交补充说明失败');
    } finally {
      setSubmittingInput(false);
      requestRefresh();
    }
  };

  const canDeleteRun = isMockMode || Boolean(
    auth.profile?.permissions?.some((permission) => permission === 'run:delete' || permission === 'runs:manage:tenant'),
  );
  const canReconcile = isMockMode || Boolean(
    auth.profile?.permissions?.some((permission) => permission === 'approval:approve' || permission === 'runs:manage:tenant'),
  );
  const isDeletable = (run: RunRecord) => ['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'CREATED', 'QUEUED'].includes(run.status);
  const deleteDescription = (run: RunRecord) => ['CREATED', 'QUEUED'].includes(run.status)
    ? '排队中的任务会直接取消并从监控列表移除，审计记录保留。'
    : '任务将从监控列表移除，审计记录保留。';

  const deleteRun = async (run: RunRecord) => {
    try {
      await api.deleteRun(run.run_id);
      notify('success', '任务已删除');
      if (selectedId === run.run_id) setSelectedId(null);
      requestRefresh();
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '删除失败');
    }
  };

  const reconcileInvocation = async (record: ToolInvocationRecord, resolved: 'CONFIRMED' | 'FAILED') => {
    try {
      await api.reconcileRun(record.run_id, record.idempotency_key, resolved, '人工确认外部执行结果');
      notify('success', `对账完成：${resolved}`);
      requestRefresh();
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '对账失败');
    }
  };

  const approve = async (run: RunRecord, approved: boolean) => {
    try {
      await api.approveRun(run.run_id, approved, approvalReason);
      notify('success', approved ? '审批已提交' : '拒绝决定已提交');
      requestRefresh();
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '审批失败');
    }
  };

  const columns = [
    {
      title: '事件描述', key: 'event', width: 190,
      render: (_: unknown, record: RunRecord) => <div className="table-primary">
        <b>{record.latest_event || record.current_step || record.status}</b>
        <span><Text code>{shortId(record.run_id)}</Text></span>
      </div>,
    },
    {
      title: '任务 / Agent', dataIndex: 'prompt', ellipsis: true,
      render: (_: string, record: RunRecord) => <div className="table-primary"><b>{record.prompt}</b><span>{record.agent_id}</span></div>,
    },
    { title: 'Model / Harness', key: 'stack', width: 210, render: (_: unknown, record: RunRecord) => <div className="stack-cell"><Text code>{record.model_name || '接口未提供'}</Text><span>{record.harness_version || '接口未提供'}</span></div> },
    { title: '状态', dataIndex: 'status', width: 130, render: (status: RunStatus, record: RunRecord) => <StatusTag status={status} pauseState={record.pause_state} /> },
    {
      title: '运行时长', dataIndex: 'duration_seconds', width: 130,
      render: (_: unknown, record: RunRecord) => {
        if (!TIMED_STATUSES.includes(record.status)) return <Text type="secondary">—</Text>;
        const value = liveDuration(record);
        const isLong = value >= 1_800;
        return <span className={isLong ? 'long-value' : ''}>{formatDuration(value)}{isLong ? '  LONG · >30min' : ''}</span>;
      },
    },
    {
      title: '进度 / 心跳', key: 'progress', width: 150,
      render: (_: unknown, record: RunRecord) => <div className="progress-cell"><Progress percent={record.progress_pct || 0} size="small" showInfo={false} /><span>{formatTime(record.heartbeat_at)}</span></div>,
    },
    { title: 'Token / 成本', key: 'cost', width: 150, render: (_: unknown, record: RunRecord) => <div className="stack-cell"><span>{((record.token_input || 0) + (record.token_output || 0)).toLocaleString()} tokens</span><span>{money(record.estimated_cost_usd)}</span></div> },
    {
      title: '', key: 'action', width: 150,
      render: (_: unknown, record: RunRecord) => <Space size={0} onClick={(event) => event.stopPropagation()}>
        <Button type="text" icon={<EyeOutlined />} onClick={() => setSelectedId(record.run_id)}>详情</Button>
        {canDeleteRun && (
          <Popconfirm
            title="删除该任务？"
            description={isDeletable(record) ? deleteDescription(record) : '请先取消任务，再执行删除。'}
            okText="删除"
            cancelText="取消"
            okButtonProps={{ disabled: !isDeletable(record) }}
            onConfirm={() => void deleteRun(record)}
          >
            <Button type="text" danger icon={<DeleteOutlined />} disabled={!isDeletable(record)}>删除</Button>
          </Popconfirm>
        )}
      </Space>,
    },
  ];

  const toolColumns = [
    { title: 'Tool', dataIndex: 'tool', render: (value: string) => <Text code>{value}</Text> },
    { title: 'MCP Server', dataIndex: 'server' },
    { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'SUCCESS' ? 'success' : value === 'FAILED' ? 'error' : 'processing'}>{value}</Tag> },
    { title: '耗时', dataIndex: 'duration_ms', render: (value: number) => `${value} ms` },
    { title: '风险', dataIndex: 'risk_level', render: (value: string) => <Tag color={value === 'high' || value === 'critical' ? 'volcano' : 'blue'}>{value}</Tag> },
    { title: 'Idempotency Key', dataIndex: 'idempotency_key', render: (value: string) => <Text code>{shortId(value)}</Text> },
  ];

  const llmColumns = [
    { title: 'Call ID', dataIndex: 'call_id', render: (value: string) => <Text code>{shortId(value)}</Text> },
    { title: 'Model', dataIndex: 'model' },
    { title: '用途', dataIndex: 'purpose' },
    { title: 'Tokens', key: 'tokens', render: (_: unknown, record: { prompt_tokens: number; completion_tokens: number }) => `${record.prompt_tokens} / ${record.completion_tokens}` },
    { title: '延迟', dataIndex: 'latency_ms', render: (value: number) => `${value} ms` },
    { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'SUCCESS' ? 'success' : 'error'}>{value}</Tag> },
  ];

  const invocationColumns = [
    { title: 'Tool', dataIndex: 'tool_name', render: (value: string) => <Text code>{value}</Text> },
    { title: '状态', dataIndex: 'status', render: (value: ToolInvocationRecord['status']) => (
      <Tag color={value === 'CONFIRMED' ? 'success' : value === 'FAILED' ? 'error' : value === 'UNKNOWN' || value === 'RECONCILIATION_REQUIRED' ? 'warning' : 'processing'}>{value}</Tag>
    ) },
    { title: 'Attempt', dataIndex: 'attempt_count' },
    { title: 'Fencing', dataIndex: 'fencing_token' },
    { title: '参数 Hash', dataIndex: 'canonical_arguments_hash', render: (value: string) => value ? <Text code>{shortId(value)}</Text> : '—' },
    { title: 'Idempotency Key', dataIndex: 'idempotency_key', render: (value: string) => <Text code>{shortId(value)}</Text> },
    { title: '对账', dataIndex: 'reconciliation_status', render: (value: ToolInvocationRecord['reconciliation_status']) => <Tag color={value === 'RESOLVED' ? 'success' : value === 'PENDING' ? 'warning' : 'default'}>{value}</Tag> },
    { title: '外部引用', dataIndex: 'external_reference', render: (value: string | null) => value ? <Text code>{shortId(value)}</Text> : '—' },
    {
      title: '操作', key: 'action', render: (_: unknown, record: ToolInvocationRecord) => (
        record.status === 'UNKNOWN' || record.status === 'RECONCILIATION_REQUIRED'
          ? <Space size={0}>
              <Button type="link" size="small" disabled={!canReconcile} onClick={() => void reconcileInvocation(record, 'CONFIRMED')}>确认已执行</Button>
              <Button type="link" size="small" danger disabled={!canReconcile} onClick={() => void reconcileInvocation(record, 'FAILED')}>确认失败</Button>
            </Space>
          : '—'
      ),
    },
  ];

  const checkpointColumns = [
    { title: 'Checkpoint ID', dataIndex: 'checkpoint_id', render: (value: string) => <Text code>{shortId(value)}</Text> },
    { title: '创建时间', dataIndex: 'created_at', render: formatTime },
    { title: '步骤', dataIndex: 'step' },
    { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'RESTORED' ? 'success' : 'processing'}>{value}</Tag> },
  ];

  const failureColumns = [
    { title: '故障时间', dataIndex: 'occurred_at', render: formatTime },
    { title: 'Worker', dataIndex: 'worker_id' },
    { title: '原因', dataIndex: 'reason', ellipsis: true },
    { title: '恢复状态', dataIndex: 'recovery_status', render: (value: string) => <Tag color={value === 'RECOVERED' ? 'success' : value === 'FAILED' ? 'error' : 'processing'}>{value}</Tag> },
    { title: '恢复 / 重试', key: 'counts', render: (_: unknown, record: WorkerFailure) => `${record.recovery_count} / ${record.retry_count}` },
  ];

  return <>
    {isMockMode && <DemoBanner note="长任务时长、Token、成本、心跳、工具与 LLM 调用记录均为 DEMO / MOCK。" />}
    <Card className="panel-card filter-card" bordered={false}>
      <Space wrap className="filter-row">
        <Input.Search allowClear placeholder="搜索 Run ID / 任务 / Agent" value={keyword} onSearch={setKeyword} onChange={(event) => setKeyword(event.target.value)} style={{ width: 300 }} />
        <Select value={statusFilter} onChange={setStatusFilter} options={statusOptions} style={{ width: 150 }} />
        <Space size={8}><Switch checked={longOnly} onChange={setLongOnly} /><Text type="secondary">仅看超过 30 分钟</Text></Space>
        <Button icon={<ReloadOutlined />} onClick={() => requestRefresh()}>刷新</Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>创建任务</Button>
      </Space>
    </Card>
    {runsQuery.error && <Alert className="api-unavailable" showIcon type="error" message="Run 列表加载失败" description={runsQuery.error.message} />}
    <Card className="panel-card table-card" bordered={false} title={<div className="card-title"><span>Agent 长任务监控</span><Text type="secondary">共 {runs.length} 条 Run，当前筛选 {filteredRuns.length} 条 · 其中 {filteredRuns.filter((r) => TIMED_STATUSES.includes(r.status) && liveDuration(r) >= 1_800).length} 条超过 30 分钟（LONG）</Text></div>}>
      <Table rowKey="run_id" columns={columns} dataSource={filteredRuns} loading={runsQuery.loading} pagination={{ pageSize: 10, showSizeChanger: true }}
        onRow={(record) => ({ onClick: () => setSelectedId(record.run_id), className: 'clickable-row' })}
        locale={{ emptyText: <Empty description="没有符合条件的任务" /> }} />
    </Card>

    <CreateRunModal open={createOpen} onClose={() => setCreateOpen(false)} onCreated={() => requestRefresh()} />

    <Drawer width={760} open={Boolean(selectedRun)} onClose={() => setSelectedId(null)}
      title={<div className="drawer-title"><div><span>Run 详情</span><Text code>{selectedRun ? shortId(selectedRun.run_id) : ''}</Text></div>{selectedRun && <StatusTag status={selectedRun.status} pauseState={selectedRun.pause_state} />}</div>}>
      {selectedRun && <div className="run-detail">
        <Row gutter={[12, 12]}>
          <Col span={8}><div className="mini-stat"><span>实际运行时长</span>{TIMED_STATUSES.includes(selectedRun.status)
            ? <b className={liveDuration(selectedRun) > 1_800 ? 'long-value' : ''}>{formatDuration(liveDuration(selectedRun))}</b>
            : <b>—</b>}</div></Col>
          <Col span={8}><div className="mini-stat"><span>Token 总量</span><b>{((selectedRun.token_input || 0) + (selectedRun.token_output || 0)).toLocaleString()}</b></div></Col>
          <Col span={8}><div className="mini-stat"><span>预计成本</span><b>{money(selectedRun.estimated_cost_usd)}</b></div></Col>
        </Row>
        <Descriptions bordered size="small" column={1} className="drawer-descriptions">
          <Descriptions.Item label="Run ID"><Text code>{selectedRun.run_id}</Text></Descriptions.Item>
          <Descriptions.Item label="Agent ID"><Text code>{selectedRun.agent_id}</Text></Descriptions.Item>
          <Descriptions.Item label="Model"><Text code>{selectedRun.model_name || '接口未提供'}</Text></Descriptions.Item>
          <Descriptions.Item label="Harness Version"><Text code>{selectedRun.harness_version || '接口未提供'}</Text></Descriptions.Item>
          <Descriptions.Item label="Tenant / Owner">{selectedRun.tenant_id} / {selectedRun.user_id}</Descriptions.Item>
          <Descriptions.Item label="Heartbeat">{formatTime(selectedRun.heartbeat_at)}</Descriptions.Item>
          <Descriptions.Item label="Deadline">{formatTime(selectedRun.deadline_at)}</Descriptions.Item>
          <Descriptions.Item label="当前步骤">{selectedRun.current_step || '—'}</Descriptions.Item>
          <Descriptions.Item label="任务描述">{selectedRun.prompt}</Descriptions.Item>
        </Descriptions>
        <div className="drawer-progress"><span>整体进度</span><Progress percent={selectedRun.progress_pct || 0} /></div>
        <div className="drawer-actions">
          {selectedRun.status === 'RUNNING' && <Button icon={<PauseCircleOutlined />} onClick={() => void actOnRun(selectedRun, 'pause')}>暂停</Button>}
          {selectedRun.status === 'PAUSED' && <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => void actOnRun(selectedRun, 'resume')}>恢复</Button>}
          {['CREATED', 'QUEUED', 'RUNNING', 'PAUSED', 'WAITING_INPUT', 'WAITING_APPROVAL'].includes(selectedRun.status) && <Button danger icon={<StopOutlined />} onClick={() => void actOnRun(selectedRun, 'cancel')}>取消</Button>}
          {canDeleteRun && isDeletable(selectedRun) && (
            <Popconfirm title="删除该任务？" description={deleteDescription(selectedRun)} okText="删除" cancelText="取消" onConfirm={() => void deleteRun(selectedRun)}>
              <Button danger icon={<DeleteOutlined />}>删除任务</Button>
            </Popconfirm>
          )}
          <Button icon={<ReloadOutlined />} onClick={() => requestRefresh()}>刷新</Button>
        </div>
        {selectedRun.status === 'WAITING_INPUT' && <Card size="small" className="approval-card" title="补充输入">
          <Alert type="info" showIcon message="工作流正在等待补充说明" description="请在下方填写 Agent 需要的信息；提交后任务会回到运行中。也可以在实时事件里查看 USER_INPUT_REQUIRED 的具体提示。" />
          <Input.TextArea rows={3} value={inputReason} onChange={(event) => setInputReason(event.target.value)} placeholder="请输入需要补充的信息" style={{ marginTop: 10 }} />
          <Space style={{ marginTop: 10 }}>
            <Button type="primary" loading={submittingInput} disabled={!inputReason.trim()} onClick={() => void provideInput(selectedRun)}>提交输入</Button>
            <Button danger icon={<StopOutlined />} onClick={() => void actOnRun(selectedRun, 'cancel')}>取消任务</Button>
          </Space>
        </Card>}
        {selectedRun.status === 'WAITING_APPROVAL' && <Card size="small" className="approval-card" title="独立审批">
          {identity.userId === selectedRun.user_id
            ? <Alert type="warning" showIcon message="需要另一位授权人员审批" description="当前身份是任务发起人；请切换为合规复核员 Demo Identity。" />
            : <>
              <Alert type="info" showIcon message={`当前审批身份：${identity.userId}`} description="审批仅对当前等待中的动作有效。" />
              <Input.TextArea rows={2} value={approvalReason} onChange={(event) => setApprovalReason(event.target.value)} style={{ marginTop: 10 }} />
              <Space style={{ marginTop: 10 }}><Button type="primary" onClick={() => void approve(selectedRun, true)}>批准</Button><Button danger onClick={() => void approve(selectedRun, false)}>拒绝</Button></Space>
            </>}
        </Card>}
        {selectedRun.error && <Alert className="drawer-alert" type="error" showIcon message="任务错误" description={selectedRun.error} />}
        {selectedRun.result && <Card size="small" title="执行结果" className="result-card"><pre>{JSON.stringify(selectedRun.result, null, 2)}</pre></Card>}
        <Tabs items={[
          {
            key: 'timeline', label: '实时事件 Timeline',
            children: eventQuery.error
              ? <ApiUnavailable error={eventQuery.error} feature="Run 事件" />
              : (eventQuery.data?.length
                ? <Timeline items={eventQuery.data.map((event) => ({
                  color: event.event_type.includes('FAILED') ? 'red' : event.event_type.includes('SUCCEEDED') || event.event_type.includes('RESUMED') ? 'green' : 'blue',
                  children: <div className="event-item"><b>{event.event_type}</b><span>{formatTime(event.created_at)}</span><pre>{JSON.stringify(event.payload, null, 2)}</pre></div>,
                }))} />
                : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂时没有事件记录" />),
          },
          {
            key: 'tools', label: '工具调用历史',
            children: telemetryQuery.error
              ? <ApiUnavailable error={telemetryQuery.error} feature="工具调用历史" />
              : <Table rowKey="call_id" size="small" pagination={false} dataSource={telemetry?.tool_calls || []} columns={toolColumns} locale={{ emptyText: <Empty description="没有工具调用记录" /> }} />,
          },
          {
            key: 'llm', label: 'LLM 调用记录',
            children: telemetryQuery.error
              ? <ApiUnavailable error={telemetryQuery.error} feature="LLM 调用记录" />
              : <Table rowKey="call_id" size="small" pagination={false} dataSource={telemetry?.llm_calls || []} columns={llmColumns} locale={{ emptyText: <Empty description="没有 LLM 调用记录" /> }} />,
          },
          {
            key: 'recovery', label: 'Checkpoint / Recovery',
            children: checkpointQuery.error || failureQuery.error
              ? <ApiUnavailable error={checkpointQuery.error || failureQuery.error} feature="Checkpoint / Recovery" />
              : <>
                <Paragraph type="secondary">Checkpoint：{checkpoints.length} 条 · Worker 故障：{failures.length} 条 · 恢复次数：{failures.reduce((sum, item) => sum + item.recovery_count, 0)}</Paragraph>
                <Table rowKey="checkpoint_id" size="small" pagination={false} dataSource={checkpoints as CheckpointRecord[]} columns={checkpointColumns} locale={{ emptyText: <Empty description="无检查点记录" /> }} />
                <div className="subtable-gap" />
                <Table rowKey="failure_id" size="small" pagination={false} dataSource={failures} columns={failureColumns} locale={{ emptyText: <Empty description="无 Worker 故障记录" /> }} />
              </>,
          },
          {
            key: 'invocations', label: 'Invocation Journal',
            children: invocationQuery.error
              ? <ApiUnavailable error={invocationQuery.error} feature="工具调用 Journal" />
              : <Table rowKey="invocation_id" size="small" pagination={false} dataSource={invocationQuery.data || []} columns={invocationColumns} locale={{ emptyText: <Empty description="无工具调用记录" /> }} />,
          },
          {
            key: 'context', label: 'Context Inspector',
            children: <ContextInspector runId={selectedId || ''} refreshToken={refreshToken} />,
          },
          {
            key: 'graph', label: 'Task Graph',
            children: <RunGraphTab runId={selectedId || ''} refreshToken={refreshToken} />,
          },
        ]} />
      </div>}
    </Drawer>
  </>;
}

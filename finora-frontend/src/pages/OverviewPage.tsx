import { useMemo } from 'react';
import { Alert, Button, Card, Col, Empty, Row, Space, Table, Tag, Typography } from 'antd';
import type { TableColumnsType } from 'antd';
import {
  ApiOutlined, ClockCircleOutlined, DatabaseOutlined, LineChartOutlined,
  RobotOutlined, SafetyCertificateOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { api } from '../api';
import { useAsyncData } from '../hooks/useAsyncData';
import { useApp } from '../state/AppContext';
import { useAuth } from '../auth/AuthProvider';
import { DemoBanner, EChart, formatDuration, formatTime, MetricCard, percent, shortId, StatusTag } from '../components/Ui';
import type { RunRecord } from '../types';

const { Paragraph, Text, Title } = Typography;

export function OverviewPage({ onOpenRuns }: { onOpenRuns: () => void }) {
  const { refreshToken, identity, isMockMode, apiHealthy } = useApp();
  const auth = useAuth();
  const runsQuery = useAsyncData(() => api.listRuns(100), [refreshToken, identity.tenantId]);
  const casesQuery = useAsyncData(() => api.listCases(), [refreshToken, identity.tenantId]);
  const healthQuery = useAsyncData(() => api.health(), [refreshToken]);
  const mcpQuery = useAsyncData(() => api.listMcpServers(), [refreshToken]);
  const memoryQuery = useAsyncData(() => api.listMemories(), [refreshToken, identity.tenantId]);
  const checkpointQuery = useAsyncData(() => Promise.all([api.listCheckpoints(), api.listWorkerFailures()]), [refreshToken, identity.tenantId]);
  const benchmarkQuery = useAsyncData(() => api.listBenchmarks(), [refreshToken]);

  const runs = runsQuery.data || [];
  const cases = casesQuery.data || [];
  const memories = memoryQuery.data || [];
  const benchmarks = benchmarkQuery.data || [];
  const [checkpoints, failures] = checkpointQuery.data || [[], []];

  const activeRuns = runs.filter((run) => ['QUEUED', 'RUNNING', 'WAITING_INPUT', 'WAITING_APPROVAL'].includes(run.status)).length;
  const longRuns = runs.filter((run) => (run.duration_seconds || 0) > 1_800).length;
  const waitingApproval = runs.filter((run) => run.status === 'WAITING_APPROVAL').length;
  const recovered = failures.filter((failure) => failure.recovery_status === 'RECOVERED').length;
  const recoveryRate = failures.length ? Math.round((recovered / failures.length) * 100) : 0;
  const healthyMcp = (mcpQuery.data || []).filter((server) => server.status === 'HEALTHY').length;
  const avgDuration = runs.length ? Math.round(runs.reduce((sum, run) => sum + (run.duration_seconds || 0), 0) / runs.length) : 0;
  const avgBenchmark = benchmarks.length ? Math.round(benchmarks.reduce((sum, item) => sum + item.task_success_rate, 0) / benchmarks.length) : 0;
  const openCases = cases.filter((item) => ['OPEN', 'PENDING', 'IN_REVIEW'].includes(item.status.toUpperCase())).length;
  const unavailableFeatures = [
    runsQuery.error && 'Agent Runs',
    casesQuery.error && '复核案件',
    healthQuery.error && '健康检查',
    mcpQuery.error && 'MCP Server',
    memoryQuery.error && 'Memory',
    checkpointQuery.error && 'Checkpoint / Recovery',
    benchmarkQuery.error && 'Benchmark',
  ].filter(Boolean).join('、');

  const trendOption = useMemo(() => {
    const days = Array.from({ length: 7 }, (_, index) => {
      const date = new Date();
      date.setDate(date.getDate() - (6 - index));
      return date;
    });
    const labels = days.map((date) => `${date.getMonth() + 1}/${date.getDate()}`);
    const created = days.map((date) => runs.filter((run) => new Date(run.created_at).toDateString() === date.toDateString()).length);
    const long = days.map((date) => runs.filter((run) => new Date(run.created_at).toDateString() === date.toDateString() && (run.duration_seconds || 0) > 1_800).length);
    return {
      tooltip: { trigger: 'axis' },
      legend: { data: ['Run 数量', '长任务'], right: 0, textStyle: { fontSize: 10 } },
      grid: { left: 34, right: 18, top: 30, bottom: 26 },
      xAxis: { type: 'category', data: labels, axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value', minInterval: 1, axisLabel: { fontSize: 10 } },
      series: [
        { name: 'Run 数量', type: 'bar', data: created, barMaxWidth: 24, itemStyle: { color: '#4aa88f', borderRadius: [4, 4, 0, 0] } },
        { name: '长任务', type: 'line', data: long, smooth: true, symbolSize: 6, itemStyle: { color: '#d99a32' } },
      ],
    };
  }, [runs]);

  const modelOption = useMemo(() => {
    const batches = Array.from(new Set(benchmarks.map((item) => item.batch))).slice(0, 6);
    const models = Array.from(new Set(benchmarks.map((item) => item.model)));
    return {
      tooltip: { trigger: 'axis' },
      legend: { data: models, right: 0, textStyle: { fontSize: 10 } },
      grid: { left: 34, right: 18, top: 30, bottom: 26 },
      xAxis: { type: 'category', data: batches, axisLabel: { fontSize: 9, interval: 0 } },
      yAxis: { type: 'value', min: 60, max: 100, axisLabel: { fontSize: 10, formatter: '{value}%' } },
      series: models.map((model) => ({
        name: model, type: 'line', smooth: true, symbolSize: 6,
        data: batches.map((batch) => {
          const values = benchmarks.filter((item) => item.batch === batch && item.model === model);
          return values.length ? Math.round(values.reduce((sum, item) => sum + item.task_success_rate, 0) / values.length) : null;
        }),
      })),
    };
  }, [benchmarks]);

  const columns: TableColumnsType<RunRecord> = [
    { title: 'Run ID', dataIndex: 'run_id', width: 150, render: (value: string) => <Text code>{shortId(value)}</Text> },
    { title: '任务', dataIndex: 'prompt', ellipsis: true },
    { title: '状态', dataIndex: 'status', width: 110, render: (status: RunRecord['status']) => <StatusTag status={status} /> },
    { title: 'Model / Harness', key: 'stack', width: 190, render: (_, record) => <div className="stack-cell"><Text code>{record.model_name || '—'}</Text><span>{record.harness_version || '接口未提供'}</span></div> },
    { title: '时长', dataIndex: 'duration_seconds', width: 100, render: (value?: number) => <span className={value && value > 1_800 ? 'long-value' : ''}>{formatDuration(value)}</span> },
    { title: '创建时间', dataIndex: 'created_at', width: 160, render: formatTime },
  ];

  return <>
    <DemoBanner note="Dashboard 汇总了 Run、Checkpoint、MCP、Memory 与 Benchmark 的合成指标。" />
    <div className="welcome-row">
      <div>
        <div className="eyebrow">ENTERPRISE AGENT RUNTIME WORKSPACE</div>
        <Title level={2}>Agent Runtime 运营总览</Title>
        <Paragraph>统一观察长任务、恢复能力、MCP / Skills、长期记忆、权限治理与模型实验。</Paragraph>
      </div>
      <Space wrap><Button icon={<ThunderboltOutlined />} onClick={onOpenRuns}>进入长任务中心</Button></Space>
    </div>
    {!isMockMode && <Alert className="dev-alert" showIcon type="info" message="API 模式" description={`Runtime 健康状态：${apiHealthy === null ? '检查中' : apiHealthy ? '正常' : '异常'}。未接入的新能力会明确提示，不会回退到 Mock 数据。`} />}
    {!isMockMode && unavailableFeatures && <Alert className="api-unavailable" showIcon type="warning" message="部分能力接口未接入" description={`${unavailableFeatures} 暂无对应后端接口，页面指标显示为“—”或“未接入”，不会使用 Mock 数据替代。`} />}
    <Row gutter={[16, 16]} className="metrics-row">
      <Col xs={24} sm={12} xl={6}><MetricCard title="总 Run 数量" value={runs.length} hint="当前租户最近 100 条" icon={<ThunderboltOutlined />} /></Col>
      <Col xs={24} sm={12} xl={6}><MetricCard title="Running" value={activeRuns} hint="执行中 / 等待交互或审批" icon={<RobotOutlined />} tone="blue" /></Col>
      <Col xs={24} sm={12} xl={6}><MetricCard title="> 30 分钟长任务" value={longRuns} hint="重点观察运行超过 30 分钟的 Run" icon={<ClockCircleOutlined />} tone="amber" /></Col>
      <Col xs={24} sm={12} xl={6}><MetricCard title="Waiting Approval" value={waitingApproval} hint="等待独立授权" icon={<SafetyCertificateOutlined />} tone="violet" /></Col>
    </Row>
    <Row gutter={[16, 16]} className="metrics-row">
      <Col xs={24} sm={12} xl={6}><MetricCard title="恢复成功率" value={checkpointQuery.error ? '—' : recoveryRate} suffix={checkpointQuery.error ? '' : '%'} hint={`${failures.length} 条 Worker 故障记录`} icon={<ApiOutlined />} /></Col>
      <Col xs={24} sm={12} xl={6}><MetricCard title="MCP 健康状态" value={mcpQuery.error ? '—' : `${healthyMcp}/${mcpQuery.data?.length || 0}`} hint="健康 Server / 总数" icon={<ApiOutlined />} tone="blue" /></Col>
      <Col xs={24} sm={12} xl={6}><MetricCard title="Memory 数量" value={memoryQuery.error ? '—' : memories.length} hint="当前租户可见记忆" icon={<DatabaseOutlined />} tone="violet" /></Col>
      <Col xs={24} sm={12} xl={6}><MetricCard title="平均执行时长" value={formatDuration(avgDuration)} hint="基于当前 Run 列表" icon={<ClockCircleOutlined />} tone="amber" /></Col>
    </Row>
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={12}>
        <Card className="panel-card" bordered={false} title={<div className="card-title"><span>Agent 执行趋势</span><Text type="secondary">最近 7 天 · Run / 长任务</Text></div>}>
          <EChart option={trendOption} height={260} />
        </Card>
      </Col>
      <Col xs={24} xl={12}>
        <Card className="panel-card" bordered={false} title={<div className="card-title"><span>模型性能趋势</span><Text type="secondary">Benchmark 成功率 · {isMockMode ? 'DEMO' : 'API'}</Text></div>}>
          {benchmarkQuery.error ? <Alert showIcon type="warning" message="Benchmark 数据未接入 API" description={benchmarkQuery.error.message} /> : <EChart option={modelOption} height={260} />}
        </Card>
      </Col>
    </Row>
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={16}>
        <Card className="panel-card table-card" bordered={false} title={<div className="card-title"><span>最近 Agent 任务</span><Button type="link" onClick={onOpenRuns}>查看长任务中心 →</Button></div>}>
          <Table rowKey="run_id" columns={columns} dataSource={runs.slice(0, 6)} pagination={false} size="middle"
            locale={{ emptyText: <Empty description="暂无 Run 数据" /> }} />
        </Card>
      </Col>
      <Col xs={24} xl={8}>
        <Card className="panel-card" bordered={false} title={<div className="card-title"><span>运行态快照</span><Text type="secondary">{healthQuery.data?.service || 'Runtime'}</Text></div>}>
          <div className="snapshot-grid">
            <div><span>Benchmark 总体成功率</span><b>{benchmarkQuery.error ? '未接入' : percent(avgBenchmark)}</b></div>
            <div><span>Checkpoint 数量</span><b>{checkpointQuery.error ? '未接入' : checkpoints.length}</b></div>
            <div><span>待复核案件</span><b>{openCases}</b></div>
            <div><span>当前租户</span><b>{isMockMode ? identity.tenantId : (auth.profile?.tenant_id || '-')}</b></div>
          </div>
          <DividerLite />
          <div className="health-line"><span className="health-dot" /><div><b>{isMockMode ? '前端数据模式' : 'Runtime API'}</b><small>{isMockMode ? '浏览器本地 DEMO / MOCK 数据' : 'FastAPI 服务'}</small></div><Tag color={isMockMode ? 'processing' : apiHealthy ? 'success' : apiHealthy === false ? 'error' : 'processing'}>{isMockMode ? 'MOCK' : apiHealthy ? '连接正常' : apiHealthy === false ? '连接异常' : '检查中'}</Tag></div>
          <DividerLite />
          <div className="snapshot-note">
            <LineChartOutlined /> 模型性能与 Benchmark 指标{isMockMode ? '均为合成演示值' : '来自尚未接入的独立评测服务'}，不可作为验收证据。
          </div>
        </Card>
      </Col>
    </Row>
  </>;
}

function DividerLite() {
  return <div className="divider-lite" />;
}

import { useMemo, useState } from 'react';
import {
  Alert, Button, Card, Col, Descriptions, Drawer, Empty, Row, Select, Space, Table, Tabs, Tag, Typography,
} from 'antd';
import type { TableColumnsType } from 'antd';
import { FundOutlined, LineChartOutlined, ReloadOutlined } from '@ant-design/icons';
import { api } from '../api';
import { useAsyncData } from '../hooks/useAsyncData';
import { useApp } from '../state/AppContext';
import { ApiUnavailable, DemoBanner, EChart, formatDuration, formatTime, MetricCard, money, percent, shortId } from '../components/Ui';
import type { AblationRecord, BenchmarkExperiment } from '../types';

const { Text, Paragraph } = Typography;

export function BenchmarkPage() {
  const { refreshToken, requestRefresh, isMockMode, notify } = useApp();
  const [modelFilter, setModelFilter] = useState<string>('ALL');
  const [harnessFilter, setHarnessFilter] = useState<string>('ALL');
  const [batchFilter, setBatchFilter] = useState<string>('ALL');
  const [selected, setSelected] = useState<BenchmarkExperiment | null>(null);
  const [aggregating, setAggregating] = useState(false);
  const benchmarkQuery = useAsyncData(() => api.listBenchmarks(), [refreshToken]);
  const ablationQuery = useAsyncData(() => api.listAblations(), [refreshToken]);

  const aggregate = async () => {
    setAggregating(true);
    try {
      const result = await api.aggregateBenchmark({ batch: 'manual', limit: 500 }) as { created?: number };
      notify('success', `已从真实 Run 聚合生成 ${result.created || 0} 条评测结果`);
      setBatchFilter('manual');
      requestRefresh();
    } catch (error) {
      notify('error', error instanceof Error ? error.message : '聚合失败');
    } finally {
      setAggregating(false);
    }
  };

  const experiments = benchmarkQuery.data || [];
  const models = useMemo(() => Array.from(new Set(experiments.map((item) => item.model))), [experiments]);
  const harnesses = useMemo(() => Array.from(new Set(experiments.map((item) => item.harness))), [experiments]);
  const batches = useMemo(() => Array.from(new Set(experiments.map((item) => item.batch))), [experiments]);

  const filtered = useMemo(() => experiments.filter((item) =>
    (modelFilter === 'ALL' || item.model === modelFilter)
    && (harnessFilter === 'ALL' || item.harness === harnessFilter)
    && (batchFilter === 'ALL' || item.batch === batchFilter),
  ), [experiments, modelFilter, harnessFilter, batchFilter]);

  const avgSuccess = filtered.length ? Math.round(filtered.reduce((sum, item) => sum + item.task_success_rate, 0) / filtered.length) : 0;
  const avgRecovery = filtered.length ? Math.round(filtered.reduce((sum, item) => sum + item.recovery_success_rate, 0) / filtered.length) : 0;
  const avgP95 = filtered.length ? Math.round(filtered.reduce((sum, item) => sum + item.p95_latency_ms, 0) / filtered.length) : 0;
  const avgCostPerSuccess = filtered.length ? filtered.reduce((sum, item) => sum + item.cost_per_success_usd, 0) / filtered.length : 0;
  const avgUnauthorized = filtered.length ? filtered.reduce((sum, item) => sum + item.unauthorized_access_rate, 0) / filtered.length : 0;
  const isRealData = filtered.length > 0 && filtered.every((item) => item.experiment_id.startsWith('agg-'));
  const metricHint = isRealData ? '真实 Run 聚合' : 'DEMO 合成指标';

  const heatmapOption = useMemo(() => ({
    tooltip: { position: 'top', formatter: (params: { data: [number, number, number] }) => `成功率：${params.data[2]}%` },
    grid: { left: 120, right: 70, top: 24, bottom: 60 },
    xAxis: { type: 'category', data: harnesses, splitArea: { show: true }, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'category', data: models, splitArea: { show: true }, axisLabel: { fontSize: 10 } },
    visualMap: { min: 60, max: 100, calculable: true, orient: 'vertical', right: 8, top: 'middle', inRange: { color: ['#f5d6c8', '#f7ead1', '#d6eee2', '#78bfa2'] } },
    series: [{
      name: 'Task Success Rate', type: 'heatmap',
      data: experiments.flatMap((item) => {
        const x = harnesses.indexOf(item.harness);
        const y = models.indexOf(item.model);
        return x >= 0 && y >= 0 ? [[x, y, item.task_success_rate]] : [];
      }),
      label: { show: true, formatter: (params: { data: [number, number, number] }) => `${params.data[2]}%`, fontSize: 10 },
    }],
  }), [experiments, models, harnesses]);

  const makeCompareOption = (dimension: 'model' | 'harness') => {
    const groups = dimension === 'model' ? models : harnesses;
    const metrics = [
      { key: 'task_success_rate', name: '成功率', color: '#2f9b83' },
      { key: 'task_completion_rate', name: '完成率', color: '#5687bb' },
      { key: 'recovery_success_rate', name: '恢复成功率', color: '#d99a32' },
      { key: 'tool_call_accuracy', name: '工具准确率', color: '#8772b7' },
    ];
    return {
      tooltip: { trigger: 'axis' },
      legend: { data: metrics.map((metric) => metric.name), top: 0, textStyle: { fontSize: 10 } },
      grid: { left: 36, right: 16, top: 42, bottom: 30 },
      xAxis: { type: 'category', data: groups, axisLabel: { fontSize: 9, interval: 0 } },
      yAxis: { type: 'value', min: 50, max: 100, axisLabel: { fontSize: 10, formatter: '{value}%' } },
      series: metrics.map((metric) => ({
        name: metric.name, type: 'bar', barMaxWidth: 22, itemStyle: { color: metric.color, borderRadius: [3, 3, 0, 0] },
        data: groups.map((group) => {
          const values = filtered.filter((item) => (dimension === 'model' ? item.model : item.harness) === group);
          const numeric = (item: BenchmarkExperiment) => Number(item[metric.key as keyof BenchmarkExperiment] ?? 0);
          return values.length ? Math.round(values.reduce((sum, item) => sum + numeric(item), 0) / values.length) : 0;
        }),
      })),
    };
  };

  const experimentColumns: TableColumnsType<BenchmarkExperiment> = [
    { title: 'Experiment ID', dataIndex: 'experiment_id', width: 150, render: (value: string) => <Text code>{shortId(value)}</Text> },
    { title: 'Batch', dataIndex: 'batch', width: 170 },
    {
      title: 'Model', key: 'model', width: 180,
      render: (_, record) => <div className="stack-cell"><b>{record.model}</b><span>{record.model_version}</span></div>,
    },
    {
      title: 'Harness', key: 'harness', width: 190,
      render: (_, record) => <div className="stack-cell"><b>{record.harness}</b><span>{record.harness_version}</span></div>,
    },
    { title: 'Prompt', dataIndex: 'prompt_version', width: 130, render: (value: string) => <Text code>{value}</Text> },
    { title: 'Dataset', dataIndex: 'dataset', width: 180 },
    { title: 'Runs', dataIndex: 'runs', width: 70 },
    { title: 'Success', dataIndex: 'task_success_rate', width: 90, render: percent },
    { title: 'Completion', dataIndex: 'task_completion_rate', width: 100, render: percent },
    { title: 'Recovery', dataIndex: 'recovery_success_rate', width: 95, render: percent },
    { title: 'Tool Acc.', dataIndex: 'tool_call_accuracy', width: 95, render: percent },
    { title: 'Unauthorized', dataIndex: 'unauthorized_access_rate', width: 105, render: (value: number) => <span className="long-value">{percent(value)}</span> },
    { title: 'Injection', dataIndex: 'prompt_injection_success_rate', width: 95, render: (value: number) => <span className="long-value">{percent(value)}</span> },
    { title: 'Avg Time', dataIndex: 'avg_duration_seconds', width: 100, render: formatDuration },
    { title: 'P95', dataIndex: 'p95_latency_ms', width: 90, render: (value: number) => `${value} ms` },
    { title: 'Token Cost', dataIndex: 'token_cost_usd', width: 100, render: money },
    { title: 'Cost / Success', dataIndex: 'cost_per_success_usd', width: 120, render: money },
    { title: '状态', dataIndex: 'status', width: 100, render: (value: string) => <Tag color={value === 'COMPLETED' ? 'success' : 'processing'}>{value}</Tag> },
    { title: '', key: 'action', width: 70, render: (_, record) => <Button type="link" onClick={() => setSelected(record)}>详情</Button> },
  ];

  const leaderboardColumns: TableColumnsType<BenchmarkExperiment> = [
    { title: '排名', key: 'rank', width: 70, render: (_, __, index) => <b>#{index + 1}</b> },
    { title: 'Experiment', dataIndex: 'experiment_id', render: (value: string) => <Text code>{shortId(value)}</Text> },
    { title: 'Model', dataIndex: 'model' },
    { title: 'Harness', dataIndex: 'harness' },
    { title: '成功率', dataIndex: 'task_success_rate', render: percent },
    { title: '恢复成功率', dataIndex: 'recovery_success_rate', render: percent },
    { title: 'Cost / Success', dataIndex: 'cost_per_success_usd', render: money },
  ];

  const ablationColumns: TableColumnsType<AblationRecord> = [
    { title: '实验', dataIndex: 'name' },
    { title: '移除组件', dataIndex: 'removed_component' },
    { title: '成功率', dataIndex: 'success_rate', render: percent },
    { title: 'Delta', dataIndex: 'delta_pp', render: (value: number) => <span className={value < 0 ? 'long-value' : ''}>{value > 0 ? '+' : ''}{value} pp</span> },
    { title: '恢复成功率', dataIndex: 'recovery_success_rate', render: percent },
    { title: '未授权访问率', dataIndex: 'unauthorized_access_rate', render: percent },
    { title: '说明', dataIndex: 'note', ellipsis: true },
  ];

  return <>
    {isMockMode && <DemoBanner note="模型、Harness、成功率、恢复率、安全指标、延迟与成本均为 DEMO / MOCK 合成实验数据。" />}
    {benchmarkQuery.error && <ApiUnavailable error={benchmarkQuery.error} feature="Benchmark 实验" />}
    <Card className="panel-card filter-card" bordered={false}>
      <Space wrap className="filter-row">
        <Select value={modelFilter} onChange={setModelFilter} style={{ width: 170 }} options={[{ label: '全部 Model', value: 'ALL' }, ...models.map((value) => ({ label: value, value }))]} />
        <Select value={harnessFilter} onChange={setHarnessFilter} style={{ width: 190 }} options={[{ label: '全部 Harness', value: 'ALL' }, ...harnesses.map((value) => ({ label: value, value }))]} />
        <Select value={batchFilter} onChange={setBatchFilter} style={{ width: 220 }} options={[{ label: '全部批次', value: 'ALL' }, ...batches.map((value) => ({ label: value, value }))]} />
        <Button icon={<ReloadOutlined />} onClick={() => requestRefresh()}>刷新</Button>
        {!isMockMode && <Button type="primary" loading={aggregating} onClick={() => void aggregate()}>从真实 Run 聚合</Button>}
        <Text type="secondary">固定 Harness 比较 Model；固定 Model 比较 Harness。</Text>
      </Space>
    </Card>
    <Row gutter={[16, 16]} className="metrics-row">
      <Col xs={24} sm={12} xl={6}><MetricCard title="实验组合" value={filtered.length} hint="当前筛选下的 Model × Harness 组合" icon={<FundOutlined />} /></Col>
      <Col xs={24} sm={12} xl={6}><MetricCard title="平均任务成功率" value={avgSuccess} suffix="%" hint={metricHint} icon={<LineChartOutlined />} tone="blue" /></Col>
      <Col xs={24} sm={12} xl={6}><MetricCard title="平均恢复成功率" value={avgRecovery} suffix="%" hint={metricHint} icon={<LineChartOutlined />} tone="violet" /></Col>
      <Col xs={24} sm={12} xl={6}><MetricCard title="Cost / Success" value={money(avgCostPerSuccess)} hint="平均每次成功任务成本" icon={<FundOutlined />} tone="amber" /></Col>
    </Row>
    <Card className="panel-card" bordered={false}>
      <Tabs items={[
        {
          key: 'matrix', label: 'Model × Harness 矩阵与对比',
          children: <>
            {isRealData
              ? <Alert className="inline-alert" type="info" showIcon message="数据来自真实 Run 聚合" description={`任务成功率 = 终态 SUCCEEDED 占比；恢复成功率 = worker 故障中 RECOVERED 占比（无故障样本为 0%）；成本 = 总估算成本 ÷ 成功数。样本 ${filtered.reduce((sum, item) => sum + item.runs, 0)} 个 Run。`} />
              : <Alert className="inline-alert" type="warning" showIcon message="演示结果不是模型性能证明" description={`未授权访问率 ${percent(avgUnauthorized)}、注入成功率与恢复成功率均为合成值；P95 ${avgP95} ms。请勿用于模型验收或监管报告。`} />}
            <Card size="small" title="热力矩阵 · Task Success Rate" bordered={false}>
              <EChart option={heatmapOption} height={280} />
            </Card>
            <Row gutter={[16, 16]}>
              <Col xs={24} xl={12}><Card size="small" title="固定 Harness：模型对比" bordered={false}><EChart option={makeCompareOption('model')} height={280} /></Card></Col>
              <Col xs={24} xl={12}><Card size="small" title="固定 Model：Harness 对比" bordered={false}><EChart option={makeCompareOption('harness')} height={280} /></Card></Col>
            </Row>
          </>,
        },
        {
          key: 'experiments', label: '实验明细',
          children: <Table rowKey="experiment_id" columns={experimentColumns} dataSource={filtered} loading={benchmarkQuery.loading} pagination={{ pageSize: 8 }} scroll={{ x: 2_200 }}
            locale={{ emptyText: <Empty description="没有符合条件的实验" /> }} />,
        },
        {
          key: 'leaderboard', label: '排行榜 / 历史',
          children: <>
            <div className="section-label">模型排行榜</div>
            <Table rowKey="experiment_id" columns={leaderboardColumns} dataSource={filtered.slice().sort((a, b) => b.task_success_rate - a.task_success_rate)} pagination={false} size="small" />
            <div className="subtable-gap" />
            <div className="section-label">Benchmark 历史记录</div>
            <Table rowKey="experiment_id" columns={experimentColumns} dataSource={experiments} pagination={{ pageSize: 8 }} scroll={{ x: 2_200 }} size="small" />
          </>,
        },
        {
          key: 'ablation', label: 'Ablation Study',
          children: ablationQuery.error
            ? <ApiUnavailable error={ablationQuery.error} feature="Ablation Study" />
            : <>
              <Paragraph type="secondary">消融实验用于说明移除某个组件后效果变化。所有 Delta 均为 DEMO 合成结果，不代表真实组件贡献。</Paragraph>
              <Table rowKey="ablation_id" columns={ablationColumns} dataSource={ablationQuery.data || []} pagination={false} scroll={{ x: 1_100 }} />
            </>,
        },
      ]} />
    </Card>

    <Drawer width={720} open={Boolean(selected)} onClose={() => setSelected(null)}
      title={<div className="drawer-title"><div><span>实验详情</span><Text code>{selected?.experiment_id}</Text></div>{selected && <Tag color={selected.status === 'COMPLETED' ? 'success' : 'processing'}>{selected.status}</Tag>}</div>}>
      {selected && <div className="run-detail">
        <Paragraph type="secondary">开始时间 {formatTime(selected.started_at)} · 批次 {selected.batch} · 数据集 {selected.dataset}</Paragraph>
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="Model / Version">{selected.model} / {selected.model_version}</Descriptions.Item>
          <Descriptions.Item label="Harness / Version">{selected.harness} / {selected.harness_version}</Descriptions.Item>
          <Descriptions.Item label="Prompt Version"><Text code>{selected.prompt_version}</Text></Descriptions.Item>
          <Descriptions.Item label="运行次数">{selected.runs}</Descriptions.Item>
          <Descriptions.Item label="Task Success Rate">{percent(selected.task_success_rate)}</Descriptions.Item>
          <Descriptions.Item label="Task Completion Rate">{percent(selected.task_completion_rate)}</Descriptions.Item>
          <Descriptions.Item label="Recovery Success Rate">{percent(selected.recovery_success_rate)}</Descriptions.Item>
          <Descriptions.Item label="Tool Call Accuracy">{percent(selected.tool_call_accuracy)}</Descriptions.Item>
          <Descriptions.Item label="Unauthorized Access Rate">{percent(selected.unauthorized_access_rate)}</Descriptions.Item>
          <Descriptions.Item label="Prompt Injection Success Rate">{percent(selected.prompt_injection_success_rate)}</Descriptions.Item>
          <Descriptions.Item label="平均任务执行时间">{formatDuration(selected.avg_duration_seconds)}</Descriptions.Item>
          <Descriptions.Item label="P95 Latency">{selected.p95_latency_ms} ms</Descriptions.Item>
          <Descriptions.Item label="Token Cost">{money(selected.token_cost_usd)}</Descriptions.Item>
          <Descriptions.Item label="Cost Per Successful Task">{money(selected.cost_per_success_usd)}</Descriptions.Item>
        </Descriptions>
        <Alert className="drawer-alert" type="warning" showIcon message="DEMO / MOCK" description="该实验详情为前端合成数据，不包含真实模型推理、真实安全攻击或真实成本记录。" />
      </div>}
    </Drawer>
  </>;
}

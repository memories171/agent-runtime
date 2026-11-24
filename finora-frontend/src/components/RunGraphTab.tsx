import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { Alert, Button, Card, Descriptions, Empty, Segmented, Space, Spin, Table, Tag, Timeline, Typography } from 'antd';
import { api } from '../api';
import { useAsyncData } from '../hooks/useAsyncData';
import { EChart, formatDuration, formatTime } from './Ui';
import type { ContextPack, NodeStatus, TaskGraphDetail, TaskGraphEvent, TaskGraphNodeDetail } from '../types';

const { Text, Paragraph } = Typography;

const statusColors: Record<NodeStatus, string> = {
  PENDING: '#d9d9d9',
  READY: '#167d72',
  RUNNING: '#1677ff',
  WAITING_APPROVAL: '#faad14',
  WAITING_INPUT: '#fa8c16',
  RETRY_WAIT: '#faad14',
  COMPLETED: '#52c41a',
  FAILED: '#ff4d4f',
  CANCELLED: '#8c8c8c',
  SKIPPED: '#bfbfbf',
};

function nodeDepths(graph: TaskGraphDetail): Record<string, number> {
  const depths: Record<string, number> = {};
  const nodes = graph.nodes || [];
  const remaining = new Set(nodes.map((node) => node.node_id));
  let guard = 0;
  while (remaining.size && guard < nodes.length + 1) {
    guard += 1;
    let progressed = false;
    for (const node of nodes) {
      if (!remaining.has(node.node_id)) continue;
      const deps = (node.dependencies || []).filter((dep) => nodes.some((item) => item.node_id === dep));
      if (deps.every((dep) => dep in depths)) {
        depths[node.node_id] = deps.length ? Math.max(...deps.map((dep) => depths[dep])) + 1 : 0;
        remaining.delete(node.node_id);
        progressed = true;
      }
    }
    if (!progressed) break;
  }
  return depths;
}

interface Props {
  runId: string;
  refreshToken?: number;
}

export function RunGraphTab({ runId, refreshToken }: Props) {
  const [view, setView] = useState<'graph' | 'events'>('graph');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [nodeDetail, setNodeDetail] = useState<TaskGraphNodeDetail | null>(null);
  const [contextPack, setContextPack] = useState<ContextPack | null>(null);
  const [contextError, setContextError] = useState<string | null>(null);
  const graphQuery = useAsyncData<TaskGraphDetail | null>(() => api.getRunGraph(runId), [runId, refreshToken]);
  const graph = graphQuery.data || null;
  const eventQuery = useAsyncData<TaskGraphEvent[]>(
    () => (graph ? api.getGraphEvents(graph.graph_id) : Promise.resolve([])),
    [graph?.graph_id, refreshToken],
  );

  useEffect(() => {
    if (!graph || !selectedNodeId) {
      setNodeDetail(null);
      return;
    }
    let active = true;
    api.getGraphNode(graph.graph_id, selectedNodeId).then((detail) => {
      if (active) setNodeDetail(detail);
    }).catch(() => {
      if (active) setNodeDetail(null);
    });
    return () => { active = false; };
  }, [graph?.graph_id, selectedNodeId, refreshToken]);

  const chartOption = useMemo(() => {
    if (!graph) return {};
    const depths = nodeDepths(graph);
    const layers: Record<number, string[]> = {};
    graph.nodes.forEach((node) => {
      const depth = depths[node.node_id] ?? 0;
      (layers[depth] ||= []).push(node.node_id);
    });
    const positions: Record<string, [number, number]> = {};
    Object.entries(layers).forEach(([depthKey, ids]) => {
      const depth = Number(depthKey);
      ids.forEach((id, index) => {
        positions[id] = [80 + depth * 190, 60 + index * 96];
      });
    });
    return {
      tooltip: { trigger: 'item', formatter: (params: { dataType?: string; name?: string; data?: Record<string, unknown> }) => {
        if (params.dataType === 'edge') return `${params.data?.source} → ${params.data?.target}`;
        const data = params.data as { name?: string; status?: NodeStatus; duration_ms?: number; attempt_count?: number } | undefined;
        return `${data?.name || ''}<br/>状态：${data?.status || '-'}<br/>耗时：${formatDuration(data?.duration_ms || 0)}<br/>重试：${data?.attempt_count || 0}`;
      } },
      legend: { data: Object.keys(statusColors), bottom: 0, type: 'scroll' },
      animationDuration: 300,
      series: [
        {
          type: 'graph',
          layout: 'none',
          roam: true,
          focusNodeAdjacency: true,
          data: graph.nodes.map((node) => ({
            id: node.node_id,
            name: node.name || node.node_id,
            x: positions[node.node_id]?.[0] ?? 80,
            y: positions[node.node_id]?.[1] ?? 60,
            symbolSize: node.node_type === 'aggregation' ? 62 : 52,
            status: node.status,
            duration_ms: node.duration_ms,
            attempt_count: node.attempt_count,
            itemStyle: { color: statusColors[node.status] || '#d9d9d9' },
            label: { show: true, position: 'bottom', fontSize: 11, formatter: `${node.name || node.node_id}\n{status|${node.status}}`, rich: { status: { color: '#666', fontSize: 10 } } },
          })),
          links: (graph.edges || []).map((edge) => ({
            source: edge.source_node_id,
            target: edge.target_node_id,
            value: edge.dependency_type,
          })),
          lineStyle: { color: 'source', curveness: 0.08, width: 2 },
          emphasis: { focus: 'adjacency' },
        },
      ],
    };
  }, [graph]);

  const onChartClick = (raw: unknown) => {
    const params = raw as { dataType?: string; data?: { id?: string } };
    if (params.dataType === 'node' && params.data?.id) setSelectedNodeId(params.data.id);
  };

  if (graphQuery.loading) return <Spin tip="加载任务图…" />;
  if (graphQuery.error) return <Alert type="error" showIcon message="任务图加载失败" description={graphQuery.error.message} />;
  if (!graph) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该任务未生成 Task Graph（线性执行模式）" />;

  const retryEvents = (eventQuery.data || []).filter((event) => event.event_type === 'NODE_RETRY' && event.node_id === selectedNodeId);

  return (
    <div className="graph-tab">
      <Space wrap style={{ marginBottom: 8 }}>
        <Segmented
          value={view}
          onChange={(value) => setView(value as 'graph' | 'events')}
          options={[{ label: 'DAG 视图', value: 'graph' }, { label: 'Graph 事件', value: 'events' }]}
        />
        <Tag color="blue">Graph v{graph.graph_version}</Tag>
        <Tag>{graph.graph_status}</Tag>
        {graph.demo && <Tag color="orange">DEMO</Tag>}
        <Text type="secondary">并发上限 {graph.max_concurrency} · 节点 {graph.nodes.length}</Text>
      </Space>

      {view === 'graph' ? <>
        <Card size="small" title="根任务目标" className="graph-goal">
          <Paragraph ellipsis={{ rows: 2 }}>{graph.root_goal}</Paragraph>
          <Space wrap>{graph.acceptance_criteria.map((item) => <Tag key={item}>{item}</Tag>)}</Space>
        </Card>
        <Card size="small" title="任务依赖图（点击节点查看详情）" className="graph-canvas">
          <EChart option={chartOption as Record<string, unknown>} height={330} onClick={onChartClick} />
          <div className="graph-legend">
            {(Object.keys(statusColors) as NodeStatus[]).map((status) => (
              <span key={status}><i style={{ background: statusColors[status] }} />{status}</span>
            ))}
          </div>
        </Card>
        {nodeDetail && <Card size="small" title={`节点详情：${nodeDetail.name}`} className="graph-node-detail" extra={<ButtonLink onClick={() => setSelectedNodeId(null)}>关闭</ButtonLink>}>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="Node ID">{nodeDetail.node_id}</Descriptions.Item>
            <Descriptions.Item label="状态"><Tag color={statusColors[nodeDetail.status]}>{nodeDetail.status}</Tag></Descriptions.Item>
            <Descriptions.Item label="类型">{nodeDetail.node_type}</Descriptions.Item>
            <Descriptions.Item label="耗时">{formatDuration(nodeDetail.duration_ms)}</Descriptions.Item>
            <Descriptions.Item label="重试次数">{nodeDetail.attempt_count}</Descriptions.Item>
            <Descriptions.Item label="Token">{nodeDetail.token_usage?.input || 0} / {nodeDetail.token_usage?.output || 0}</Descriptions.Item>
            <Descriptions.Item label="ContextPack">{nodeDetail.context_pack_id || '-'}</Descriptions.Item>
            <Descriptions.Item label="上游依赖">{(nodeDetail.dependencies || []).join(', ') || '无'}</Descriptions.Item>
          </Descriptions>
          <Paragraph type="secondary">{nodeDetail.description}</Paragraph>
          <Space wrap style={{ marginBottom: 8 }}>
            <Button size="small" onClick={() => {
              setContextError(null);
              api.getRunContext(runId).then((pack) => setContextPack(pack)).catch((error) => {
                setContextPack(null);
                setContextError(error instanceof Error ? error.message : '加载失败');
              });
            }}>查看 ContextPack</Button>
            <Text type="secondary">节点 Pack ID：{nodeDetail.context_pack_id || '-'}</Text>
          </Space>
          {contextError && <Alert type="warning" showIcon message="ContextPack 不可用" description={contextError} />}
          {contextPack && <Card size="small" title={`ContextPack ${contextPack.schema_version}`} className="graph-context">
            <pre className="graph-pre">{JSON.stringify({
              pack_id: contextPack.metadata.context_pack_id,
              node_id: contextPack.metadata.node_id ?? null,
              graph_id: contextPack.metadata.graph_id ?? null,
              parent_node_ids: contextPack.metadata.parent_node_ids ?? [],
              estimated_tokens: contextPack.budget?.estimated_tokens,
              degraded: contextPack.metadata.degraded,
              budget: contextPack.budget,
            }, null, 2)}</pre>
          </Card>}
          {nodeDetail.last_error && <Alert type="error" showIcon message="失败原因" description={nodeDetail.last_error} />}
          <Text strong>输入 / 输出摘要</Text>
          <pre className="graph-pre">{JSON.stringify(nodeDetail.result ?? {}, null, 2)}</pre>
          <Text strong>Artifact References</Text>
          <pre className="graph-pre">{JSON.stringify(nodeDetail.artifact_refs || [], null, 2)}</pre>
          {retryEvents.length > 0 && <>
            <Text strong>重试历史</Text>
            <Table rowKey="event_id" size="small" pagination={false} dataSource={retryEvents} columns={[
              { title: '序号', dataIndex: 'sequence_no', width: 70 },
              { title: '时间', dataIndex: 'created_at', render: (value: string) => formatTime(value) },
              { title: '详情', dataIndex: ['payload', 'error'], render: (_: unknown, record: TaskGraphEvent) => JSON.stringify(record.payload) },
            ]} />
          </>}
          <Text strong>节点事件</Text>
          <Timeline items={(nodeDetail.events || []).map((event) => ({
            color: event.event_type.includes('FAILED') ? 'red' : 'blue',
            children: <div className="event-item"><b>{event.event_type}</b><span>#{event.sequence_no} · {formatTime(event.created_at)}</span><pre>{JSON.stringify(event.payload, null, 2)}</pre></div>,
          }))} />
        </Card>}
      </> : (
        <Card size="small" title="Graph 事件流">
          {eventQuery.error
            ? <Alert type="error" showIcon message="事件加载失败" description={eventQuery.error.message} />
            : (eventQuery.data?.length
              ? <Timeline items={eventQuery.data.map((event) => ({
                color: event.event_type.includes('FAILED') ? 'red' : event.event_type.includes('COMPLETED') ? 'green' : 'blue',
                children: <div className="event-item"><b>#{event.sequence_no} {event.event_type}</b>{event.node_id && <Tag>{event.node_id}</Tag>}<span>{formatTime(event.created_at)}</span><pre>{JSON.stringify(event.payload, null, 2)}</pre></div>,
              }))} />
              : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 Graph 事件" />)}
        </Card>
      )}
    </div>
  );
}

function ButtonLink({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return <a onClick={onClick} style={{ cursor: 'pointer' }}>{children}</a>;
}

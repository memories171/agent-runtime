import { useEffect, useState, type ReactNode } from 'react';
import { Alert, Collapse, Descriptions, Empty, Progress, Segmented, Select, Space, Spin, Tabs, Tag, Tooltip, Typography } from 'antd';
import { ApiUnavailable, DemoBanner, formatTime, shortId } from './Ui';
import { api, isMockMode } from '../api';
import type { ContextItem, ContextPack, ContextPackSummary, ContextSummaryRecord, ContextMetrics } from '../types';

const { Text, Paragraph } = Typography;

const trustColor: Record<string, string> = {
  system: 'red',
  verified: 'green',
  derived: 'blue',
  untrusted: 'orange',
};

function renderValue(value: unknown): ReactNode {
  if (value === null || value === undefined) return <Text type="secondary">—</Text>;
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return <span>{String(value)}</span>;
  return <pre className="context-pre">{JSON.stringify(value, null, 2)}</pre>;
}

function ProvenanceTag({ item }: { item: ContextItem }) {
  const p = item.provenance;
  return (
    <Space size={4} wrap>
      <Tooltip title={`source_id=${p.source_id || '—'}`}><Tag color={trustColor[p.trust_level] || 'default'}>{p.trust_level}</Tag></Tooltip>
      <Tag>{p.source_type}</Tag>
      <Tag color={p.sensitivity === 'restricted' || p.sensitivity === 'confidential' ? 'red' : 'default'}>{p.sensitivity}</Tag>
      {p.source_event_id && <Tooltip title="来源事件"><Text code copyable={{ text: p.source_event_id }}>{shortId(p.source_event_id)}</Text></Tooltip>}
      <Text type="secondary" className="context-rel">rel {p.relevance_score.toFixed(2)}</Text>
    </Space>
  );
}

function ItemList({ items, emptyText }: { items: ContextItem[]; emptyText: string }) {
  if (!items.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyText} />;
  return (
    <div className="context-item-list">
      {items.map((item, idx) => (
        <div className="context-item" key={idx}>
          <div className="context-item-meta"><ProvenanceTag item={item} /><Text type="secondary">{item.estimated_tokens} tok</Text></div>
          <div className="context-item-value">{renderValue(item.value)}</div>
        </div>
      ))}
    </div>
  );
}

interface Props {
  runId: string;
  refreshToken?: number;
}

export function ContextInspector({ runId, refreshToken }: Props) {
  const [view, setView] = useState<'current' | 'history' | 'metrics'>('current');
  const [pack, setPack] = useState<ContextPack | null>(null);
  const [history, setHistory] = useState<ContextPackSummary[]>([]);
  const [summaries, setSummaries] = useState<ContextSummaryRecord[]>([]);
  const [metrics, setMetrics] = useState<ContextMetrics | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (isMockMode) {
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const [hist, summ, met] = await Promise.all([
          api.listRunContextHistory(runId),
          api.listRunContextSummaries(runId),
          api.getRunContextMetrics(runId),
        ]);
        if (!active) return;
        setHistory(hist);
        setSummaries(summ);
        setMetrics(met);
        const target = hist[0]?.context_pack_id;
        if (target) {
          const detail = await api.getRunContext(runId);
          if (active) setPack(detail.schema_version ? detail : null);
        } else {
          setPack(null);
        }
      } catch (err) {
        if (active) setError(err instanceof Error ? err : new Error(String(err)));
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => { active = false; };
  }, [runId, refreshToken, isMockMode]);

  if (isMockMode) {
    return (
      <div>
        <DemoBanner note="Context Inspector 仅在 API 模式展示真实 ContextPack；Mock 模式不会伪造上下文数据。" />
        <Alert type="info" showIcon message="Context 数据不可用" description="切换到 API 模式并运行带 Context Manager 的 Agent Run 后，此处会展示真实的 ContextPack、摘要来源与 Token 预算。" />
      </div>
    );
  }

  if (error) return <ApiUnavailable error={error} feature="Context Inspector" />;
  if (loading && !pack) return <div style={{ textAlign: 'center', padding: 32 }}><Spin tip="加载 Context..." /></div>;

  const budget = pack?.budget;
  const pctUsed = budget && budget.model_context_limit > 0
    ? Math.min(100, Math.round(budget.estimated_tokens / budget.model_context_limit * 100))
    : 0;

  return (
    <div className="context-inspector">
      <Segmented
        value={view}
        onChange={(v) => setView(v as typeof view)}
        options={[
          { label: '当前 Context', value: 'current' },
          { label: `历史版本 (${history.length})`, value: 'history' },
          { label: '压缩与 Token 指标', value: 'metrics' },
        ]}
      />

      {view === 'history' && (
        <div style={{ marginTop: 12 }}>
          <Select
            style={{ width: '100%' }}
            placeholder="历史版本详情暂不可用"
            value={undefined}
            disabled
            options={history.map((h) => ({
              value: h.context_pack_id,
              label: `${formatTime(h.created_at)} · cursor=${h.event_cursor} · ${h.compression_count} 次压缩${h.degraded ? ' · 降级' : ''}`,
            }))}
          />
          <Paragraph type="secondary" style={{ marginTop: 8 }}>
            共 {history.length} 个 ContextPack 版本。当前仅展示版本列表；历史详情需后端提供按 context_pack_id 查询的端点，暂不支持切换。
          </Paragraph>
          <ItemList items={[]} emptyText="暂无 ContextPack 版本记录" />
        </div>
      )}

      {view === 'metrics' && (
        <div style={{ marginTop: 12 }}>
          {metrics ? (
            <>
              <Descriptions bordered size="small" column={2}>
                <Descriptions.Item label="ContextPack 构建次数">{metrics.context_builds}</Descriptions.Item>
                <Descriptions.Item label="携带 Context 的 LLM 调用">{metrics.context_calls}</Descriptions.Item>
                <Descriptions.Item label="压缩次数">{metrics.compressions}</Descriptions.Item>
                <Descriptions.Item label="降级构建">{metrics.degraded_builds}</Descriptions.Item>
                <Descriptions.Item label="Provider 真实 usage">{metrics.provider_reported_calls}</Descriptions.Item>
                <Descriptions.Item label="平均 Token 误差">{metrics.avg_token_delta ?? '—'}</Descriptions.Item>
              </Descriptions>
              <Paragraph type="secondary" style={{ marginTop: 8 }}>
                Token 误差 = Provider 实际 prompt_tokens − 本地估算。Provider 未返回 usage 时不伪造真实值。
              </Paragraph>
            </>
          ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无指标" />}
        </div>
      )}

      {view === 'current' && (
        <>
          {!pack ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无 ContextPack（运行带 Context Manager 的任务后生成）" style={{ marginTop: 24 }} />
          ) : (
            <div style={{ marginTop: 12 }}>
              {pack.metadata.degraded && (
                <Alert type="warning" showIcon className="context-alert" message="上下文已安全降级" description={pack.metadata.degradation_reason || '触发了安全截取策略'} />
              )}
              <Descriptions bordered size="small" column={2} className="context-meta">
                <Descriptions.Item label="Pack ID"><Text code copyable>{pack.metadata.context_pack_id}</Text></Descriptions.Item>
                <Descriptions.Item label="Schema">{pack.schema_version}</Descriptions.Item>
                <Descriptions.Item label="Event Cursor">{pack.metadata.event_cursor}</Descriptions.Item>
                <Descriptions.Item label="压缩次数 / 比例">{pack.metadata.compression_count} / {(pack.metadata.compression_ratio * 100).toFixed(1)}%</Descriptions.Item>
                <Descriptions.Item label="Model">{pack.metadata.model_name || '—'}</Descriptions.Item>
                <Descriptions.Item label="生成时间">{formatTime(pack.metadata.created_at)}</Descriptions.Item>
              </Descriptions>

              {budget && (
                <CardLess>
                  <div className="context-budget-title"><b>Token Allocation</b><Text type="secondary">{budget.tokenizer_name}{budget.tokens_are_estimate ? '（保守估算）' : '（真实 tokenizer）'}</Text></div>
                  <Progress percent={pctUsed} success={{ percent: Math.min(100, Math.round(budget.system_instruction_tokens / Math.max(1, budget.model_context_limit) * 100)) }} />
                  <Space wrap>
                    <Tag>窗口 {budget.model_context_limit.toLocaleString()}</Tag>
                    <Tag>已用 {budget.estimated_tokens.toLocaleString()}</Tag>
                    <Tag color="blue">上下文 {budget.context_tokens.toLocaleString()}</Tag>
                    <Tag color="green">剩余 {budget.available_tokens.toLocaleString()}</Tag>
                    <Tag color="gold">输出预留 {budget.reserved_output_tokens.toLocaleString()}</Tag>
                    <Tag>安全边界 {budget.safety_margin_tokens.toLocaleString()}</Tag>
                  </Space>
                  {budget.provider_prompt_tokens !== null && (
                    <Paragraph type="secondary" style={{ marginTop: 6 }}>
                      Provider 实际 prompt_tokens={budget.provider_prompt_tokens} · 误差={budget.token_delta ?? '—'}
                    </Paragraph>
                  )}
                </CardLess>
              )}

              <Tabs
                items={[
                  {
                    key: 'goal', label: 'Current Goal',
                    children: <Collapse size="small" items={[
                      { key: 'goal', label: '原始任务目标', children: <ItemList items={[pack.goal.original_user_goal]} emptyText="无" /> },
                      { key: 'objectives', label: `任务目标 (${pack.goal.task_objectives.length})`, children: <ItemList items={pack.goal.task_objectives} emptyText="无" /> },
                      { key: 'acceptance', label: `验收标准 (${pack.goal.acceptance_criteria.length})`, children: <ItemList items={pack.goal.acceptance_criteria} emptyText="无" /> },
                      { key: 'constraints', label: `不可变约束 (${pack.goal.immutable_constraints.length})`, children: <ItemList items={pack.goal.immutable_constraints} emptyText="无" /> },
                      { key: 'policy', label: `安全策略引用 (${pack.goal.active_policy_references.length})`, children: <ItemList items={pack.goal.active_policy_references} emptyText="无" /> },
                    ]} />,
                  },
                  {
                    key: 'execution', label: 'Current Context',
                    children: <Collapse size="small" items={[
                      { key: 'plan', label: `执行计划 (${pack.execution.current_plan.length})`, children: <ItemList items={pack.execution.current_plan} emptyText="无" /> },
                      { key: 'step', label: '当前步骤', children: <ItemList items={pack.execution.current_step ? [pack.execution.current_step] : []} emptyText="无" /> },
                      { key: 'done', label: `已完成 (${pack.execution.completed_steps.length})`, children: <ItemList items={pack.execution.completed_steps} emptyText="无" /> },
                      { key: 'artifacts', label: `Artifact 引用 (${pack.execution.active_artifact_references.length})`, children: <ItemList items={pack.execution.active_artifact_references} emptyText="无" /> },
                    ]} />,
                  },
                  {
                    key: 'summary', label: `Rolling Summary (v${pack.historical.summary_version})`,
                    children: <>
                      {pack.historical.summary_id && <Paragraph type="secondary">Summary ID: <Text code copyable>{pack.historical.summary_id}</Text></Paragraph>}
                      <ItemList items={pack.historical.rolling_summary ? [pack.historical.rolling_summary] : []} emptyText="尚无摘要" />
                      <DividerSmall />
                      <Text strong>关键事实与来源事件</Text>
                      <ItemList items={pack.historical.historical_decisions} emptyText="无" />
                      <div className="subtable-gap" />
                      <Text strong>历史摘要版本</Text>
                      <Collapse size="small" items={summaries.map((s) => ({
                        key: s.summary_id,
                        label: `v${s.summary_version} · events ${s.source_event_start}→${s.source_event_end} · ${formatTime(s.created_at)}`,
                        children: <div><Paragraph>{s.summary_content}</Paragraph><Text type="secondary" code>{s.content_hash}</Text></div>,
                      }))} />
                    </>,
                  },
                  {
                    key: 'events', label: `Recent Events (${pack.historical.recent_events.length})`,
                    children: <ItemList items={pack.historical.recent_events} emptyText="无近期事件" />,
                  },
                  {
                    key: 'memory', label: `Retrieved Memory (${pack.memory.retrieved_memory_references.length})`,
                    children: <>
                      <Paragraph type="secondary">可信度均值 {pack.memory.memory_confidence} · 范围 {pack.memory.memory_scope} · 来源 Run {pack.memory.memory_source_run_ids.length} 个</Paragraph>
                      <ItemList items={pack.memory.retrieved_memory_references} emptyText="未检索到 Memory" />
                    </>,
                  },
                  {
                    key: 'tools', label: `Tool Results (${pack.historical.important_tool_results.length})`,
                    children: <ItemList items={pack.historical.important_tool_results} emptyText="无工具结果" />,
                  },
                ]}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}

function CardLess({ children }: { children: ReactNode }) {
  return <div className="context-budget">{children}</div>;
}

function DividerSmall() {
  return <div className="context-divider" />;
}

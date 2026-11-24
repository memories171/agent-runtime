import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { Alert, Card, Tag, Typography } from 'antd';
import * as echarts from 'echarts';
import { ApiNotImplementedError, isMockMode } from '../api';
import type { Decision, MemoryScope, MemoryType, PauseState, RunStatus } from '../types';

const { Text } = Typography;

export function formatTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

export function shortId(value: string) {
  return value.length > 18 ? `${value.slice(0, 10)}…${value.slice(-6)}` : value;
}

export function formatDuration(seconds?: number) {
  if (seconds === undefined || seconds === null) return '—';
  if (seconds < 60) return seconds < 10 ? `${Number(seconds.toFixed(1))}s` : `${Math.round(seconds)}s`;
  if (seconds < 3_600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3_600)}h ${Math.floor((seconds % 3_600) / 60)}m`;
}

export function money(value?: number) {
  return value === undefined || value === null ? '—' : `$${value.toFixed(2)}`;
}

export function percent(value?: number) {
  return value === undefined || value === null ? '—' : `${value.toFixed(1)}%`;
}

const runStatusMeta: Record<RunStatus, { label: string; color: string }> = {
  CREATED: { label: '已创建', color: 'default' }, QUEUED: { label: '排队中', color: 'processing' },
  RUNNING: { label: '运行中', color: 'processing' }, PAUSED: { label: '已暂停', color: 'warning' },
  WAITING_INPUT: { label: '等待输入', color: 'warning' }, WAITING_APPROVAL: { label: '等待审批', color: 'warning' },
  SUCCEEDED: { label: '已完成', color: 'success' }, FAILED: { label: '执行失败', color: 'error' },
  CANCELLED: { label: '已取消', color: 'default' }, TIMED_OUT: { label: '已超时', color: 'error' },
};

const pauseStateLabels: Record<PauseState, { label: string; color: string }> = {
  NONE: { label: '', color: 'default' },
  REQUESTED: { label: '暂停请求已提交', color: 'processing' },
  PAUSING: { label: '正在安全暂停', color: 'processing' },
  PAUSED: { label: '已暂停', color: 'warning' },
  RESUME_REQUESTED: { label: '恢复请求已提交', color: 'processing' },
  RESUMING: { label: '正在恢复', color: 'processing' },
};

export function StatusTag({ status, pauseState }: { status: RunStatus; pauseState?: PauseState }) {
  if (pauseState && pauseState !== 'NONE') {
    const p = pauseStateLabels[pauseState];
    return <Tag color={p.color}>{p.label}</Tag>;
  }
  const meta = runStatusMeta[status] || runStatusMeta.CREATED;
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

export function McpStatusTag({ status }: { status: 'HEALTHY' | 'DEGRADED' | 'UNHEALTHY' }) {
  const color = status === 'HEALTHY' ? 'success' : status === 'DEGRADED' ? 'warning' : 'error';
  const label = status === 'HEALTHY' ? '健康' : status === 'DEGRADED' ? '降级' : '异常';
  return <Tag color={color}>{label}</Tag>;
}

export function MemoryTypeTag({ type }: { type: MemoryType }) {
  const color: Record<MemoryType, string> = { WORKING: 'blue', EPISODIC: 'gold', SEMANTIC: 'purple', PROCEDURAL: 'cyan' };
  return <Tag color={color[type]}>{type}</Tag>;
}

export function ScopeTag({ scope }: { scope: MemoryScope }) {
  const label: Record<MemoryScope, string> = { user: '个人级', team: '团队级', organization: '组织级' };
  const color: Record<MemoryScope, string> = { user: 'blue', team: 'gold', organization: 'green' };
  return <Tag color={color[scope]}>{label[scope]}</Tag>;
}

export function DecisionTag({ decision }: { decision: Decision }) {
  return <Tag color={decision === 'ALLOWED' ? 'success' : 'error'}>{decision === 'ALLOWED' ? '允许' : '拒绝'}</Tag>;
}

export function riskLabel(value: string) {
  const labels: Record<string, string> = {
    low: '低风险', medium: '中风险', high: '高风险', critical: '极高风险', denied: '已拒绝',
  };
  return labels[value] || value;
}

export function releaseLabel(value: string) {
  const labels: Record<string, string> = { STABLE: '稳定版', BETA: '试用版', DRAFT: '草稿版' };
  return labels[value] || value;
}

export function transportLabel(value: string) {
  const labels: Record<string, string> = {
    'Streamable HTTP': '流式 HTTP', 'SSE': '服务器推送事件（SSE）', 'stdio': '标准输入输出',
  };
  return labels[value] || value;
}

export function toolLabel(name: string, displayName?: string) {
  const labels: Record<string, string> = {
    'demo.echo': '文本回显', 'demo.add': '数值求和', 'demo.notify': '外部通知模拟',
    'financial.get_customer_profile': '客户资料查询', 'financial.get_account_summary': '账户摘要查询',
    'financial.list_transactions': '交易明细查询', 'financial.screen_transactions': '交易监测筛查',
    'financial.create_review_case': '复核案件创建', 'mcp.kyc-docs.list': 'KYC 文档列表',
    'mcp.market.search': '市场信息检索', 'mcp.filings.read': '财报片段读取', 'memory.search': '记忆检索',
    'echo': '文本回显', 'add': '数值相加', 'longRunningOperation': '长任务模拟',
    'sampleLLM': 'LLM 采样演示', 'getTinyImage': '小图片获取', 'annotatedMessage': '带注解消息',
    'printEnv': '环境变量打印',
  };
  const shortName = name.includes('.') ? name.slice(name.lastIndexOf('.') + 1) : name;
  const label = labels[name] || labels[shortName];
  if (label) return label;
  if (displayName && !displayName.startsWith('MCP 工具：')) return displayName;
  return displayName || name;
}

export function skillLabel(skillId: string, name?: string, displayName?: string) {
  const labels: Record<string, string> = {
    'transaction-monitoring': '交易监测与异常调查', 'kyc-review': 'KYC 资料复核',
    'financial-research': '金融研究与财报分析', 'financial-reconciliation': '财务对账',
    'credit-risk-review': '信用风险复核',
  };
  return displayName || labels[skillId] || name || skillId;
}

export function MetricCard({ title, value, suffix, hint, icon, tone = 'teal' }: {
  title: string; value: number | string; suffix?: string; hint: string; icon: ReactNode; tone?: string;
}) {
  return <Card className="metric-card" bordered={false}>
    <div className="metric-head"><span>{title}</span><span className={`metric-icon ${tone}`}>{icon}</span></div>
    <div className="metric-value">{value}{suffix && <small>{suffix}</small>}</div>
    <div className="metric-hint">{hint}</div>
  </Card>;
}

export function DemoBanner({ note }: { note: string }) {
  if (!isMockMode) return null;
  return <Alert className="demo-banner" showIcon type="warning" message="DEMO / MOCK：合成演示数据"
    description={`${note} 所有指标、恢复成功率、安全评测与成本均为演示值，不代表真实模型执行或真实业务结果。`} />;
}

export function ApiUnavailable({ error, feature }: { error: Error | null; feature: string }) {
  if (!error) return null;
  const missing = error instanceof ApiNotImplementedError;
  return <Alert className="api-unavailable" showIcon type={missing ? 'warning' : 'error'}
    message={missing ? `${feature}接口未接入` : `${feature}加载失败`}
    description={error.message} />;
}

export function TextHint({ children }: { children: ReactNode }) {
  return <Text type="secondary" className="text-hint">{children}</Text>;
}

export function EChart({ option, height = 280, onClick }: { option: Record<string, unknown>; height?: number; onClick?: (params: unknown) => void }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const clickRef = useRef(onClick);
  clickRef.current = onClick;

  useEffect(() => {
    if (!hostRef.current) return undefined;
    const chart = echarts.init(hostRef.current, undefined, { renderer: 'canvas' });
    chartRef.current = chart;
    chart.on('click', (params) => clickRef.current?.(params));
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(hostRef.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option as echarts.EChartsOption, true);
  }, [option]);

  return <div ref={hostRef} style={{ width: '100%', height }} />;
}

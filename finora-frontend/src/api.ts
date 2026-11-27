import { addMockAudit, commitMockState, getMockState, newId, resetMockState } from './data/mock';
import { ensureFreshToken, renewToken } from './auth/oidc';
import type {
  AblationRecord, AuditLog, BenchmarkExperiment, CheckpointDetail, CheckpointRecord, Identity, LlmCallRecord,
  ManagedSkill, McpServerRecord, McpToolRecord, MemoryRecord, RecoveryStage, ReviewCase,
  ApprovalActionEnvelope, ApprovalRecord, RunEvent, RunRecord, RunTelemetry, SecuritySnapshot, ToolCallRecord, ToolInvocationRecord, WorkerFailure,
  ContextPack, ContextPackSummary, ContextSummaryRecord, ContextMetrics,
  NodeStatus, TaskGraphDetail, TaskGraphEvent, TaskGraphNodeDetail,
  ChatConversation, ChatMessage, KnowledgeDocument, KnowledgeDocumentDetail, KnowledgeCitation, KnowledgeChunkDetail,
} from './types';

export const isMockMode = (import.meta.env.VITE_DATA_MODE || 'mock').toLowerCase() !== 'api';
export const dataMode = isMockMode ? 'mock' : 'api';

const tenantId = import.meta.env.VITE_TENANT_ID || 'demo-bank';
const defaultScopes = [
  'agent:execute', 'runs:read:tenant', 'runs:manage:tenant',
  'financial:customer:read', 'financial:account:read',
  'financial:transactions:read', 'financial:monitoring:run',
  'financial:case:create', 'financial:case:read', 'financial:audit:read',
  'memory:read', 'memory:read:team', 'memory:read:organization',
  'memory:write', 'memory:write:team', 'memory:write:organization', 'memory:write:tenant',
  'approval:approve', 'financial:case:approve',
  'skills:manage', 'security:read', 'platform:manage',
];
const configuredScopes = import.meta.env.VITE_SCOPES || defaultScopes.join(',');

export type DemoRole = 'general_manager' | 'analyst' | 'risk_controller';

const DEMO_SCOPES: Record<DemoRole, string> = {
  general_manager: [
    'platform:manage', 'user:manage', 'security:read', 'audit:read',
    'financial:audit:read', 'runs:read:tenant', 'skills:manage',
    'approval:read', 'knowledge:read', 'knowledge:write',
  ].join(','),
  analyst: [
    'agent:execute', 'run:create', 'run:read', 'run:pause', 'run:resume', 'run:context:read',
    'financial:customer:read', 'financial:account:read', 'financial:transactions:read',
    'financial:monitoring:run', 'financial:case:create', 'financial:case:read',
    'memory:read', 'memory:read:team', 'memory:write', 'memory:write:team',
    'knowledge:read', 'tool:execute',
  ].join(','),
  risk_controller: [
    'run:read', 'runs:read:tenant', 'runs:cancel:tenant',
    'financial:customer:read', 'financial:account:read', 'financial:transactions:read',
    'financial:case:read', 'financial:case:review', 'financial:case:approve',
    'approval:read', 'approval:approve', 'financial:audit:read', 'audit:read',
    'knowledge:read', 'data:sensitive', 'tool:execute',
  ].join(','),
};

const DEMO_IDENTITIES: Record<DemoRole, { userId: string; displayName: string }> = {
  general_manager: { userId: 'manager-01', displayName: '总经理' },
  analyst: { userId: import.meta.env.VITE_USER_ID || 'analyst-01', displayName: '分析人员' },
  risk_controller: { userId: 'risk-01', displayName: '风控复核员' },
};

export const identity: Identity = {
  tenantId,
  userId: isMockMode ? DEMO_IDENTITIES.analyst.userId : (import.meta.env.VITE_USER_ID || 'analyst-01'),
  scopes: isMockMode ? DEMO_SCOPES.analyst : configuredScopes,
  displayName: isMockMode ? DEMO_IDENTITIES.analyst.displayName : '分析人员',
};

export function setDemoIdentity(role: DemoRole) {
  identity.userId = DEMO_IDENTITIES[role].userId;
  identity.displayName = DEMO_IDENTITIES[role].displayName;
  identity.tenantId = tenantId;
  identity.scopes = DEMO_SCOPES[role];
}

export function setDemoTenant(nextTenantId: string) {
  identity.tenantId = nextTenantId || tenantId;
}

export class ApiNotImplementedError extends Error {
  constructor(feature: string) {
    super(`${feature} 尚未接入 API。当前为 API 模式，不会使用 Mock 数据代替真实结果。`);
    this.name = 'ApiNotImplementedError';
  }
}

export class UnauthorizedError extends Error {
  constructor() {
    super('登录状态已失效，请重新登录');
    this.name = 'UnauthorizedError';
  }
}

export class ForbiddenError extends Error {
  constructor(detail: string) {
    super(detail || '没有执行该操作的权限');
    this.name = 'ForbiddenError';
  }
}

type AuthHandlers = { onUnauthorized?: () => void; onForbidden?: (error: Error) => void };
let authHandlers: AuthHandlers = {};
let apiTenantId: string | null = null;

export function setAuthHandlers(handlers: AuthHandlers): void {
  authHandlers = handlers;
}

// API 模式下只有用户显式切换租户后才设置 X-Tenant-ID。
// 登录时租户必须来自已验证 Token，不能使用 .env 或浏览器自报值。
export function setApiTenant(tenantId: string | null): void {
  apiTenantId = tenantId;
}

function requireMock(): void {
  if (!isMockMode) throw new ApiNotImplementedError('该前端交互');
}

function makeToolCalls(runId: string, tools: string[]): ToolCallRecord[] {
  return tools.map((tool, index) => ({
    call_id: `${runId}-call-${index + 1}`, tool,
    server: tool.startsWith('mcp.') ? 'mcp-finance-read' : 'internal-tool-gateway',
    status: 'SUCCESS', started_at: new Date().toISOString(), duration_ms: 320 + index * 220,
    risk_level: tool.includes('review_case') ? 'high' : 'low',
    idempotency_key: newId('idem'),
  }));
}

function makeLlmCalls(): LlmCallRecord[] {
  return [{
    call_id: newId('llm'), model: 'Model-A-DEMO', purpose: '演示规划与摘要',
    prompt_tokens: 8_400, completion_tokens: 1_260, latency_ms: 2_180,
    started_at: new Date().toISOString(), status: 'SUCCESS',
  }];
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const doFetch = async (token: string | null) => {
    const headers = new Headers(init.headers);
    if (!isMockMode) {
      if (token) headers.set('Authorization', `Bearer ${token}`);
      // 仅作为当前租户选择参数；未显式切换时让后端从 JWT claim 取租户。
      if (apiTenantId) headers.set('X-Tenant-ID', apiTenantId);
    } else {
      headers.set('X-Tenant-ID', identity.tenantId);
      headers.set('X-User-ID', identity.userId);
      headers.set('X-Scopes', identity.scopes);
    }
    if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    // `/api` 是前端统一代理挂载点。已自带 /api/v1 的路由保留完整路径，
    // 其余业务路由由代理去掉 /api 前缀后转发到后端根路径。
    const target = path.startsWith('/api/') ? path : `/api${path}`;
    return fetch(target, { ...init, headers });
  };

  let response = await doFetch(isMockMode ? null : await ensureFreshToken());
  // Token 在页面停留期间过期时静默续期并重试一次，避免把用户踢回登录页。
  if (response.status === 401 && !isMockMode) {
    const refreshed = await renewToken();
    if (refreshed) response = await doFetch(refreshed);
  }
  const raw = await response.text();
  let payload: unknown = undefined;
  try { payload = raw ? JSON.parse(raw) : undefined; } catch { payload = raw; }
  if (response.status === 401) {
    authHandlers.onUnauthorized?.();
    throw new UnauthorizedError();
  }
  if (response.status === 403) {
    const detail = (payload as { detail?: unknown } | undefined)?.detail;
    const error = new ForbiddenError(typeof detail === 'string' ? detail : '');
    authHandlers.onForbidden?.(error);
    throw error;
  }
  if (!response.ok) {
    const detail = (payload as { detail?: unknown } | undefined)?.detail;
    throw new Error(typeof detail === 'string' ? detail : `请求失败 (${response.status})`);
  }
  return payload as T;
}

function sortByTime<T extends { created_at?: string | null; updated_at?: string | null }>(items: T[]): T[] {
  return items.slice().sort((a, b) => (b.updated_at || b.created_at || '').localeCompare(a.updated_at || a.created_at || ''));
}

function makeDemoGraph(run: RunRecord): TaskGraphDetail {
  const statuses: Record<string, NodeStatus> = {
    collect_inputs: 'COMPLETED',
    analyze_facts: run.status === 'FAILED' ? 'FAILED' : run.status === 'RUNNING' ? 'RUNNING' : 'COMPLETED',
    final_report: run.status === 'SUCCEEDED' ? 'COMPLETED' : run.status === 'FAILED' ? 'SKIPPED' : 'PENDING',
  };
  const nodes = [
    { node_id: 'collect_inputs', node_type: 'task', name: '收集输入', description: '收集任务相关的最小必要信息。', dependencies: [], status: statuses.collect_inputs, execution_order: 0, attempt_count: 1, last_error: null, result: { summary: 'DEMO：已获取最小化输入集合。' }, token_usage: { input: 1820, output: 240 }, duration_ms: 1240, artifact_refs: [], context_pack_id: 'ctx-demo-001', source_event_ids: ['evt-demo-001'] },
    { node_id: 'analyze_facts', node_type: 'analysis', name: '分析事实', description: '基于输入分析事实与不确定性。', dependencies: [], status: statuses.analyze_facts, execution_order: 1, attempt_count: 2, last_error: statuses.analyze_facts === 'FAILED' ? 'DEMO：上游数据超时' : null, result: statuses.analyze_facts === 'FAILED' ? null : { summary: 'DEMO：已形成事实清单，结论仍需人工确认。' }, token_usage: { input: 2640, output: 380 }, duration_ms: 2180, artifact_refs: [], context_pack_id: 'ctx-demo-002', source_event_ids: ['evt-demo-002'] },
    { node_id: 'final_report', node_type: 'aggregation', name: '汇总报告', description: '汇聚上游结果，明确标注数据缺口。', dependencies: ['collect_inputs', 'analyze_facts'], status: statuses.final_report, execution_order: 2, attempt_count: 1, last_error: null, result: statuses.final_report === 'COMPLETED' ? { summary: 'DEMO：任务图汇总完成，无数据缺口。' } : null, token_usage: { input: 980, output: 160 }, duration_ms: 860, artifact_refs: [], context_pack_id: 'ctx-demo-003', source_event_ids: [] },
  ];
  return {
    graph_id: `graph-demo-${run.run_id}`,
    run_id: run.run_id,
    tenant_id: run.tenant_id,
    graph_version: 1,
    graph_status: run.status === 'SUCCEEDED' ? 'SUCCEEDED' : run.status === 'FAILED' ? 'FAILED' : 'RUNNING',
    root_goal: run.prompt,
    acceptance_criteria: ['覆盖任务目标', '明确标注数据缺口'],
    max_concurrency: 5,
    failure_policy: 'CONTINUE_INDEPENDENT',
    nodes,
    edges: [
      { edge_id: 'e-1', source_node_id: 'collect_inputs', target_node_id: 'final_report', dependency_type: 'SUCCESS' },
      { edge_id: 'e-2', source_node_id: 'analyze_facts', target_node_id: 'final_report', dependency_type: 'SUCCESS' },
    ],
    demo: true,
  };
}

function mockChunkDocument(content: string, chunkSize = 800): KnowledgeChunkDetail[] {
  const size = Math.max(120, Math.min(4000, chunkSize || 800));
  const chunks: KnowledgeChunkDetail[] = [];
  for (let i = 0, index = 0; i < content.length; i += size, index += 1) {
    const part = content.slice(i, i + size);
    chunks.push({
      chunk_id: `chunk-${index}-${newId('demo')}`,
      chunk_index: index,
      heading: `第 ${index + 1} 段`,
      content: part,
      char_count: part.length,
    });
  }
  return chunks;
}

export const api = {
  authMe: async (): Promise<Record<string, unknown>> => request('/api/v1/auth/me'),
  authTenants: async (): Promise<unknown[]> => request('/api/v1/auth/tenants'),
  switchTenant: async (body: { tenant_id: string }): Promise<Record<string, unknown>> => request('/api/v1/auth/switch-tenant', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  authPermissions: async (): Promise<Record<string, unknown>> => request('/api/v1/auth/permissions'),
  health: async (): Promise<{ status: string; service: string }> => isMockMode
    ? { status: 'mock', service: 'Finora DEMO / MOCK data adapter' }
    : request('/health'),

  meta: async (): Promise<Record<string, unknown>> => isMockMode
    ? { mode: 'mock', tenant_id: identity.tenantId, source: 'DEMO' }
    : request('/meta'),

  listRuns: async (limit = 100): Promise<RunRecord[]> => {
    if (!isMockMode) return request<RunRecord[]>(`/runs?limit=${limit}`);
    const state = getMockState();
    return sortByTime(state.runs.filter((run) => run.tenant_id === identity.tenantId)).slice(0, limit);
  },

  getRun: async (runId: string): Promise<RunRecord> => {
    if (!isMockMode) return request<RunRecord>(`/runs/${runId}`);
    const run = getMockState().runs.find((item) => item.run_id === runId && item.tenant_id === identity.tenantId);
    if (!run) throw new Error('没有找到该任务，或当前租户无权访问');
    return run;
  },

  getEvents: async (runId: string): Promise<RunEvent[]> => {
    if (!isMockMode) return request<RunEvent[]>(`/runs/${runId}/events`);
    return getMockState().eventsByRun[runId] || [];
  },

  createRun: async (body: { prompt: string; skill_ids: string[]; agent_id?: string; graph?: { enabled: boolean } }): Promise<RunRecord> => {
    if (!isMockMode) return request<RunRecord>('/runs', { method: 'POST', body: JSON.stringify(body) });
    const state = getMockState();
    const now = new Date().toISOString();
    const runId = newId('run');
    const asksApproval = /(导出|发送|转账|付款|退款|删除|修改权限)/.test(body.prompt);
    const isMonitoring = /(financial\.screen_transactions|交易监测|交易筛查|监测)/.test(body.prompt);
    const tools = isMonitoring
      ? ['financial.list_transactions', 'financial.screen_transactions']
      : ['financial.get_customer_profile'];
    const run: RunRecord = {
      run_id: runId, tenant_id: identity.tenantId, user_id: identity.userId,
      session_id: newId('session'), agent_id: body.agent_id || (isMonitoring ? 'transaction-monitoring-agent' : 'financial-assistant'),
      prompt: body.prompt,
      status: asksApproval ? 'WAITING_APPROVAL' : 'RUNNING',
      result: asksApproval
        ? { mode: 'DEMO', approval_required: true, reason: '演示规则将该动作标记为需要独立审批。', skill_ids: body.skill_ids, graph_mode: Boolean(body.graph?.enabled) }
        : { mode: 'DEMO', summary: body.graph?.enabled ? 'DEMO：Task Graph 模式已启用，DAG 视图见详情 Tab。' : '演示任务由本地 Mock Adapter 模拟，不代表真实模型推理。', skill_ids: body.skill_ids, graph_mode: Boolean(body.graph?.enabled) },
      created_at: now, updated_at: now,
      model_name: 'Model-A-DEMO', harness_version: 'Harness-Alpha-DEMO',
      duration_seconds: 0, heartbeat_at: now, deadline_at: new Date(Date.now() + 8 * 3_600_000).toISOString(),
      progress_pct: asksApproval ? 72 : 18,
      current_step: asksApproval ? '等待独立审批' : '演示任务已排队',
      token_input: 0, token_output: 0, estimated_cost_usd: 0, demo: true,
    };
    const telemetry: RunTelemetry = {
      run_id: runId, model: run.model_name || 'Model-A-DEMO', harness_version: run.harness_version || 'Harness-Alpha-DEMO',
      prompt_version: 'PROMPT-DEMO-v4.2', duration_seconds: 0, heartbeat_at: now,
      deadline_at: run.deadline_at || null, current_step: run.current_step || '初始化', progress_pct: run.progress_pct || 0,
      token_input: 0, token_output: 0, estimated_cost_usd: 0,
      tool_calls: makeToolCalls(runId, tools), llm_calls: makeLlmCalls(),
      checkpoint_ids: [], recovery_count: 0, demo: true,
    };
    state.runs.unshift(run);
    state.telemetryByRun[runId] = telemetry;
    state.eventsByRun[runId] = [
      { event_id: newId('evt'), event_type: 'RUN_CREATED', payload: { mode: 'DEMO', skill_ids: body.skill_ids }, created_at: now },
      { event_id: newId('evt'), event_type: asksApproval ? 'WAITING_APPROVAL' : 'PLAN_CREATED', payload: { mode: 'DEMO' }, created_at: now },
    ];
    addMockAudit('RUN_CREATED', 'AGENT_RUN', runId, identity.userId, runId);
    commitMockState();
    return run;
  },

  runAction: async (runId: string, action: 'pause' | 'resume' | 'cancel') => {
    if (!isMockMode) return request(`/runs/${runId}/${action}`, { method: 'POST', body: '{}' });
    const state = getMockState();
    const run = state.runs.find((item) => item.run_id === runId && item.tenant_id === identity.tenantId);
    if (!run) throw new Error('没有找到该任务');
    if (action === 'pause') {
      if (run.status !== 'RUNNING') throw new Error('只有运行中的任务可以暂停');
      run.status = 'PAUSED';
    } else if (action === 'resume') {
      if (run.status !== 'PAUSED') throw new Error('只有已暂停的任务可以恢复');
      run.status = 'RUNNING';
    } else {
      if (!['RUNNING', 'PAUSED', 'WAITING_INPUT', 'WAITING_APPROVAL'].includes(run.status)) throw new Error('当前任务状态不允许取消');
      run.status = 'CANCELLED';
    }
    run.updated_at = new Date().toISOString();
    const telemetry = state.telemetryByRun[runId];
    if (telemetry) telemetry.current_step = `${action.toUpperCase()} · DEMO 操作`;
    state.eventsByRun[runId] ||= [];
    state.eventsByRun[runId].push({
      event_id: newId('evt'), event_type: `RUN_${action.toUpperCase()}`,
      payload: { actor: identity.userId, mode: 'DEMO' }, created_at: run.updated_at,
    });
    addMockAudit(`RUN_${action.toUpperCase()}`, 'AGENT_RUN', runId, identity.userId, runId);
    commitMockState();
    return { status: run.status };
  },

  deleteRun: async (runId: string): Promise<{ run_id: string; deleted: boolean }> => {
    if (!isMockMode) return request(`/runs/${runId}`, { method: 'DELETE' });
    const state = getMockState();
    const index = state.runs.findIndex((item) => item.run_id === runId && item.tenant_id === identity.tenantId);
    if (index < 0) throw new Error('没有找到该任务');
    const run = state.runs[index];
    if (['RUNNING', 'PAUSED', 'WAITING_INPUT', 'WAITING_APPROVAL', 'QUEUED', 'CREATED'].includes(run.status)) {
      throw new Error('请先取消任务再删除');
    }
    state.runs.splice(index, 1);
    delete state.eventsByRun[runId];
    delete state.telemetryByRun[runId];
    state.checkpoints = state.checkpoints.filter((item) => item.run_id !== runId);
    state.failures = state.failures.filter((item) => item.run_id !== runId);
    addMockAudit('RUN_DELETED', 'AGENT_RUN', runId, identity.userId, runId);
    commitMockState();
    return { run_id: runId, deleted: true };
  },

  approveRun: async (runId: string, approved: boolean, reason: string) => {
    if (!isMockMode) return request(`/runs/${runId}/approval`, { method: 'POST', body: JSON.stringify({ approved, reason }) });
    const state = getMockState();
    const run = state.runs.find((item) => item.run_id === runId && item.tenant_id === identity.tenantId);
    if (!run) throw new Error('没有找到该任务');
    if (run.status !== 'WAITING_APPROVAL') throw new Error('该任务当前不等待审批');
    if (run.user_id === identity.userId) throw new Error('任务发起人不能审批自己的请求');
    run.status = approved ? 'SUCCEEDED' : 'CANCELLED';
    run.updated_at = new Date().toISOString();
    run.result = { ...(run.result || {}), approval: approved ? 'APPROVED' : 'REJECTED', approval_reason: reason, approved_by: identity.userId, mode: 'DEMO' };
    state.eventsByRun[runId] ||= [];
    state.eventsByRun[runId].push({
      event_id: newId('evt'), event_type: approved ? 'APPROVAL_GRANTED' : 'APPROVAL_REJECTED',
      payload: { approved_by: identity.userId, reason, mode: 'DEMO' }, created_at: run.updated_at,
    });
    addMockAudit(approved ? 'APPROVAL_GRANTED' : 'APPROVAL_REJECTED', 'AGENT_RUN', runId, identity.userId, runId);
    commitMockState();
    return { status: run.status };
  },

  listCases: async (): Promise<ReviewCase[]> => {
    if (!isMockMode) return request<ReviewCase[]>('/finance/review-cases?limit=100');
    return sortByTime(getMockState().cases);
  },

  reviewCase: async (caseId: string, action: string, reason: string): Promise<ReviewCase> => {
    if (!isMockMode) {
      return request<ReviewCase>(`/finance/review-cases/${encodeURIComponent(caseId)}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action, reason }),
      });
    }
    const item = getMockState().cases.find((row) => row.case_id === caseId);
    if (!item) throw new Error('案件不存在');
    const statusMap: Record<string, string> = {
      START_REVIEW: 'IN_REVIEW', APPROVE: 'APPROVED', REJECT: 'REJECTED',
      ESCALATE: 'ESCALATED', ARCHIVE: 'ARCHIVED',
    };
    const now = new Date().toISOString();
    item.status = statusMap[action] || item.status;
    item.reviewer = identity.userId;
    item.review_decision = action;
    item.review_reason = reason;
    item.reviewed_at = now;
    item.review_history = [...(item.review_history || []), {
      actor: identity.userId, action, reason, status: item.status, at: now,
    }];
    addMockAudit('REVIEW_CASE_ACTION', 'REVIEW_CASE', caseId, identity.userId);
    commitMockState();
    return item;
  },

  listAudit: async (): Promise<AuditLog[]> => {
    if (!isMockMode) return request<AuditLog[]>('/finance/audit-logs?limit=100');
    return sortByTime(getMockState().auditLogs);
  },

  listSkills: async (): Promise<Array<{ skill_id: string; name?: string; display_name?: string; description: string; path?: string }>> => {
    if (!isMockMode) return request<Array<{ skill_id: string; name?: string; display_name?: string; description: string; path?: string }>>('/skills');
    return getMockState().managedSkills.map((skill) => ({
      skill_id: skill.skill_id, name: skill.name, display_name: skill.name,
      description: skill.description, path: `技能目录 / ${skill.name}`,
    }));
  },

  listTools: async (): Promise<Array<{ name: string; display_name?: string; description: string; required_scopes: string[]; risk_level: string; idempotent: boolean }>> => {
    if (!isMockMode) return request<Array<{ name: string; display_name?: string; description: string; required_scopes: string[]; risk_level: string; idempotent: boolean }>>('/tools');
    return getMockState().mcpTools.map(({ name, description, required_scopes, risk_level, idempotent }) => ({
      name, display_name: name, description, required_scopes, risk_level, idempotent,
    }));
  },

  listRunTelemetries: async (): Promise<RunTelemetry[]> => {
    if (!isMockMode) return request<RunTelemetry[]>('/runs/telemetry?limit=100');
    const state = getMockState();
    return Object.values(state.telemetryByRun).filter((item) => state.runs.some((run) => run.run_id === item.run_id && run.tenant_id === identity.tenantId));
  },

  getRunTelemetry: async (runId: string): Promise<RunTelemetry | null> => {
    if (!isMockMode) return request<RunTelemetry>(`/runs/${runId}/telemetry`);
    return getMockState().telemetryByRun[runId] || null;
  },

  listMcpServers: async (): Promise<McpServerRecord[]> => {
    if (!isMockMode) return request<McpServerRecord[]>('/mcp/servers');
    return getMockState().mcpServers;
  },

  listMcpTools: async (): Promise<McpToolRecord[]> => {
    if (!isMockMode) return request<McpToolRecord[]>('/mcp/tools');
    return getMockState().mcpTools;
  },

  listManagedSkills: async (): Promise<ManagedSkill[]> => {
    if (!isMockMode) return request<ManagedSkill[]>('/skills/managed');
    return getMockState().managedSkills;
  },

  updateSkillEnabled: async (skillId: string, enabled: boolean): Promise<ManagedSkill> => {
    if (!isMockMode) return request<ManagedSkill>(`/skills/${skillId}/enabled`, { method: 'PATCH', body: JSON.stringify({ enabled }) });
    const state = getMockState();
    const skill = state.managedSkills.find((item) => item.skill_id === skillId);
    if (!skill) throw new Error('没有找到该 Skill');
    skill.enabled = enabled;
    skill.updated_at = new Date().toISOString();
    commitMockState();
    return skill;
  },

  listMemories: async (): Promise<MemoryRecord[]> => {
    if (!isMockMode) return request<MemoryRecord[]>('/memory?limit=200');
    return sortByTime(getMockState().memories.filter((item) => item.tenant_id === identity.tenantId));
  },

  createMemory: async (body: Pick<MemoryRecord, 'title' | 'type' | 'scope' | 'content' | 'confidence' | 'source_run_id' | 'expires_at' | 'acl'>): Promise<MemoryRecord> => {
    if (!isMockMode) return request<MemoryRecord>('/memory/full', {
      method: 'POST',
      body: JSON.stringify({
        title: body.title, content: body.content, scope: body.scope,
        memory_type: body.type.toLowerCase(), confidence: body.confidence,
        source_run_id: body.source_run_id || null, expires_at: body.expires_at || null, acl: body.acl,
      }),
    });
    const now = new Date().toISOString();
    const record: MemoryRecord = {
      ...body, memory_id: newId('mem'), owner: identity.userId, tenant_id: identity.tenantId,
      source_run_id: body.source_run_id || null, expires_at: body.expires_at || null,
      acl: body.acl.length ? body.acl : [`user:${identity.userId}`],
      created_at: now, updated_at: now, archived: false, demo: true,
    };
    const state = getMockState();
    state.memories.unshift(record);
    addMockAudit('MEMORY_CREATED', 'MEMORY', record.memory_id, identity.userId);
    commitMockState();
    return record;
  },

  updateMemory: async (memoryId: string, body: Pick<MemoryRecord, 'title' | 'type' | 'scope' | 'content' | 'confidence' | 'expires_at' | 'acl'>): Promise<MemoryRecord> => {
    if (!isMockMode) return request<MemoryRecord>(`/memory/${memoryId}`, {
      method: 'PATCH',
      body: JSON.stringify({
        title: body.title, content: body.content, scope: body.scope,
        memory_type: body.type.toLowerCase(), confidence: body.confidence,
        expires_at: body.expires_at || null, acl: body.acl,
      }),
    });
    const state = getMockState();
    const record = state.memories.find((item) => item.memory_id === memoryId);
    if (!record) throw new Error('没有找到该记忆');
    Object.assign(record, body, { updated_at: new Date().toISOString(), expires_at: body.expires_at || null });
    addMockAudit('MEMORY_UPDATED', 'MEMORY', memoryId, identity.userId);
    commitMockState();
    return record;
  },

  archiveMemory: async (memoryId: string): Promise<void> => {
    if (!isMockMode) { await request(`/memory/${memoryId}/archive`, { method: 'POST' }); return; }
    const state = getMockState();
    const record = state.memories.find((item) => item.memory_id === memoryId);
    if (!record) throw new Error('没有找到该记忆');
    record.archived = true;
    record.updated_at = new Date().toISOString();
    addMockAudit('MEMORY_ARCHIVED', 'MEMORY', memoryId, identity.userId);
    commitMockState();
  },

  deleteMemory: async (memoryId: string): Promise<void> => {
    if (!isMockMode) { await request(`/memory/${memoryId}`, { method: 'DELETE' }); return; }
    const state = getMockState();
    state.memories = state.memories.filter((item) => item.memory_id !== memoryId);
    addMockAudit('MEMORY_DELETED', 'MEMORY', memoryId, identity.userId);
    commitMockState();
  },

  listCheckpoints: async (): Promise<CheckpointRecord[]> => {
    if (!isMockMode) return request<CheckpointRecord[]>('/checkpoints');
    return getMockState().checkpoints.filter((item) => item.tenant_id === identity.tenantId).slice().sort((a, b) => b.created_at.localeCompare(a.created_at));
  },

  getCheckpoint: async (checkpointId: string): Promise<CheckpointDetail> => {
    if (!isMockMode) return request<CheckpointDetail>(`/checkpoints/${checkpointId}`);
    throw new ApiNotImplementedError('Checkpoint 快照详情（Mock 模式不提供完整 Agent State）');
  },

  listWorkerFailures: async (): Promise<WorkerFailure[]> => {
    if (!isMockMode) return request<WorkerFailure[]>('/worker-failures');
    return getMockState().failures.filter((item) => item.tenant_id === identity.tenantId).slice().sort((a, b) => b.occurred_at.localeCompare(a.occurred_at));
  },

  getRecoveryStages: async (runId: string): Promise<RecoveryStage[]> => {
    if (!isMockMode) return request<RecoveryStage[]>(`/runs/${runId}/recovery-timeline`);
    const state = getMockState();
    const failure = state.failures.filter((item) => item.run_id === runId).sort((a, b) => b.occurred_at.localeCompare(a.occurred_at))[0];
    const checkpoints = state.checkpoints.filter((item) => item.run_id === runId);
    const run = state.runs.find((item) => item.run_id === runId);
    const stages: RecoveryStage[] = [
      { key: 'running', label: 'RUNNING', time: run?.created_at || '—', status: 'finish', description: '任务开始执行，工作流持续心跳。' },
      { key: 'checkpoint', label: 'CHECKPOINT_SAVED', time: checkpoints[0]?.created_at || '未保存', status: checkpoints.length ? 'finish' : 'wait', description: checkpoints[0]?.summary || '当前 Run 没有已保存检查点。' },
      { key: 'failed', label: 'WORKER_FAILED', time: failure?.occurred_at || '未发生', status: failure ? 'error' : 'wait', description: failure?.reason || '未记录 Worker 故障。' },
      { key: 'recovering', label: 'RECOVERING', time: failure?.occurred_at || '未发生', status: failure && failure.recovery_status !== 'FAILED' ? 'process' : 'wait', description: `恢复次数 ${failure?.recovery_count || 0}，重试 ${failure?.retry_count || 0}。` },
      { key: 'resumed', label: 'RESUMED', time: failure?.recovered_from_checkpoint_id ? failure.occurred_at : '未恢复', status: failure?.recovered_from_checkpoint_id ? 'finish' : 'wait', description: `恢复位置：${failure?.recovery_position || '—'}` },
      { key: 'succeeded', label: 'SUCCEEDED', time: run?.status === 'SUCCEEDED' ? run.updated_at : '未完成', status: run?.status === 'SUCCEEDED' ? 'finish' : 'wait', description: run?.status === 'SUCCEEDED' ? '任务已成功完成。' : '任务尚未到达成功终态。' },
    ];
    return stages;
  },

  listToolInvocations: async (runId: string): Promise<ToolInvocationRecord[]> => {
    if (!isMockMode) return request<ToolInvocationRecord[]>(`/runs/${runId}/tool-invocations`);
    const calls = (await api.listRunTelemetries()).filter((item) => item.run_id === runId).flatMap((item) => item.tool_calls || []);
    return calls.map((call) => ({
      invocation_id: call.call_id || call.idempotency_key || '',
      run_id: runId,
      tenant_id: identity.tenantId,
      tool_call_id: call.call_id || null,
      tool_name: call.tool,
      idempotency_key: call.idempotency_key || '',
      canonical_arguments_hash: '',
      status: call.status === 'SUCCESS' ? 'CONFIRMED' : 'FAILED',
      attempt_count: 1,
      external_reference: null,
      result_reference: null,
      reconciliation_status: 'NONE',
      reconciliation_note: null,
      fencing_token: 0,
      lease_owner: null,
      lease_expires_at: null,
      started_at: call.started_at || null,
      completed_at: call.started_at || null,
      last_error: null,
      created_at: call.started_at || '',
      demo: true,
    }));
  },

  reconcileRun: async (runId: string, idempotencyKey: string, resolvedStatus: 'CONFIRMED' | 'FAILED', note: string) => {
    if (!isMockMode) {
      return request(`/runs/${runId}/reconcile`, {
        method: 'POST',
        body: JSON.stringify({ idempotency_key: idempotencyKey, resolved_status: resolvedStatus, resolved_result: null, note }),
      });
    }
    throw new ApiNotImplementedError('人工对账（Mock 模式不支持）');
  },

  pauseRun: async (runId: string) => {
    if (!isMockMode) return request(`/runs/${runId}/pause`, { method: 'POST' });
    const state = getMockState();
    const run = state.runs.find((item) => item.run_id === runId && item.tenant_id === identity.tenantId);
    if (!run) throw new Error('没有找到该任务');
    if (run.status !== 'RUNNING') throw new Error('只有运行中的任务可以暂停');
    run.status = 'PAUSED';
    run.updated_at = new Date().toISOString();
    addMockAudit('RUN_PAUSED', 'AGENT_RUN', runId, identity.userId, runId);
    commitMockState();
    return { run_id: runId, pause_state: 'PAUSED', accepted: true, demo: true };
  },

  resumeRun: async (runId: string) => {
    if (!isMockMode) return request(`/runs/${runId}/resume`, { method: 'POST' });
    const state = getMockState();
    const run = state.runs.find((item) => item.run_id === runId && item.tenant_id === identity.tenantId);
    if (!run) throw new Error('没有找到该任务');
    if (run.status !== 'PAUSED') throw new Error('只有已暂停的任务可以恢复');
    run.status = 'RUNNING';
    run.updated_at = new Date().toISOString();
    addMockAudit('RUN_RESUMED', 'AGENT_RUN', runId, identity.userId, runId);
    commitMockState();
    return { run_id: runId, pause_state: 'NONE', accepted: true, demo: true };
  },

  provideRunInput: async (runId: string, value: string) => {
    if (!isMockMode) return request(`/runs/${runId}/input`, {
      method: 'POST',
      body: JSON.stringify({ value }),
    });
    const state = getMockState();
    const run = state.runs.find((item) => item.run_id === runId && item.tenant_id === identity.tenantId);
    if (!run) throw new Error('没有找到该任务');
    if (run.status !== 'WAITING_INPUT') throw new Error('该任务当前不等待输入');
    const now = new Date().toISOString();
    run.status = 'RUNNING';
    run.updated_at = now;
    run.result = { ...(run.result || {}), user_input: value };
    state.eventsByRun[runId] = [
      ...(state.eventsByRun[runId] || []),
      { event_id: newId('evt'), event_type: 'USER_INPUT_RECEIVED', payload: { value, mode: 'DEMO' }, created_at: now },
    ];
    addMockAudit('USER_INPUT_PROVIDED', 'AGENT_RUN', runId, identity.userId, runId);
    commitMockState();
    return { run_id: runId, status: run.status, accepted: true, demo: true };
  },

  listApprovals: async (): Promise<ApprovalRecord[]> => {
    if (!isMockMode) return request<ApprovalRecord[]>('/approvals');
    return (getMockState().security.approvals || []).map((item) => ({
      ...item,
      approval_status: item.approval_status ?? item.decision,
      requested_by: item.requested_by ?? item.requester,
      approver_id: item.approver_id ?? item.approver ?? null,
      demo: true,
    }));
  },

  listRunApprovals: async (runId: string): Promise<ApprovalRecord[]> => {
    if (!isMockMode) return request<ApprovalRecord[]>(`/runs/${runId}/approvals`);
    return (getMockState().security.approvals || [])
      .filter((item) => item.run_id === runId)
      .map((item) => ({
        ...item,
        approval_status: item.approval_status ?? item.decision,
        requested_by: item.requested_by ?? item.requester,
        approver_id: item.approver_id ?? item.approver ?? null,
        demo: true,
      }));
  },

  getApprovalActionEnvelope: async (approvalId: string): Promise<ApprovalActionEnvelope> => {
    if (!isMockMode) return request<ApprovalActionEnvelope>(`/approvals/${approvalId}/action-envelope`);
    const record = getMockState().security.approvals.find((item) => item.approval_id === approvalId);
    if (!record) throw new Error('没有找到该审批单');
    return {
      approval_id: record.approval_id,
      tenant_id: identity.tenantId,
      run_id: record.run_id,
      tool_call_id: record.tool_call_id || `${record.approval_id}-tool`,
      tool_name: record.tool_name || 'financial.create_review_case',
      target_resource_id: record.target_resource_id || '',
      operation_type: record.operation_type || 'CASE_CREATE',
      canonical_arguments: record.canonical_arguments || '{}',
      canonical_arguments_hash: record.canonical_arguments_hash || 'demo-arguments-hash',
      action_hash: record.action_hash || 'demo-action-hash',
      requested_by: record.requested_by || record.requester,
      policy_version: record.policy_version || 'v1',
      risk_level: record.risk_level || 'HIGH',
      nonce: record.nonce || 'demo-nonce',
      issued_at: record.issued_at || record.time,
      expires_at: record.expires_at || new Date(Date.now() + 3_600_000).toISOString(),
      approval_status: (record.approval_status ?? record.decision) as ApprovalActionEnvelope['approval_status'],
      signature_reference: null,
    };
  },

  decideApproval: async (approvalId: string, approved: boolean, reason: string) => {
    if (!isMockMode) return request(`/approvals/${approvalId}/${approved ? 'approve' : 'reject'}`, {
      method: 'POST', body: JSON.stringify({ approved, reason }),
    });
    const state = getMockState();
    const record = state.security.approvals.find((item) => item.approval_id === approvalId);
    if (!record) throw new Error('没有找到该审批单');
    const status = record.approval_status ?? record.decision;
    if (status !== 'PENDING') throw new Error('该审批单已处理，不能再次决策');
    record.approval_status = approved ? 'APPROVED' : 'REJECTED';
    record.decision = approved ? 'APPROVED' : 'REJECTED';
    record.approver_id = identity.userId;
    record.approver = identity.userId;
    record.reason = reason;
    record.version = (record.version || 0) + 1;
    addMockAudit(approved ? 'APPROVAL_APPROVED' : 'APPROVAL_REJECTED', 'APPROVAL', approvalId, identity.userId, record.run_id);
    return { approval_id: approvalId, accepted: true, approved, demo: true };
  },

  revokeApproval: async (approvalId: string) => {
    if (!isMockMode) return request(`/approvals/${approvalId}/revoke`, { method: 'POST' });
    const state = getMockState();
    const record = state.security.approvals.find((item) => item.approval_id === approvalId);
    if (!record) throw new Error('没有找到该审批单');
    const status = record.approval_status ?? record.decision;
    if (status !== 'PENDING' && status !== 'APPROVED') throw new Error('该审批单当前状态不能撤销');
    record.approval_status = 'REVOKED';
    record.approver_id = identity.userId;
    record.approver = identity.userId;
    record.version = (record.version || 0) + 1;
    addMockAudit('APPROVAL_REVOKED', 'APPROVAL', approvalId, identity.userId, record.run_id);
    return { approval_id: approvalId, revoked: true, demo: true };
  },

  getSecuritySnapshot: async (): Promise<SecuritySnapshot> => {
    if (!isMockMode) return request<SecuritySnapshot>('/security/snapshot');
    return getMockState().security;
  },

  listBenchmarks: async (): Promise<BenchmarkExperiment[]> => {
    if (!isMockMode) return request<BenchmarkExperiment[]>('/benchmark/experiments?limit=100');
    return getMockState().benchmarks.slice().sort((a, b) => b.started_at.localeCompare(a.started_at));
  },

  listAblations: async (): Promise<AblationRecord[]> => {
    if (!isMockMode) return request<AblationRecord[]>('/benchmark/ablations');
    return getMockState().ablations;
  },

  aggregateBenchmark: async (body: { batch?: string; dataset?: string; limit?: number }): Promise<{ created: number }> => {
    if (!isMockMode) return request('/benchmark/aggregate', {
      method: 'POST',
      body: JSON.stringify({ batch: body.batch || 'manual', dataset: body.dataset || 'agent_runs', limit: body.limit || 500 }),
    });
    throw new ApiNotImplementedError('Benchmark 聚合（Mock 模式不支持）');
  },

  getRunContext: async (runId: string): Promise<ContextPack> => {
    if (!isMockMode) return request<ContextPack>(`/api/v1/runs/${runId}/context`);
    throw new ApiNotImplementedError('ContextPack 快照（Mock 模式不提供真实 Context 构建结果）');
  },

  listRunContextHistory: async (runId: string): Promise<ContextPackSummary[]> => {
    if (!isMockMode) {
      const res = await request<{ items: ContextPackSummary[] }>(`/api/v1/runs/${runId}/context/history`);
      return res.items || [];
    }
    throw new ApiNotImplementedError('ContextPack 历史（Mock 模式不提供）');
  },

  listRunContextSummaries: async (runId: string): Promise<ContextSummaryRecord[]> => {
    if (!isMockMode) {
      const res = await request<{ items: ContextSummaryRecord[] }>(`/api/v1/runs/${runId}/context/summaries`);
      return res.items || [];
    }
    throw new ApiNotImplementedError('Rolling Summary（Mock 模式不提供）');
  },

  getRunContextMetrics: async (runId: string): Promise<ContextMetrics> => {
    if (!isMockMode) return request<ContextMetrics>(`/api/v1/runs/${runId}/context/metrics`);
    throw new ApiNotImplementedError('Context 指标（Mock 模式不提供）');
  },

  getRunGraph: async (runId: string): Promise<TaskGraphDetail | null> => {
    if (!isMockMode) {
      try {
        return await request<TaskGraphDetail>(`/api/v1/runs/${runId}/graph`);
      } catch (error) {
        if (error instanceof Error && /graph not found/i.test(error.message)) return null;
        throw error;
      }
    }
    const run = getMockState().runs.find((item) => item.run_id === runId && item.tenant_id === identity.tenantId);
    return run ? makeDemoGraph(run) : null;
  },

  getGraphEvents: async (graphId: string, limit = 500): Promise<TaskGraphEvent[]> => {
    if (!isMockMode) {
      const res = await request<{ events: TaskGraphEvent[] }>(`/api/v1/graphs/${graphId}/events?limit=${limit}`);
      return res.events || [];
    }
    const now = Date.now();
    return [
      { event_id: 'ge-1', graph_id: graphId, run_id: graphId.replace('graph-demo-', ''), sequence_no: 1, event_type: 'GRAPH_STARTED', node_id: null, execution_duration_ms: 0, token_usage: {}, payload: { max_concurrency: 5 }, created_at: new Date(now - 12000).toISOString() },
      { event_id: 'ge-2', graph_id: graphId, run_id: graphId.replace('graph-demo-', ''), sequence_no: 2, event_type: 'NODE_COMPLETED', node_id: 'collect_inputs', execution_duration_ms: 1240, token_usage: { input: 1820, output: 240 }, payload: {}, created_at: new Date(now - 9000).toISOString() },
      { event_id: 'ge-3', graph_id: graphId, run_id: graphId.replace('graph-demo-', ''), sequence_no: 3, event_type: 'NODE_RETRY', node_id: 'analyze_facts', execution_duration_ms: 860, token_usage: {}, payload: { attempt: 2, error: 'DEMO temporary timeout' }, created_at: new Date(now - 6000).toISOString() },
    ];
  },

  getGraphNode: async (graphId: string, nodeId: string): Promise<TaskGraphNodeDetail | null> => {
    if (!isMockMode) {
      try {
        return await request<TaskGraphNodeDetail>(`/api/v1/graphs/${graphId}/nodes/${nodeId}`);
      } catch (error) {
        if (error instanceof Error && /not found/i.test(error.message)) return null;
        throw error;
      }
    }
    const graphIdParts = graphId.split('graph-demo-');
    const runId = graphIdParts.length > 1 ? graphIdParts[1] : '';
    const run = getMockState().runs.find((item) => item.run_id === runId && item.tenant_id === identity.tenantId);
    if (!run) return null;
    const graph = makeDemoGraph(run);
    const node = graph.nodes.find((item) => item.node_id === nodeId);
    return node ? { ...node, events: [] } : null;
  },

  replanGraph: async (_graphId: string, _proposal: Record<string, unknown>, _reason?: string): Promise<TaskGraphDetail> => {
    requireMock();
    throw new ApiNotImplementedError('运行中 Graph Replan（请在 API 模式通过后端校验执行）');
  },

  listConversations: async (): Promise<ChatConversation[]> => {
    if (!isMockMode) return request<ChatConversation[]>('/chat/conversations');
    return getMockState().conversations.slice()
      .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
  },

  createConversation: async (title = '新对话'): Promise<ChatConversation> => {
    if (!isMockMode) return request<ChatConversation>('/chat/conversations', {
      method: 'POST', body: JSON.stringify({ title }),
    });
    const state = getMockState();
    const now = new Date().toISOString();
    const conv: ChatConversation = {
      conversation_id: newId('conv'), title,
      session_id: `session-${newId('chat')}`, created_at: now, updated_at: now,
    };
    state.conversations.unshift(conv);
    state.chatMessagesByConversation[conv.conversation_id] = [];
    addMockAudit('CHAT_CONVERSATION_CREATED', 'CHAT_CONVERSATION', conv.conversation_id, identity.userId);
    return conv;
  },

  deleteConversation: async (conversationId: string): Promise<void> => {
    if (!isMockMode) { await request(`/chat/conversations/${conversationId}`, { method: 'DELETE' }); return; }
    const state = getMockState();
    state.conversations = state.conversations.filter((item) => item.conversation_id !== conversationId);
    delete state.chatMessagesByConversation[conversationId];
    addMockAudit('CHAT_CONVERSATION_DELETED', 'CHAT_CONVERSATION', conversationId, identity.userId);
  },

  listChatMessages: async (conversationId: string): Promise<ChatMessage[]> => {
    if (!isMockMode) return request<ChatMessage[]>(`/chat/conversations/${conversationId}/messages`);
    return (getMockState().chatMessagesByConversation[conversationId] || []).slice();
  },

  sendChatMessage: async (conversationId: string, content: string, useMemory = true, deepThink = false): Promise<ChatMessage & { usage?: Record<string, number>; memory_hits?: number }> => {
    if (!isMockMode) return request(`/chat/conversations/${conversationId}/messages`, {
      method: 'POST', body: JSON.stringify({ content, use_memory: useMemory, memory_limit: 6, deep_think: deepThink }),
    });
    const reply = `演示答复：已收到“${content.slice(0, 40)}”。当前为浏览器内合成数据，不作为业务结论。`;
    const now = new Date().toISOString();
    const userMessage: ChatMessage = {
      message_id: newId('msg'), conversation_id: conversationId, role: 'user', content,
      created_at: now,
    };
    const message: ChatMessage & { usage?: Record<string, number>; memory_hits?: number } = {
      message_id: newId('msg'), conversation_id: conversationId, role: 'assistant', content: reply,
      reasoning: deepThink ? '演示推理：先定位知识条目，再说明适用边界。' : undefined,
      created_at: now,
      usage: { prompt_tokens: 120, completion_tokens: 80 },
      memory_hits: useMemory ? 1 : 0,
    };
    const state = getMockState();
    const list = state.chatMessagesByConversation[conversationId] || [];
    list.push(userMessage, message);
    state.chatMessagesByConversation[conversationId] = list;
    addMockAudit('CHAT_MESSAGE_SENT', 'CHAT_CONVERSATION', conversationId, identity.userId);
    return message;
  },

  streamChatMessage: async (
    conversationId: string,
    content: string,
    handlers: {
      onStatus: (phase: string, message: string) => void;
      onReasoning: (delta: string) => void;
      onDelta: (delta: string) => void;
      onTool: (event: { phase: string; tool: string; result?: unknown }) => void;
      onDone: (result: ChatMessage & { usage?: Record<string, number>; memory_hits?: number; memory_saved?: { title: string; content: string; memory_id: string } | null; reasoning?: string; citations?: KnowledgeCitation[] }) => void;
      onError: (message: string) => void;
    },
    useMemory = true,
    deepThink = false,
    signal?: AbortSignal,
  ): Promise<void> => {
    if (isMockMode) {
      const reply = `演示流式答复：已收到“${content.slice(0, 40)}”。当前为浏览器内合成数据，不作为业务结论。`;
      const reasoning = deepThink ? '演示推理：先定位知识条目，再说明适用边界。' : undefined;
      handlers.onStatus('working', '演示模式：正在生成答复');
      if (reasoning) handlers.onReasoning(reasoning);
      await Promise.resolve();
      for (const part of reply.match(/.{1,12}/g) || [reply]) {
        if (signal?.aborted) return;
        handlers.onDelta(part);
        await Promise.resolve();
      }
      if (signal?.aborted) return;
      const now = new Date().toISOString();
      const userMessage: ChatMessage = {
        message_id: newId('msg'), conversation_id: conversationId, role: 'user', content,
        created_at: now,
      };
      const message = {
        message_id: newId('msg'),
        conversation_id: conversationId,
        role: 'assistant' as const,
        content: reply,
        ...(reasoning ? { reasoning } : {}),
        created_at: new Date().toISOString(),
        usage: { prompt_tokens: 120, completion_tokens: 80 },
        memory_hits: useMemory ? 1 : 0,
      };
      const state = getMockState();
      const list = state.chatMessagesByConversation[conversationId] || [];
      list.push(userMessage, message);
      state.chatMessagesByConversation[conversationId] = list;
      addMockAudit('CHAT_MESSAGE_STREAMED', 'CHAT_CONVERSATION', conversationId, identity.userId);
      handlers.onDone(message);
      return;
    }
    const doStreamFetch = (token: string | null) => fetch(`/api/chat/conversations/${conversationId}/messages/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(apiTenantId ? { 'X-Tenant-ID': apiTenantId } : {}),
      },
      body: JSON.stringify({ content, use_memory: useMemory, memory_limit: 6, deep_think: deepThink }),
      signal,
    });
    let response = await doStreamFetch(await ensureFreshToken());
    if (response.status === 401) {
      const refreshed = await renewToken();
      if (refreshed) response = await doStreamFetch(refreshed);
    }
    if (response.status === 401) {
      authHandlers.onUnauthorized?.();
      handlers.onError('登录状态已失效，请重新登录');
      return;
    }
    if (response.status === 403) {
      let detail = '没有执行该操作的权限';
      try { detail = (await response.json())?.detail || detail; } catch { /* keep default */ }
      const error = new ForbiddenError(detail);
      authHandlers.onForbidden?.(error);
      handlers.onError(detail);
      return;
    }
    if (!response.ok || !response.body) {
      let detail = `HTTP ${response.status}`;
      try { detail = (await response.json())?.detail || detail; } catch { /* keep status */ }
      handlers.onError(detail);
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let eventName = '';
    let eventData = '';
    const dispatch = () => {
      if (!eventName || !eventData) { eventName = ''; eventData = ''; return; }
      try {
        const data = JSON.parse(eventData);
        if (eventName === 'status') handlers.onStatus(data.phase || '', data.message || '');
        else if (eventName === 'reasoning') handlers.onReasoning(data.delta || '');
        else if (eventName === 'delta') handlers.onDelta(data.delta || '');
        else if (eventName === 'tool') handlers.onTool(data);
        else if (eventName === 'done') handlers.onDone(data);
        else if (eventName === 'error') handlers.onError(data.message || '未知错误');
      } catch { /* ignore malformed SSE payload */ }
      eventName = '';
      eventData = '';
    };
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (line.startsWith('event:')) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith('data:')) {
          eventData = line.slice(5).trim();
        } else if (line.trim() === '') {
          dispatch();
        }
      }
    }
    dispatch();
  },

  listKnowledgeDocuments: async (): Promise<KnowledgeDocument[]> => {
    if (!isMockMode) return request<KnowledgeDocument[]>('/knowledge/documents');
    return getMockState().knowledgeDocuments.slice()
      .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
  },

  getKnowledgeDocument: async (documentId: string): Promise<KnowledgeDocumentDetail> => {
    if (!isMockMode) return request<KnowledgeDocumentDetail>(`/knowledge/documents/${encodeURIComponent(documentId)}/chunks`);
    const state = getMockState();
    const doc = state.knowledgeDocuments.find((item) => item.document_id === documentId);
    if (!doc) throw new Error('没有找到该文档');
    const chunks = state.knowledgeChunksByDocument[documentId] || [];
    return { ...doc, raw_content: chunks.map((chunk) => chunk.content).join('\n\n'), chunks };
  },

  createKnowledgeDocument: async (body: {
    title: string; content: string; source?: string; content_type?: string;
    chunk_size?: number; chunk_overlap?: number;
  }): Promise<{ document_id: string; title: string; chunk_count: number; char_count: number }> => {
    if (!isMockMode) return request('/knowledge/documents', { method: 'POST', body: JSON.stringify(body) });
    const state = getMockState();
    const documentId = newId('doc');
    const chunks = mockChunkDocument(body.content, body.chunk_size);
    const doc: KnowledgeDocument = {
      document_id: documentId, title: body.title,
      source: body.source || '浏览器导入', content_type: body.content_type || 'auto',
      char_count: body.content.length, chunk_count: chunks.length,
      status: 'READY', created_at: new Date().toISOString(),
    };
    state.knowledgeDocuments.unshift(doc);
    state.knowledgeChunksByDocument[documentId] = chunks;
    addMockAudit('KNOWLEDGE_DOCUMENT_CREATED', 'KNOWLEDGE_DOCUMENT', documentId, identity.userId);
    return { document_id: documentId, title: doc.title, chunk_count: chunks.length, char_count: doc.char_count };
  },

  updateKnowledgeDocument: async (documentId: string, body: {
    title: string; content: string; source?: string; content_type?: string;
    chunk_size?: number; chunk_overlap?: number;
  }): Promise<{ document_id: string; title: string; chunk_count: number; char_count: number; status: string }> => {
    if (!isMockMode) return request(`/knowledge/documents/${encodeURIComponent(documentId)}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
    const state = getMockState();
    const doc = state.knowledgeDocuments.find((item) => item.document_id === documentId);
    if (!doc) throw new Error('没有找到该文档');
    const chunks = mockChunkDocument(body.content, body.chunk_size);
    doc.title = body.title;
    doc.source = body.source || doc.source;
    doc.content_type = body.content_type || doc.content_type;
    doc.char_count = body.content.length;
    doc.chunk_count = chunks.length;
    doc.status = 'READY';
    state.knowledgeChunksByDocument[documentId] = chunks;
    addMockAudit('KNOWLEDGE_DOCUMENT_UPDATED', 'KNOWLEDGE_DOCUMENT', documentId, identity.userId);
    return { document_id: documentId, title: doc.title, chunk_count: chunks.length, char_count: doc.char_count, status: doc.status };
  },

  deleteKnowledgeDocument: async (documentId: string): Promise<void> => {
    if (!isMockMode) { await request(`/knowledge/documents/${documentId}`, { method: 'DELETE' }); return; }
    const state = getMockState();
    state.knowledgeDocuments = state.knowledgeDocuments.filter((item) => item.document_id !== documentId);
    delete state.knowledgeChunksByDocument[documentId];
    addMockAudit('KNOWLEDGE_DOCUMENT_DELETED', 'KNOWLEDGE_DOCUMENT', documentId, identity.userId);
  },

  searchKnowledge: async (query: string, topK = 5): Promise<KnowledgeCitation[]> => {
    if (!isMockMode) return request<KnowledgeCitation[]>('/knowledge/search', {
      method: 'POST', body: JSON.stringify({ query, top_k: topK }),
    });
    const state = getMockState();
    const needle = query.toLowerCase();
    const results: KnowledgeCitation[] = [];
    for (const doc of state.knowledgeDocuments) {
      for (const chunk of state.knowledgeChunksByDocument[doc.document_id] || []) {
        if (chunk.content.toLowerCase().includes(needle) || doc.title.toLowerCase().includes(needle)) {
          results.push({
            chunk_id: chunk.chunk_id, index: results.length + 1,
            document_id: doc.document_id, document_title: doc.title,
            source: doc.source, heading: chunk.heading,
            chunk_index: chunk.chunk_index, content: chunk.content, score: 0.8,
          });
          if (results.length >= topK) return results;
        }
      }
    }
    return results;
  },

  knowledgeStats: async (): Promise<{ documents: number; chunks: number }> => {
    if (!isMockMode) return request('/knowledge/stats');
    const state = getMockState();
    return {
      documents: state.knowledgeDocuments.length,
      chunks: Object.values(state.knowledgeChunksByDocument).reduce((sum, list) => sum + list.length, 0),
    };
  },

  cancelChatStream: async (_conversationId: string): Promise<void> => {
    if (!isMockMode) { await request(`/chat/conversations/${_conversationId}/cancel`, { method: 'POST' }); return; }
    // Mock streaming is synchronous; abort signal on the caller side is enough.
  },

  saveStoppedMessage: async (conversationId: string, partial: {
    content: string; reasoning?: string; tool_calls?: Array<Record<string, unknown>>;
  }): Promise<ChatMessage> => {
    if (!isMockMode) return request(`/chat/conversations/${conversationId}/messages/stopped`, {
      method: 'POST', body: JSON.stringify(partial),
    });
    const message: ChatMessage = {
      message_id: newId('msg'), conversation_id: conversationId, role: 'assistant',
      content: partial.content, ...(partial.reasoning ? { reasoning: partial.reasoning } : {}),
      ...(partial.tool_calls ? { tool_calls: partial.tool_calls } : {}),
      created_at: new Date().toISOString(),
    };
    const state = getMockState();
    const list = state.chatMessagesByConversation[conversationId] || [];
    list.push(message);
    state.chatMessagesByConversation[conversationId] = list;
    addMockAudit('CHAT_MESSAGE_STOPPED', 'CHAT_CONVERSATION', conversationId, identity.userId);
    return message;
  },

  resetMockData: () => {
    requireMock();
    resetMockState();
  },
};

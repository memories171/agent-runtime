import type {
  AblationRecord, ApprovalRecord, AuditLog, AclEntry, AbacPolicy, BenchmarkExperiment,
  CheckpointRecord, DelegatedIdentity, LlmCallRecord, ManagedSkill, McpServerRecord,
  McpToolRecord, MemoryRecord, ReviewCase, RoleRecord, RunEvent, RunRecord, RunTelemetry,
  SecurityEvent, SecuritySnapshot, TenantRecord, ToolCallRecord, UserRecord, WorkerFailure,
  ChatConversation, ChatMessage, KnowledgeChunkDetail, KnowledgeDocument,
} from '../types';

const STORAGE_KEY = 'finora.agent-runtime.mock.v2';
const anchor = Date.now();
const isoAgo = (minutes: number) => new Date(anchor - minutes * 60_000).toISOString();
const isoAhead = (minutes: number) => isoAgo(-minutes);

export const newId = (prefix: string) => `${prefix}-DEMO-${Math.random().toString(36).slice(2, 9)}`;

export interface MockState {
  runs: RunRecord[];
  eventsByRun: Record<string, RunEvent[]>;
  telemetryByRun: Record<string, RunTelemetry>;
  cases: ReviewCase[];
  auditLogs: AuditLog[];
  mcpServers: McpServerRecord[];
  mcpTools: McpToolRecord[];
  managedSkills: ManagedSkill[];
  memories: MemoryRecord[];
  checkpoints: CheckpointRecord[];
  failures: WorkerFailure[];
  security: SecuritySnapshot;
  benchmarks: BenchmarkExperiment[];
  ablations: AblationRecord[];
  conversations: ChatConversation[];
  chatMessagesByConversation: Record<string, ChatMessage[]>;
  knowledgeDocuments: KnowledgeDocument[];
  knowledgeChunksByDocument: Record<string, KnowledgeChunkDetail[]>;
}

function makeEvent(type: string, payload: Record<string, unknown>, minutesAgo: number): RunEvent {
  return { event_id: newId('evt'), event_type: type, payload: { ...payload, source: 'DEMO' }, created_at: isoAgo(minutesAgo) };
}

function makeToolCalls(runId: string, tools: string[], risk: string[]): ToolCallRecord[] {
  return tools.map((tool, index) => ({
    call_id: `${runId}-tool-${index + 1}`,
    tool,
    server: tool.startsWith('mcp.') ? 'mcp-finance-read' : 'internal-tool-gateway',
    status: index === tools.length - 1 && risk[index] === 'critical' ? 'PENDING' : 'SUCCESS',
    started_at: isoAgo(80 - index * 9),
    duration_ms: 420 + index * 317,
    risk_level: risk[index] || 'low',
    idempotency_key: `idem-${runId.slice(-8)}-${index + 1}`,
  }));
}

function makeLlmCalls(runId: string, model: string): LlmCallRecord[] {
  return [
    { call_id: `${runId}-llm-1`, model, purpose: '规划任务步骤', prompt_tokens: 12_480, completion_tokens: 1_820, latency_ms: 2_140, started_at: isoAgo(78), status: 'SUCCESS' },
    { call_id: `${runId}-llm-2`, model, purpose: '整理工具结果与证据', prompt_tokens: 46_210, completion_tokens: 4_960, latency_ms: 4_680, started_at: isoAgo(48), status: 'SUCCESS' },
    { call_id: `${runId}-llm-3`, model, purpose: '生成人工复核摘要', prompt_tokens: 28_940, completion_tokens: 3_120, latency_ms: 3_260, started_at: isoAgo(17), status: 'SUCCESS' },
  ];
}

function makeTelemetry(
  run: RunRecord,
  tools: string[],
  risk: string[],
  checkpointIds: string[],
  recoveryCount: number,
): RunTelemetry {
  return {
    run_id: run.run_id,
    model: run.model_name || 'Model-A-DEMO',
    harness_version: run.harness_version || 'Harness-Alpha-DEMO',
    prompt_version: 'PROMPT-DEMO-v4.2',
    duration_seconds: run.duration_seconds || 0,
    heartbeat_at: run.heartbeat_at || null,
    deadline_at: run.deadline_at || null,
    current_step: run.current_step || '初始化',
    progress_pct: run.progress_pct || 0,
    token_input: run.token_input || 0,
    token_output: run.token_output || 0,
    estimated_cost_usd: run.estimated_cost_usd || 0,
    tool_calls: makeToolCalls(run.run_id, tools, risk),
    llm_calls: makeLlmCalls(run.run_id, run.model_name || 'Model-A-DEMO'),
    checkpoint_ids: checkpointIds,
    recovery_count: recoveryCount,
    demo: true,
  };
}

const runs: RunRecord[] = [
  {
    run_id: 'run-demo-long-001', tenant_id: 'demo-bank', user_id: 'analyst-01',
    session_id: 'session-demo-001', agent_id: 'transaction-monitoring-agent',
    prompt: '对 ACC-DEMO-001 近 30 天交易执行长时监测，汇总短时高频借记与重复参考号命中证据，并生成人工复核草稿。',
    status: 'RUNNING', created_at: isoAgo(92), updated_at: isoAgo(1),
    model_name: 'Model-A-DEMO', harness_version: 'Harness-Alpha-DEMO',
    duration_seconds: 5_520, heartbeat_at: isoAgo(1), deadline_at: isoAhead(268),
    progress_pct: 68, current_step: '聚合交易命中证据并校验 ACL',
    token_input: 184_320, token_output: 26_540, estimated_cost_usd: 4.82, demo: true,
  },
  {
    run_id: 'run-demo-pause-002', tenant_id: 'demo-bank', user_id: 'analyst-01',
    session_id: 'session-demo-002', agent_id: 'kyc-review-agent',
    prompt: '整理 CUS-DEMO-002 的尽调材料完整性，列出资质缺口与需人工核验的字段。',
    status: 'PAUSED', created_at: isoAgo(64), updated_at: isoAgo(9),
    model_name: 'Model-B-DEMO', harness_version: 'Harness-Beta-DEMO',
    duration_seconds: 2_460, heartbeat_at: isoAgo(9), deadline_at: isoAhead(180),
    progress_pct: 41, current_step: '等待恢复：材料版本比对',
    token_input: 98_400, token_output: 14_220, estimated_cost_usd: 2.61, demo: true,
  },
  {
    run_id: 'run-demo-approval-003', tenant_id: 'demo-bank', user_id: 'analyst-01',
    session_id: 'session-demo-003', agent_id: 'compliance-assistant',
    prompt: '导出客户 CUS-DEMO-002 的扩展尽调摘要，并提交独立合规复核。',
    status: 'WAITING_APPROVAL', created_at: isoAgo(58), updated_at: isoAgo(7),
    model_name: 'Model-A-DEMO', harness_version: 'Harness-Beta-DEMO',
    duration_seconds: 3_120, heartbeat_at: isoAgo(7), deadline_at: isoAhead(120),
    progress_pct: 76, current_step: '等待独立审批人确认高风险导出动作',
    token_input: 132_900, token_output: 18_640, estimated_cost_usd: 3.44, demo: true,
  },
  {
    run_id: 'run-demo-recover-004', tenant_id: 'demo-bank', user_id: 'analyst-02',
    session_id: 'session-demo-004', agent_id: 'financial-research-agent',
    prompt: '研究企业客户风险变化，结合财报与市场信息输出可追溯摘要。',
    status: 'SUCCEEDED', created_at: isoAgo(150), updated_at: isoAgo(52),
    model_name: 'Model-B-DEMO', harness_version: 'Harness-Alpha-DEMO',
    duration_seconds: 5_880, heartbeat_at: isoAgo(52), deadline_at: isoAgo(45),
    progress_pct: 100, current_step: '完成：研究摘要已归档',
    token_input: 246_800, token_output: 38_920, estimated_cost_usd: 6.17, demo: true,
  },
  {
    run_id: 'run-demo-failed-005', tenant_id: 'demo-bank', user_id: 'analyst-01',
    session_id: 'session-demo-005', agent_id: 'reconciliation-agent',
    prompt: '核对演示账户与交易流水，输出差异清单及证据链。',
    status: 'FAILED', created_at: isoAgo(112), updated_at: isoAgo(79),
    model_name: 'Model-A-DEMO', harness_version: 'Harness-Beta-DEMO',
    duration_seconds: 1_980, heartbeat_at: isoAgo(79), deadline_at: isoAgo(70),
    progress_pct: 63, current_step: '失败：MCP 数据源超时且恢复未成功',
    token_input: 84_200, token_output: 9_860, estimated_cost_usd: 2.08, demo: true,
    error: 'DEMO：mcp-finance-read 连续超时，超过恢复重试上限。',
  },
  {
    run_id: 'run-demo-other-006', tenant_id: 'other-bank', user_id: 'analyst-02',
    session_id: 'session-demo-006', agent_id: 'transaction-monitoring-agent',
    prompt: '对 other-bank 演示账户执行隔离范围内的交易筛查。',
    status: 'RUNNING', created_at: isoAgo(47), updated_at: isoAgo(2),
    model_name: 'Model-B-DEMO', harness_version: 'Harness-Beta-DEMO',
    duration_seconds: 2_700, heartbeat_at: isoAgo(2), deadline_at: isoAhead(180),
    progress_pct: 55, current_step: '读取本租户授权交易',
    token_input: 71_400, token_output: 10_280, estimated_cost_usd: 1.92, demo: true,
  },
  {
    run_id: 'run-demo-short-007', tenant_id: 'demo-bank', user_id: 'analyst-01',
    session_id: 'session-demo-007', agent_id: 'credit-risk-agent',
    prompt: '整理信用风险辅助分析要点，并标注需要人工复核的假设。',
    status: 'SUCCEEDED', created_at: isoAgo(24), updated_at: isoAgo(14),
    model_name: 'Model-A-DEMO', harness_version: 'Harness-Alpha-DEMO',
    duration_seconds: 610, heartbeat_at: isoAgo(14), deadline_at: isoAhead(60),
    progress_pct: 100, current_step: '完成：风险要点已整理',
    token_input: 42_600, token_output: 7_440, estimated_cost_usd: 1.14, demo: true,
  },
  {
    run_id: 'run-demo-cancel-008', tenant_id: 'demo-bank', user_id: 'analyst-02',
    session_id: 'session-demo-008', agent_id: 'financial-research-agent',
    prompt: '汇总历史绩效资料并生成演示性研究草稿。',
    status: 'CANCELLED', created_at: isoAgo(39), updated_at: isoAgo(27),
    model_name: 'Model-B-DEMO', harness_version: 'Harness-Beta-DEMO',
    duration_seconds: 720, heartbeat_at: isoAgo(27), deadline_at: isoAgo(20),
    progress_pct: 34, current_step: '已取消：用户主动中止',
    token_input: 31_200, token_output: 4_120, estimated_cost_usd: 0.84, demo: true,
  },
  {
    run_id: 'run-demo-resume-009', tenant_id: 'demo-bank', user_id: 'analyst-01',
    session_id: 'session-demo-009', agent_id: 'transaction-monitoring-agent',
    prompt: '对 ACC-DEMO-002 执行跨日交易复核，保存检查点并验证 Worker 故障后的恢复路径。',
    status: 'RUNNING', created_at: isoAgo(44), updated_at: isoAgo(3),
    model_name: 'Model-A-DEMO', harness_version: 'Harness-Beta-DEMO',
    duration_seconds: 2_460, heartbeat_at: isoAgo(3), deadline_at: isoAhead(96),
    progress_pct: 58, current_step: '已从 checkpoint-demo-009 恢复并继续复核',
    token_input: 116_900, token_output: 17_250, estimated_cost_usd: 3.06, demo: true,
  },
  {
    run_id: 'run-demo-sla-010', tenant_id: 'demo-bank', user_id: 'compliance-01',
    session_id: 'session-demo-010', agent_id: 'compliance-assistant',
    prompt: '复核演示交易监测提示，输出可解释的审批意见草稿。',
    status: 'SUCCEEDED', created_at: isoAgo(206), updated_at: isoAgo(158),
    model_name: 'Model-B-DEMO', harness_version: 'Harness-Alpha-DEMO',
    duration_seconds: 2_880, heartbeat_at: isoAgo(158), deadline_at: isoAgo(140),
    progress_pct: 100, current_step: '完成：审批意见草稿已记录',
    token_input: 154_200, token_output: 21_880, estimated_cost_usd: 4.01, demo: true,
  },
];

const eventsByRun: Record<string, RunEvent[]> = {
  'run-demo-long-001': [
    makeEvent('RUN_CREATED', { agent_id: 'transaction-monitoring-agent' }, 92),
    makeEvent('PLAN_CREATED', { steps: ['读取授权交易', '运行监测规则', '整理证据', '生成人工复核摘要'] }, 91),
    makeEvent('TOOL_CALL_COMPLETED', { tool: 'financial.list_transactions', count: 420 }, 78),
    makeEvent('CHECKPOINT_SAVED', { checkpoint_id: 'checkpoint-demo-001', step: '读完交易分片 3/6' }, 61),
    makeEvent('TOOL_CALL_COMPLETED', { tool: 'financial.screen_transactions', finding_count: 7 }, 40),
    makeEvent('HEARTBEAT', { progress_pct: 68 }, 1),
  ],
  'run-demo-pause-002': [
    makeEvent('RUN_CREATED', { agent_id: 'kyc-review-agent' }, 64),
    makeEvent('CHECKPOINT_SAVED', { checkpoint_id: 'checkpoint-demo-002' }, 42),
    makeEvent('RUN_PAUSED', { actor: 'analyst-01', mode: 'DEMO' }, 9),
  ],
  'run-demo-approval-003': [
    makeEvent('RUN_CREATED', { agent_id: 'compliance-assistant' }, 58),
    makeEvent('POLICY_CHECK_PASSED', { risk_level: 'high' }, 57),
    makeEvent('WAITING_APPROVAL', { action: 'customer_due_diligence_export', approver_role: 'compliance' }, 7),
  ],
  'run-demo-recover-004': [
    makeEvent('RUN_CREATED', { agent_id: 'financial-research-agent' }, 150),
    makeEvent('CHECKPOINT_SAVED', { checkpoint_id: 'checkpoint-demo-004' }, 108),
    makeEvent('WORKER_FAILED', { worker_id: 'worker-demo-2', reason: 'pod evicted' }, 104),
    makeEvent('RECOVERING', { recovery_count: 1 }, 103),
    makeEvent('RESUMED', { checkpoint_id: 'checkpoint-demo-004' }, 101),
    makeEvent('RUN_SUCCEEDED', { summary: 'research summary saved' }, 52),
  ],
  'run-demo-failed-005': [
    makeEvent('RUN_CREATED', { agent_id: 'reconciliation-agent' }, 112),
    makeEvent('CHECKPOINT_SAVED', { checkpoint_id: 'checkpoint-demo-005' }, 91),
    makeEvent('WORKER_FAILED', { worker_id: 'worker-demo-1', reason: 'mcp timeout' }, 88),
    makeEvent('RECOVERING', { retry_count: 2 }, 87),
    makeEvent('RECOVERY_FAILED', { reason: 'exceeded retry budget' }, 79),
  ],
  'run-demo-other-006': [
    makeEvent('RUN_CREATED', { tenant_id: 'other-bank' }, 47),
    makeEvent('TOOL_CALL_COMPLETED', { tool: 'financial.list_transactions' }, 21),
    makeEvent('HEARTBEAT', { progress_pct: 55 }, 2),
  ],
  'run-demo-short-007': [
    makeEvent('RUN_CREATED', { agent_id: 'credit-risk-agent' }, 24),
    makeEvent('TOOL_CALL_COMPLETED', { tool: 'financial.get_account_summary' }, 19),
    makeEvent('RUN_SUCCEEDED', { mode: 'DEMO' }, 14),
  ],
  'run-demo-cancel-008': [
    makeEvent('RUN_CREATED', { agent_id: 'financial-research-agent' }, 39),
    makeEvent('RUN_CANCELLED', { actor: 'analyst-02', mode: 'DEMO' }, 27),
  ],
  'run-demo-resume-009': [
    makeEvent('RUN_CREATED', { agent_id: 'transaction-monitoring-agent' }, 44),
    makeEvent('CHECKPOINT_SAVED', { checkpoint_id: 'checkpoint-demo-009' }, 28),
    makeEvent('WORKER_FAILED', { worker_id: 'worker-demo-3', reason: 'process restart' }, 26),
    makeEvent('RECOVERING', { recovery_count: 1 }, 25),
    makeEvent('RESUMED', { checkpoint_id: 'checkpoint-demo-009' }, 24),
    makeEvent('HEARTBEAT', { progress_pct: 58 }, 3),
  ],
  'run-demo-sla-010': [
    makeEvent('RUN_CREATED', { agent_id: 'compliance-assistant' }, 206),
    makeEvent('APPROVAL_DECISION', { approved: true, approver: 'compliance-01' }, 162),
    makeEvent('RUN_SUCCEEDED', { mode: 'DEMO' }, 158),
  ],
};

const telemetryByRun: Record<string, RunTelemetry> = {
  'run-demo-long-001': makeTelemetry(runs[0], ['financial.get_account_summary', 'financial.list_transactions', 'financial.screen_transactions'], ['low', 'medium', 'medium'], ['checkpoint-demo-001'], 0),
  'run-demo-pause-002': makeTelemetry(runs[1], ['financial.get_customer_profile', 'mcp.kyc-docs.list'], ['low', 'medium'], ['checkpoint-demo-002'], 0),
  'run-demo-approval-003': makeTelemetry(runs[2], ['financial.get_customer_profile', 'financial.export_due_diligence'], ['low', 'critical'], [], 0),
  'run-demo-recover-004': makeTelemetry(runs[3], ['mcp.market.search', 'mcp.filings.read', 'memory.search'], ['low', 'low', 'low'], ['checkpoint-demo-004'], 1),
  'run-demo-failed-005': makeTelemetry(runs[4], ['financial.get_account_summary', 'mcp.finance.read', 'financial.reconcile'], ['low', 'high', 'medium'], ['checkpoint-demo-005'], 2),
  'run-demo-other-006': makeTelemetry(runs[5], ['financial.list_transactions', 'financial.screen_transactions'], ['low', 'medium'], [], 0),
  'run-demo-short-007': makeTelemetry(runs[6], ['financial.get_customer_profile'], ['low'], [], 0),
  'run-demo-cancel-008': makeTelemetry(runs[7], ['mcp.market.search'], ['low'], [], 0),
  'run-demo-resume-009': makeTelemetry(runs[8], ['financial.list_transactions', 'financial.screen_transactions'], ['low', 'medium'], ['checkpoint-demo-009'], 1),
  'run-demo-sla-010': makeTelemetry(runs[9], ['financial.get_customer_profile', 'financial.create_review_case'], ['low', 'high'], [], 0),
};

const cases: ReviewCase[] = [
  { case_id: 'CASE-DEMO-1001', case_type: 'TRANSACTION_MONITORING', subject_id: 'ACC-DEMO-001', rationale: '短时高频借记命中演示规则，建议结合客户背景人工核查。', evidence_transaction_ids: ['TX-DEMO-001', 'TX-DEMO-002'], status: 'OPEN', created_by: 'analyst-01', created_at: isoAgo(220) },
  { case_id: 'CASE-DEMO-1002', case_type: 'KYC_REVIEW', subject_id: 'CUS-DEMO-002', rationale: '尽调材料版本过期，需要合规人员确认完整性。', evidence_transaction_ids: [], status: 'IN_REVIEW', created_by: 'analyst-02', created_at: isoAgo(95) },
  { case_id: 'CASE-DEMO-1003', case_type: 'RECONCILIATION', subject_id: 'ACC-DEMO-002', rationale: '演示对账差异待人工确认。', evidence_transaction_ids: ['TX-DEMO-009'], status: 'PENDING', created_by: 'compliance-01', created_at: isoAgo(38) },
];

const auditLogs: AuditLog[] = [
  { audit_id: 'AUD-DEMO-001', actor_user_id: 'analyst-01', run_id: 'run-demo-long-001', action: 'TRANSACTION_SCREENING_STARTED', resource_type: 'ACCOUNT', resource_id: 'ACC-DEMO-001', details: { mode: 'DEMO' }, created_at: isoAgo(91) },
  { audit_id: 'AUD-DEMO-002', actor_user_id: 'compliance-01', run_id: 'run-demo-sla-010', action: 'APPROVAL_GRANTED', resource_type: 'REVIEW_CASE', resource_id: 'CASE-DEMO-1002', details: { mode: 'DEMO' }, created_at: isoAgo(162) },
  { audit_id: 'AUD-DEMO-003', actor_user_id: 'analyst-02', run_id: 'run-demo-other-006', action: 'TENANT_SCOPE_CHECK', resource_type: 'TENANT', resource_id: 'other-bank', details: { mode: 'DEMO', decision: 'ALLOWED' }, created_at: isoAgo(46) },
  { audit_id: 'AUD-DEMO-004', actor_user_id: 'analyst-01', run_id: 'run-demo-failed-005', action: 'RECOVERY_FAILED', resource_type: 'WORKER', resource_id: 'worker-demo-1', details: { mode: 'DEMO', retry_count: 2 }, created_at: isoAgo(79) },
];

const mcpServers: McpServerRecord[] = [
  { server_id: 'mcp-finance-read', name: '金融只读数据服务', transport: 'Streamable HTTP', status: 'HEALTHY', protocol_version: '2025-06-18', tools_count: 6, resources_count: 3, prompts_count: 2, last_connected_at: isoAgo(1), error_rate_pct: 0.2, latency_ms: 84, description: '客户、账户与交易的只读查询，强制 Tenant 与 Resource Grant 校验。', demo: true },
  { server_id: 'mcp-kyc-docs', name: 'KYC 文档服务', transport: 'SSE', status: 'DEGRADED', protocol_version: '2024-11-05', tools_count: 4, resources_count: 12, prompts_count: 1, last_connected_at: isoAgo(6), error_rate_pct: 3.8, latency_ms: 512, description: '文档元数据与完整性提示，不返回原始身份证件内容。', demo: true },
  { server_id: 'mcp-market-research', name: '市场研究服务', transport: 'stdio', status: 'HEALTHY', protocol_version: '2025-06-18', tools_count: 3, resources_count: 8, prompts_count: 4, last_connected_at: isoAgo(2), error_rate_pct: 0.6, latency_ms: 168, description: '公开财报与市场摘要检索，输出需附来源和时间口径。', demo: true },
  { server_id: 'mcp-review-cases', name: '内部案件流转服务', transport: 'Streamable HTTP', status: 'UNHEALTHY', protocol_version: '2024-11-05', tools_count: 2, resources_count: 1, prompts_count: 0, last_connected_at: isoAgo(34), error_rate_pct: 18.4, latency_ms: 1_420, description: '内部复核案件草稿写入；当前演示连接异常。', demo: true },
  { server_id: 'mcp-other-bank', name: 'Other Bank 隔离服务', transport: 'Streamable HTTP', status: 'HEALTHY', protocol_version: '2025-06-18', tools_count: 3, resources_count: 2, prompts_count: 1, last_connected_at: isoAgo(4), error_rate_pct: 0.1, latency_ms: 96, description: 'other-bank 租户专用 MCP，演示跨租户隔离。', demo: true },
];

const mcpTools: McpToolRecord[] = [
  { name: 'financial.get_customer_profile', server_id: 'mcp-finance-read', description: '读取授权范围内的客户类型、国家与 KYC 状态。', required_scopes: ['financial:customer:read'], risk_level: 'low', idempotent: true, demo: true },
  { name: 'financial.get_account_summary', server_id: 'mcp-finance-read', description: '读取掩码账户、币种、余额与状态。', required_scopes: ['financial:account:read'], risk_level: 'low', idempotent: true, demo: true },
  { name: 'financial.list_transactions', server_id: 'mcp-finance-read', description: '查询授权账户交易明细。', required_scopes: ['financial:transactions:read'], risk_level: 'medium', idempotent: true, demo: true },
  { name: 'financial.screen_transactions', server_id: 'mcp-finance-read', description: '运行演示交易规则并返回复核提示。', required_scopes: ['financial:transactions:read', 'financial:monitoring:run'], risk_level: 'medium', idempotent: true, demo: true },
  { name: 'financial.create_review_case', server_id: 'mcp-review-cases', description: '创建内部复核案件草稿，需独立审批。', required_scopes: ['financial:case:create', 'approval:approve'], risk_level: 'high', idempotent: true, demo: true },
  { name: 'mcp.kyc-docs.list', server_id: 'mcp-kyc-docs', description: '列出 KYC 文档元数据。', required_scopes: ['financial:case:read'], risk_level: 'low', idempotent: true, demo: true },
  { name: 'mcp.market.search', server_id: 'mcp-market-research', description: '检索公开财报与市场摘要。', required_scopes: ['agent:execute'], risk_level: 'low', idempotent: true, demo: true },
  { name: 'mcp.filings.read', server_id: 'mcp-market-research', description: '读取指定财报片段并保留来源。', required_scopes: ['agent:execute'], risk_level: 'low', idempotent: true, demo: true },
];

const managedSkills: ManagedSkill[] = [
  { skill_id: 'transaction-monitoring', name: '交易监测', description: '整理交易模式、规则命中与证据链，仅输出人工复核提示。', version: '2.4.1', release_status: 'STABLE', tools: ['financial.list_transactions', 'financial.screen_transactions'], required_scopes: ['financial:transactions:read', 'financial:monitoring:run'], updated_at: isoAgo(72), enabled: true, demo: true },
  { skill_id: 'kyc-review', name: 'KYC 资料复核', description: '检查尽调资料完整性并标注待补充字段。', version: '1.8.0', release_status: 'STABLE', tools: ['financial.get_customer_profile', 'mcp.kyc-docs.list'], required_scopes: ['financial:case:read'], updated_at: isoAgo(96), enabled: true, demo: true },
  { skill_id: 'financial-research', name: '财报研究', description: '汇总可追溯财务与市场信息，强调期间、币种和口径。', version: '3.1.0', release_status: 'BETA', tools: ['mcp.market.search', 'mcp.filings.read'], required_scopes: ['agent:execute'], updated_at: isoAgo(48), enabled: true, demo: true },
  { skill_id: 'financial-reconciliation', name: '财务对账', description: '识别账务差异并输出调查线索。', version: '1.2.3', release_status: 'DRAFT', tools: ['financial.get_account_summary', 'financial.list_transactions'], required_scopes: ['financial:account:read', 'financial:transactions:read'], updated_at: isoAgo(132), enabled: false, demo: true },
  { skill_id: 'credit-risk-review', name: '信用风险辅助分析', description: '整理信用风险因素、变化与人工复核事项。', version: '2.0.0', release_status: 'STABLE', tools: ['mcp.market.search', 'memory.search'], required_scopes: ['agent:execute', 'memory:read'], updated_at: isoAgo(164), enabled: true, demo: true },
  { skill_id: 'compliance-approval', name: '合规独立审批', description: '为高风险动作生成审批上下文，强制职责分离。', version: '0.9.1', release_status: 'BETA', tools: ['financial.create_review_case'], required_scopes: ['approval:approve', 'financial:case:approve'], updated_at: isoAgo(28), enabled: true, demo: true },
];

const memories: MemoryRecord[] = [
  { memory_id: 'mem-demo-001', type: 'WORKING', scope: 'user', title: '当前复核上下文', content: '本轮复核聚焦 ACC-DEMO-001 的短时高频借记命中；尚未形成风险定性。', source_run_id: 'run-demo-long-001', owner: 'analyst-01', tenant_id: 'demo-bank', confidence: 0.92, created_at: isoAgo(88), updated_at: isoAgo(3), expires_at: isoAhead(180), acl: ['user:analyst-01'], archived: false, demo: true },
  { memory_id: 'mem-demo-002', type: 'EPISODIC', scope: 'user', title: '上次暂停的尽调任务', content: 'CUS-DEMO-002 的材料版本比对在检查点 checkpoint-demo-002 暂停，恢复后应从版本差异步骤继续。', source_run_id: 'run-demo-pause-002', owner: 'analyst-01', tenant_id: 'demo-bank', confidence: 0.88, created_at: isoAgo(62), updated_at: isoAgo(10), expires_at: isoAhead(1440), acl: ['user:analyst-01'], archived: false, demo: true },
  { memory_id: 'mem-demo-003', type: 'SEMANTIC', scope: 'organization', title: '交易复核提示边界', content: '规则命中仅代表调查线索；不得自动描述为洗钱、欺诈或违法结论。', source_run_id: null, owner: 'compliance-01', tenant_id: 'demo-bank', confidence: 0.99, created_at: isoAgo(720), updated_at: isoAgo(210), expires_at: null, acl: ['tenant:demo-bank:read'], archived: false, demo: true },
  { memory_id: 'mem-demo-004', type: 'PROCEDURAL', scope: 'team', title: '高风险导出审批步骤', content: '先核对主体和动作参数，再由不同用户以 approval:approve 与 financial:case:approve 审批。', source_run_id: 'run-demo-approval-003', owner: 'compliance-01', tenant_id: 'demo-bank', confidence: 0.95, created_at: isoAgo(510), updated_at: isoAgo(58), expires_at: isoAhead(2880), acl: ['team:compliance:read', 'team:compliance:write'], archived: false, demo: true },
  { memory_id: 'mem-demo-005', type: 'EPISODIC', scope: 'team', title: 'Worker 故障恢复记录', content: 'run-demo-recover-004 在 checkpoint-demo-004 恢复一次后成功；失败原因演示为 pod evicted。', source_run_id: 'run-demo-recover-004', owner: 'analyst-02', tenant_id: 'demo-bank', confidence: 0.84, created_at: isoAgo(104), updated_at: isoAgo(53), expires_at: isoAhead(720), acl: ['team:platform:read'], archived: false, demo: true },
  { memory_id: 'mem-demo-006', type: 'SEMANTIC', scope: 'organization', title: '金额币种口径', content: '金额以最小货币单位整数存储，解释时必须结合 currency，不得跨币种直接相加。', source_run_id: null, owner: 'analyst-01', tenant_id: 'demo-bank', confidence: 0.98, created_at: isoAgo(880), updated_at: isoAgo(320), expires_at: null, acl: ['tenant:demo-bank:read'], archived: false, demo: true },
  { memory_id: 'mem-demo-007', type: 'WORKING', scope: 'team', title: '对账差异临时假设', content: 'ACC-DEMO-002 的差异可能来自跨日记账时间窗口，需要人工查证原交易。', source_run_id: 'run-demo-failed-005', owner: 'analyst-01', tenant_id: 'demo-bank', confidence: 0.61, created_at: isoAgo(80), updated_at: isoAgo(76), expires_at: isoAhead(60), acl: ['team:ops:read'], archived: false, demo: true },
  { memory_id: 'mem-demo-008', type: 'PROCEDURAL', scope: 'organization', title: 'MCP 工具调用校验顺序', content: '先做 JSON Schema 校验，再检查 Scope、Resource Grant、风险等级与幂等键。', source_run_id: null, owner: 'platform-admin', tenant_id: 'demo-bank', confidence: 0.97, created_at: isoAgo(1_200), updated_at: isoAgo(180), expires_at: null, acl: ['tenant:demo-bank:read'], archived: false, demo: true },
  { memory_id: 'mem-demo-009', type: 'EPISODIC', scope: 'user', title: '上一轮 Benchmark 观察', content: '固定 Harness-Alpha-DEMO 时，Model-B-DEMO 的恢复成功率更高；数据为合成演示。', source_run_id: null, owner: 'analyst-01', tenant_id: 'demo-bank', confidence: 0.73, created_at: isoAgo(190), updated_at: isoAgo(188), expires_at: isoAhead(2_160), acl: ['user:analyst-01'], archived: false, demo: true },
  { memory_id: 'mem-demo-010', type: 'SEMANTIC', scope: 'team', title: '跨租户隔离原则', content: 'other-bank 的客户、账户和交易不得出现在 demo-bank 的检索结果中。', source_run_id: 'run-demo-other-006', owner: 'analyst-02', tenant_id: 'other-bank', confidence: 0.99, created_at: isoAgo(430), updated_at: isoAgo(46), expires_at: null, acl: ['tenant:other-bank:read'], archived: false, demo: true },
  { memory_id: 'mem-demo-011', type: 'PROCEDURAL', scope: 'user', title: '长任务心跳检查', content: '超过 30 分钟的任务每 60 秒记录一次 heartbeat，超时后进入恢复流程。', source_run_id: 'run-demo-long-001', owner: 'analyst-01', tenant_id: 'demo-bank', confidence: 0.9, created_at: isoAgo(600), updated_at: isoAgo(5), expires_at: isoAhead(4_320), acl: ['user:analyst-01'], archived: false, demo: true },
  { memory_id: 'mem-demo-012', type: 'EPISODIC', scope: 'organization', title: '已归档的旧演示结论', content: '旧版演示规则结论已废弃，不得用于新评测。', source_run_id: null, owner: 'platform-admin', tenant_id: 'demo-bank', confidence: 0.55, created_at: isoAgo(2_400), updated_at: isoAgo(900), expires_at: isoAgo(200), acl: ['tenant:demo-bank:read'], archived: true, demo: true },
];

const checkpoints: CheckpointRecord[] = [
  { checkpoint_id: 'checkpoint-demo-001', run_id: 'run-demo-long-001', tenant_id: 'demo-bank', created_at: isoAgo(61), step: '读完交易分片 3/6', summary: '保存已筛选交易 ID、规则命中摘要和游标位置。', size_kb: 284, status: 'SAVED', worker_id: 'worker-demo-1', idempotency_key: 'idem-cp-001', duplicate_protection: true, demo: true },
  { checkpoint_id: 'checkpoint-demo-002', run_id: 'run-demo-pause-002', tenant_id: 'demo-bank', created_at: isoAgo(42), step: '材料版本比对', summary: '保存已比对文档清单与差异集合。', size_kb: 156, status: 'RESTORED', worker_id: 'worker-demo-2', idempotency_key: 'idem-cp-002', duplicate_protection: true, demo: true },
  { checkpoint_id: 'checkpoint-demo-004', run_id: 'run-demo-recover-004', tenant_id: 'demo-bank', created_at: isoAgo(108), step: '财报摘要归档前', summary: '保存来源引用、期间与币种口径。', size_kb: 412, status: 'RESTORED', worker_id: 'worker-demo-2', idempotency_key: 'idem-cp-004', duplicate_protection: true, demo: true },
  { checkpoint_id: 'checkpoint-demo-005', run_id: 'run-demo-failed-005', tenant_id: 'demo-bank', created_at: isoAgo(91), step: '对账差异聚类', summary: '保存差异清单与游标，后续恢复失败。', size_kb: 198, status: 'SAVED', worker_id: 'worker-demo-1', idempotency_key: 'idem-cp-005', duplicate_protection: true, demo: true },
  { checkpoint_id: 'checkpoint-demo-009', run_id: 'run-demo-resume-009', tenant_id: 'demo-bank', created_at: isoAgo(28), step: '跨日交易分片 2/4', summary: '保存已完成的筛查结果与去重键。', size_kb: 322, status: 'RESTORED', worker_id: 'worker-demo-3', idempotency_key: 'idem-cp-009', duplicate_protection: true, demo: true },
];

const failures: WorkerFailure[] = [
  { failure_id: 'fail-demo-001', run_id: 'run-demo-recover-004', tenant_id: 'demo-bank', worker_id: 'worker-demo-2', occurred_at: isoAgo(104), reason: 'Pod evicted during activity execution', recovery_status: 'RECOVERED', recovery_count: 1, retry_count: 1, recovery_attempt: 1, activity_retry_count: 1, recovery_started_at: isoAgo(103), recovery_completed_at: isoAgo(102), fencing_token: 2, recovered_from_checkpoint_id: 'checkpoint-demo-004', recovery_position: '财报摘要归档前', idempotency_key: 'idem-rec-004', duplicate_protection: true, demo: true },
  { failure_id: 'fail-demo-002', run_id: 'run-demo-failed-005', tenant_id: 'demo-bank', worker_id: 'worker-demo-1', occurred_at: isoAgo(88), reason: 'MCP data source timeout', recovery_status: 'FAILED', recovery_count: 2, retry_count: 3, recovery_attempt: 2, activity_retry_count: 3, recovery_started_at: isoAgo(87), recovery_completed_at: null, fencing_token: 3, recovered_from_checkpoint_id: 'checkpoint-demo-005', recovery_position: '对账差异聚类', idempotency_key: 'idem-rec-005', duplicate_protection: true, demo: true },
  { failure_id: 'fail-demo-003', run_id: 'run-demo-resume-009', tenant_id: 'demo-bank', worker_id: 'worker-demo-3', occurred_at: isoAgo(26), reason: 'Worker process restarted', recovery_status: 'RECOVERED', recovery_count: 1, retry_count: 1, recovery_attempt: 1, activity_retry_count: 1, recovery_started_at: isoAgo(25), recovery_completed_at: isoAgo(24), fencing_token: 2, recovered_from_checkpoint_id: 'checkpoint-demo-009', recovery_position: '跨日交易分片 2/4', idempotency_key: 'idem-rec-009', duplicate_protection: true, demo: true },
  { failure_id: 'fail-demo-004', run_id: 'run-demo-short-007', tenant_id: 'demo-bank', worker_id: 'worker-demo-2', occurred_at: isoAgo(18), reason: 'Transient network reset', recovery_status: 'RECOVERING', recovery_count: 0, retry_count: 1, recovery_attempt: 1, activity_retry_count: 1, recovery_started_at: isoAgo(17), recovery_completed_at: null, fencing_token: 1, recovered_from_checkpoint_id: null, recovery_position: '等待新检查点', idempotency_key: 'idem-rec-007', duplicate_protection: true, demo: true },
];

const tenants: TenantRecord[] = [
  { tenant_id: 'demo-bank', name: 'Demo Bank', status: 'ACTIVE', users: 5, created_at: isoAgo(9_000), demo: true },
  { tenant_id: 'other-bank', name: 'Other Bank', status: 'ACTIVE', users: 2, created_at: isoAgo(7_200), demo: true },
];

const users: UserRecord[] = [
  { user_id: 'analyst-01', tenant_id: 'demo-bank', display_name: '分析人员 01', roles: ['ANALYST', 'MEMORY_USER'], status: 'ACTIVE', last_active_at: isoAgo(1), delegated_identity: 'delegate-analyst-demo', demo: true },
  { user_id: 'analyst-02', tenant_id: 'demo-bank', display_name: '分析人员 02', roles: ['ANALYST'], status: 'ACTIVE', last_active_at: isoAgo(27), delegated_identity: 'delegate-analyst-demo', demo: true },
  { user_id: 'compliance-01', tenant_id: 'demo-bank', display_name: '合规复核员', roles: ['COMPLIANCE', 'APPROVER'], status: 'ACTIVE', last_active_at: isoAgo(4), delegated_identity: 'delegate-approver-demo', demo: true },
  { user_id: 'auditor-01', tenant_id: 'demo-bank', display_name: '审计观察员', roles: ['AUDITOR'], status: 'ACTIVE', last_active_at: isoAgo(46), delegated_identity: 'delegate-auditor-demo', demo: true },
  { user_id: 'platform-admin', tenant_id: 'demo-bank', display_name: '平台管理员', roles: ['PLATFORM_ADMIN'], status: 'ACTIVE', last_active_at: isoAgo(12), delegated_identity: 'delegate-admin-demo', demo: true },
  { user_id: 'analyst-02-other', tenant_id: 'other-bank', display_name: 'Other Bank 分析师', roles: ['ANALYST'], status: 'ACTIVE', last_active_at: isoAgo(3), delegated_identity: 'delegate-other-demo', demo: true },
];

const roles: RoleRecord[] = [
  { role_id: 'role-analyst', name: 'ANALYST', description: '可发起 Run、读取授权金融资源与 Skill。', permissions: ['agent:execute', 'financial:transactions:read', 'financial:case:create', 'memory:read', 'memory:write'], member_count: 3, demo: true },
  { role_id: 'role-compliance', name: 'COMPLIANCE', description: '可复核案件，但不能审批自己发起的动作。', permissions: ['financial:case:read', 'financial:case:approve', 'approval:approve', 'runs:read:tenant'], member_count: 1, demo: true },
  { role_id: 'role-approver', name: 'APPROVER', description: '高风险工具独立审批角色。', permissions: ['approval:approve', 'financial:case:approve', 'financial:audit:read'], member_count: 1, demo: true },
  { role_id: 'role-auditor', name: 'AUDITOR', description: '只读审计与运行记录。', permissions: ['financial:audit:read', 'runs:read:tenant', 'memory:read'], member_count: 1, demo: true },
  { role_id: 'role-platform', name: 'PLATFORM_ADMIN', description: '管理平台配置，不直接作出金融决策。', permissions: ['platform:manage', 'runs:manage:tenant', 'skills:manage'], member_count: 1, demo: true },
];

const abacPolicies: AbacPolicy[] = [
  { policy_id: 'abac-demo-001', name: '同租户资源访问', effect: 'ALLOWED', conditions: 'resource.tenant == principal.tenant', status: 'ACTIVE', demo: true },
  { policy_id: 'abac-demo-002', name: '高风险动作双人复核', effect: 'DENIED', conditions: 'risk == high AND approver == requester', status: 'ACTIVE', demo: true },
  { policy_id: 'abac-demo-003', name: '组织级记忆只读', effect: 'ALLOWED', conditions: 'memory.scope == organization AND action == read', status: 'ACTIVE', demo: true },
  { policy_id: 'abac-demo-004', name: '跨租户访问拦截', effect: 'DENIED', conditions: 'resource.tenant != principal.tenant', status: 'ACTIVE', demo: true },
];

const acl: AclEntry[] = [
  { resource: 'ACC-DEMO-001', subject: 'analyst-01', permission: 'read', effect: 'ALLOWED', source: 'FinancialResourceGrant', demo: true },
  { resource: 'CUS-DEMO-002', subject: 'compliance-01', permission: 'case:approve', effect: 'ALLOWED', source: 'FinancialResourceGrant', demo: true },
  { resource: 'ACC-OTHER-001', subject: 'analyst-01', permission: 'read', effect: 'DENIED', source: 'TenantBoundary', demo: true },
  { resource: 'memory:organization', subject: 'role:AUDITOR', permission: 'read', effect: 'ALLOWED', source: 'MemoryACL', demo: true },
  { resource: 'mcp:financial.create_review_case', subject: 'role:APPROVER', permission: 'invoke', effect: 'ALLOWED', source: 'ToolGateway', demo: true },
  { resource: 'mcp:mcp.filings.read', subject: 'role:AUDITOR', permission: 'invoke', effect: 'DENIED', source: 'ToolGateway', demo: true },
];

const delegatedIdentities: DelegatedIdentity[] = [
  { identity_id: 'delegate-analyst-demo', name: '分析任务代理', owner: 'analyst-01', tenant_id: 'demo-bank', allowed_tools: ['financial.list_transactions', 'financial.screen_transactions'], scopes: ['financial:transactions:read', 'financial:monitoring:run'], expires_at: isoAhead(240), demo: true },
  { identity_id: 'delegate-approver-demo', name: '审批代理', owner: 'compliance-01', tenant_id: 'demo-bank', allowed_tools: ['financial.create_review_case'], scopes: ['approval:approve', 'financial:case:approve'], expires_at: isoAhead(120), demo: true },
  { identity_id: 'delegate-auditor-demo', name: '只读审计代理', owner: 'auditor-01', tenant_id: 'demo-bank', allowed_tools: [], scopes: ['financial:audit:read'], expires_at: isoAhead(480), demo: true },
  { identity_id: 'delegate-other-demo', name: 'Other Bank 隔离代理', owner: 'analyst-02-other', tenant_id: 'other-bank', allowed_tools: ['financial.list_transactions'], scopes: ['financial:transactions:read'], expires_at: isoAhead(180), demo: true },
];

const approvals: ApprovalRecord[] = [
  { approval_id: 'apr-demo-001', run_id: 'run-demo-sla-010', requester: 'analyst-01', requested_by: 'analyst-01', approver: 'compliance-01', approver_id: 'compliance-01', decision: 'APPROVED', approval_status: 'APPROVED', tool_name: 'financial.create_review_case', operation_type: 'CASE_CREATE', target_resource_id: 'CUST-DEMO-001', risk_level: 'HIGH', reason: '已完成独立复核。', time: isoAgo(162), issued_at: isoAgo(162), expires_at: isoAhead(60), policy_version: 'v1', version: 1, demo: true },
  { approval_id: 'apr-demo-002', run_id: 'run-demo-approval-003', requester: 'analyst-01', requested_by: 'analyst-01', approver: 'compliance-01', approver_id: null, decision: 'PENDING', approval_status: 'PENDING', tool_name: 'financial.create_review_case', operation_type: 'CASE_CREATE', target_resource_id: 'ACC-DEMO-001', risk_level: 'HIGH', reason: '等待确认高风险导出参数。', time: isoAgo(7), issued_at: isoAgo(7), expires_at: isoAhead(120), policy_version: 'v1', version: 0, demo: true },
  { approval_id: 'apr-demo-003', run_id: 'run-demo-recover-004', requester: 'analyst-02', requested_by: 'analyst-02', approver: 'platform-admin', approver_id: 'platform-admin', decision: 'APPROVED', approval_status: 'APPROVED', tool_name: 'financial.list_transactions', operation_type: 'RUN_RECOVER', target_resource_id: 'ACC-DEMO-001', risk_level: 'MEDIUM', reason: '恢复后继续研究任务。', time: isoAgo(101), issued_at: isoAgo(101), expires_at: isoAhead(30), policy_version: 'v1', version: 1, demo: true },
  { approval_id: 'apr-demo-004', run_id: 'run-demo-failed-005', requester: 'analyst-01', requested_by: 'analyst-01', approver: 'compliance-01', approver_id: 'compliance-01', decision: 'REJECTED', approval_status: 'REJECTED', tool_name: 'financial.create_review_case', operation_type: 'CASE_CREATE', target_resource_id: 'CUST-DEMO-002', risk_level: 'HIGH', reason: '恢复预算与证据链不满足要求。', time: isoAgo(80), issued_at: isoAgo(80), expires_at: isoAgo(20), policy_version: 'v1', version: 1, demo: true },
];

const securityEvents: SecurityEvent[] = [
  { event_id: 'sec-demo-001', type: 'CROSS_TENANT_BLOCK', actor: 'analyst-01', tenant_id: 'demo-bank', target: 'ACC-OTHER-001', reason: '资源租户与主体租户不一致', time: isoAgo(12), decision: 'DENIED', demo: true },
  { event_id: 'sec-demo-002', type: 'SCOPE_DENIED', actor: 'auditor-01', tenant_id: 'demo-bank', target: 'mcp.filings.read', reason: '缺少 agent:execute scope', time: isoAgo(33), decision: 'DENIED', demo: true },
  { event_id: 'sec-demo-003', type: 'SELF_APPROVAL_BLOCK', actor: 'analyst-01', tenant_id: 'demo-bank', target: 'run-demo-approval-003', reason: '审批人不能是任务发起人', time: isoAgo(8), decision: 'DENIED', demo: true },
  { event_id: 'sec-demo-004', type: 'RESOURCE_GRANT_OK', actor: 'analyst-01', tenant_id: 'demo-bank', target: 'ACC-DEMO-001', reason: '命中用户到账户的 Resource Grant', time: isoAgo(2), decision: 'ALLOWED', demo: true },
  { event_id: 'sec-demo-005', type: 'MCP_ALLOWLIST_OK', actor: 'analyst-02', tenant_id: 'other-bank', target: 'financial.list_transactions', reason: '工具在 Allowlist 中且 Scope 满足', time: isoAgo(4), decision: 'ALLOWED', demo: true },
  { event_id: 'sec-demo-006', type: 'CROSS_TENANT_BLOCK', actor: 'analyst-02-other', tenant_id: 'other-bank', target: 'CUS-DEMO-002', reason: '读取 demo-bank 客户资料被拦截', time: isoAgo(21), decision: 'DENIED', demo: true },
  { event_id: 'sec-demo-007', type: 'HIGH_RISK_APPROVAL_REQUIRED', actor: 'analyst-01', tenant_id: 'demo-bank', target: 'financial.create_review_case', reason: '高风险工具要求独立审批', time: isoAgo(7), decision: 'DENIED', demo: true },
  { event_id: 'sec-demo-008', type: 'DELEGATED_IDENTITY_EXPIRED', actor: 'compliance-01', tenant_id: 'demo-bank', target: 'delegate-approver-demo', reason: '代理身份已过期（演示记录）', time: isoAgo(310), decision: 'DENIED', demo: true },
];

const security: SecuritySnapshot = {
  tenants, users, roles, abacPolicies, acl, delegatedIdentities, approvals, events: securityEvents, demo: true,
};

const benchmarkExperiments: BenchmarkExperiment[] = [
  { experiment_id: 'exp-demo-001', batch: 'BATCH-DEMO-1119-A', model: 'Model-A-DEMO', model_version: 'a-demo-4.1', harness: 'Harness-Alpha-DEMO', harness_version: 'alpha-demo-1.6', prompt_version: 'PROMPT-DEMO-v4.2', dataset: 'finance-smoke-demo-120', runs: 24, task_success_rate: 82, task_completion_rate: 91, recovery_success_rate: 74, tool_call_accuracy: 88, unauthorized_access_rate: 0.4, prompt_injection_success_rate: 1.8, avg_duration_seconds: 486, p95_latency_ms: 1_240, token_cost_usd: 4.82, cost_per_success_usd: 5.88, status: 'COMPLETED', started_at: isoAgo(1_440), demo: true },
  { experiment_id: 'exp-demo-002', batch: 'BATCH-DEMO-1119-A', model: 'Model-A-DEMO', model_version: 'a-demo-4.1', harness: 'Harness-Beta-DEMO', harness_version: 'beta-demo-2.0', prompt_version: 'PROMPT-DEMO-v4.2', dataset: 'finance-smoke-demo-120', runs: 24, task_success_rate: 88, task_completion_rate: 94, recovery_success_rate: 91, tool_call_accuracy: 93, unauthorized_access_rate: 0.2, prompt_injection_success_rate: 0.8, avg_duration_seconds: 412, p95_latency_ms: 1_080, token_cost_usd: 5.16, cost_per_success_usd: 5.86, status: 'COMPLETED', started_at: isoAgo(1_390), demo: true },
  { experiment_id: 'exp-demo-003', batch: 'BATCH-DEMO-1119-A', model: 'Model-B-DEMO', model_version: 'b-demo-3.7', harness: 'Harness-Alpha-DEMO', harness_version: 'alpha-demo-1.6', prompt_version: 'PROMPT-DEMO-v4.2', dataset: 'finance-smoke-demo-120', runs: 24, task_success_rate: 86, task_completion_rate: 93, recovery_success_rate: 88, tool_call_accuracy: 91, unauthorized_access_rate: 0.3, prompt_injection_success_rate: 1.1, avg_duration_seconds: 448, p95_latency_ms: 1_160, token_cost_usd: 3.94, cost_per_success_usd: 4.58, status: 'COMPLETED', started_at: isoAgo(1_330), demo: true },
  { experiment_id: 'exp-demo-004', batch: 'BATCH-DEMO-1119-A', model: 'Model-B-DEMO', model_version: 'b-demo-3.7', harness: 'Harness-Beta-DEMO', harness_version: 'beta-demo-2.0', prompt_version: 'PROMPT-DEMO-v4.2', dataset: 'finance-smoke-demo-120', runs: 24, task_success_rate: 92, task_completion_rate: 96, recovery_success_rate: 94, tool_call_accuracy: 95, unauthorized_access_rate: 0.1, prompt_injection_success_rate: 0.5, avg_duration_seconds: 394, p95_latency_ms: 990, token_cost_usd: 4.28, cost_per_success_usd: 4.65, status: 'COMPLETED', started_at: isoAgo(1_270), demo: true },
  { experiment_id: 'exp-demo-005', batch: 'BATCH-DEMO-1119-B', model: 'Model-A-DEMO', model_version: 'a-demo-4.2', harness: 'Harness-Alpha-DEMO', harness_version: 'alpha-demo-1.7', prompt_version: 'PROMPT-DEMO-v5.0', dataset: 'finance-longrun-demo-60', runs: 12, task_success_rate: 79, task_completion_rate: 88, recovery_success_rate: 70, tool_call_accuracy: 86, unauthorized_access_rate: 0.6, prompt_injection_success_rate: 2.2, avg_duration_seconds: 2_840, p95_latency_ms: 4_620, token_cost_usd: 18.4, cost_per_success_usd: 23.29, status: 'COMPLETED', started_at: isoAgo(720), demo: true },
  { experiment_id: 'exp-demo-006', batch: 'BATCH-DEMO-1119-B', model: 'Model-A-DEMO', model_version: 'a-demo-4.2', harness: 'Harness-Beta-DEMO', harness_version: 'beta-demo-2.1', prompt_version: 'PROMPT-DEMO-v5.0', dataset: 'finance-longrun-demo-60', runs: 12, task_success_rate: 85, task_completion_rate: 92, recovery_success_rate: 89, tool_call_accuracy: 90, unauthorized_access_rate: 0.3, prompt_injection_success_rate: 1.0, avg_duration_seconds: 2_610, p95_latency_ms: 4_180, token_cost_usd: 19.2, cost_per_success_usd: 22.59, status: 'COMPLETED', started_at: isoAgo(680), demo: true },
  { experiment_id: 'exp-demo-007', batch: 'BATCH-DEMO-1119-B', model: 'Model-B-DEMO', model_version: 'b-demo-3.8', harness: 'Harness-Alpha-DEMO', harness_version: 'alpha-demo-1.7', prompt_version: 'PROMPT-DEMO-v5.0', dataset: 'finance-longrun-demo-60', runs: 12, task_success_rate: 83, task_completion_rate: 90, recovery_success_rate: 85, tool_call_accuracy: 89, unauthorized_access_rate: 0.4, prompt_injection_success_rate: 1.3, avg_duration_seconds: 2_720, p95_latency_ms: 4_340, token_cost_usd: 14.6, cost_per_success_usd: 17.59, status: 'COMPLETED', started_at: isoAgo(640), demo: true },
  { experiment_id: 'exp-demo-008', batch: 'BATCH-DEMO-1119-B', model: 'Model-B-DEMO', model_version: 'b-demo-3.8', harness: 'Harness-Beta-DEMO', harness_version: 'beta-demo-2.1', prompt_version: 'PROMPT-DEMO-v5.0', dataset: 'finance-longrun-demo-60', runs: 12, task_success_rate: 90, task_completion_rate: 95, recovery_success_rate: 93, tool_call_accuracy: 94, unauthorized_access_rate: 0.2, prompt_injection_success_rate: 0.6, avg_duration_seconds: 2_480, p95_latency_ms: 3_920, token_cost_usd: 15.1, cost_per_success_usd: 16.78, status: 'RUNNING', started_at: isoAgo(610), demo: true },
  { experiment_id: 'exp-model-2026-deepseek-chat-alpha', batch: 'BATCH-DEMO-2026-MODEL-COMPARE', model: 'deepseek-chat', model_version: 'v3', harness: 'Harness-Alpha', harness_version: 'alpha-demo-2.0', prompt_version: 'PROMPT-DEMO-v5.2', dataset: 'finance-smoke-demo-120', runs: 24, task_success_rate: 86, task_completion_rate: 94, recovery_success_rate: 76, tool_call_accuracy: 90, unauthorized_access_rate: 0.3, prompt_injection_success_rate: 1.2, avg_duration_seconds: 430, p95_latency_ms: 1_180, token_cost_usd: 4.20, cost_per_success_usd: 4.88, status: 'COMPLETED', started_at: isoAgo(580), demo: true },
  { experiment_id: 'exp-model-2026-deepseek-chat-beta', batch: 'BATCH-DEMO-2026-MODEL-COMPARE', model: 'deepseek-chat', model_version: 'v3', harness: 'Harness-Beta', harness_version: 'beta-demo-2.2', prompt_version: 'PROMPT-DEMO-v5.2', dataset: 'finance-smoke-demo-120', runs: 24, task_success_rate: 91, task_completion_rate: 96, recovery_success_rate: 82, tool_call_accuracy: 93, unauthorized_access_rate: 0.2, prompt_injection_success_rate: 0.8, avg_duration_seconds: 390, p95_latency_ms: 1_020, token_cost_usd: 4.50, cost_per_success_usd: 4.95, status: 'COMPLETED', started_at: isoAgo(570), demo: true },
  { experiment_id: 'exp-model-2026-deepseek-reasoner-alpha', batch: 'BATCH-DEMO-2026-MODEL-COMPARE', model: 'deepseek-reasoner', model_version: 'r1', harness: 'Harness-Alpha', harness_version: 'alpha-demo-2.0', prompt_version: 'PROMPT-DEMO-v5.2', dataset: 'finance-smoke-demo-120', runs: 24, task_success_rate: 90, task_completion_rate: 95, recovery_success_rate: 88, tool_call_accuracy: 82, unauthorized_access_rate: 0.2, prompt_injection_success_rate: 0.9, avg_duration_seconds: 610, p95_latency_ms: 1_640, token_cost_usd: 7.80, cost_per_success_usd: 8.67, status: 'COMPLETED', started_at: isoAgo(560), demo: true },
  { experiment_id: 'exp-model-2026-deepseek-reasoner-beta', batch: 'BATCH-DEMO-2026-MODEL-COMPARE', model: 'deepseek-reasoner', model_version: 'r1', harness: 'Harness-Beta', harness_version: 'beta-demo-2.2', prompt_version: 'PROMPT-DEMO-v5.2', dataset: 'finance-smoke-demo-120', runs: 24, task_success_rate: 94, task_completion_rate: 97, recovery_success_rate: 92, tool_call_accuracy: 86, unauthorized_access_rate: 0.1, prompt_injection_success_rate: 0.6, avg_duration_seconds: 540, p95_latency_ms: 1_430, token_cost_usd: 8.20, cost_per_success_usd: 8.72, status: 'COMPLETED', started_at: isoAgo(550), demo: true },
  { experiment_id: 'exp-model-2026-qwen3-235b-alpha', batch: 'BATCH-DEMO-2026-MODEL-COMPARE', model: 'qwen3-235b-a22b', model_version: 'instruct', harness: 'Harness-Alpha', harness_version: 'alpha-demo-2.0', prompt_version: 'PROMPT-DEMO-v5.2', dataset: 'finance-smoke-demo-120', runs: 24, task_success_rate: 88, task_completion_rate: 95, recovery_success_rate: 84, tool_call_accuracy: 91, unauthorized_access_rate: 0.2, prompt_injection_success_rate: 0.8, avg_duration_seconds: 470, p95_latency_ms: 1_260, token_cost_usd: 5.10, cost_per_success_usd: 5.80, status: 'COMPLETED', started_at: isoAgo(540), demo: true },
  { experiment_id: 'exp-model-2026-qwen3-235b-beta', batch: 'BATCH-DEMO-2026-MODEL-COMPARE', model: 'qwen3-235b-a22b', model_version: 'instruct', harness: 'Harness-Beta', harness_version: 'beta-demo-2.2', prompt_version: 'PROMPT-DEMO-v5.2', dataset: 'finance-smoke-demo-120', runs: 24, task_success_rate: 92, task_completion_rate: 97, recovery_success_rate: 89, tool_call_accuracy: 94, unauthorized_access_rate: 0.2, prompt_injection_success_rate: 0.6, avg_duration_seconds: 420, p95_latency_ms: 1_100, token_cost_usd: 5.40, cost_per_success_usd: 5.87, status: 'COMPLETED', started_at: isoAgo(530), demo: true },
  { experiment_id: 'exp-model-2026-gpt-4.1-mini-alpha', batch: 'BATCH-DEMO-2026-MODEL-COMPARE', model: 'gpt-4.1-mini', model_version: '2026-04', harness: 'Harness-Alpha', harness_version: 'alpha-demo-2.0', prompt_version: 'PROMPT-DEMO-v5.2', dataset: 'finance-smoke-demo-120', runs: 24, task_success_rate: 91, task_completion_rate: 96, recovery_success_rate: 86, tool_call_accuracy: 94, unauthorized_access_rate: 0.1, prompt_injection_success_rate: 0.5, avg_duration_seconds: 400, p95_latency_ms: 1_050, token_cost_usd: 5.60, cost_per_success_usd: 6.15, status: 'COMPLETED', started_at: isoAgo(520), demo: true },
  { experiment_id: 'exp-model-2026-gpt-4.1-mini-beta', batch: 'BATCH-DEMO-2026-MODEL-COMPARE', model: 'gpt-4.1-mini', model_version: '2026-04', harness: 'Harness-Beta', harness_version: 'beta-demo-2.2', prompt_version: 'PROMPT-DEMO-v5.2', dataset: 'finance-smoke-demo-120', runs: 24, task_success_rate: 95, task_completion_rate: 98, recovery_success_rate: 91, tool_call_accuracy: 96, unauthorized_access_rate: 0.1, prompt_injection_success_rate: 0.4, avg_duration_seconds: 360, p95_latency_ms: 930, token_cost_usd: 5.90, cost_per_success_usd: 6.21, status: 'COMPLETED', started_at: isoAgo(510), demo: true },
  { experiment_id: 'exp-model-2026-claude-sonnet-4-alpha', batch: 'BATCH-DEMO-2026-MODEL-COMPARE', model: 'claude-sonnet-4', model_version: '2026-05', harness: 'Harness-Alpha', harness_version: 'alpha-demo-2.0', prompt_version: 'PROMPT-DEMO-v5.2', dataset: 'finance-smoke-demo-120', runs: 24, task_success_rate: 93, task_completion_rate: 97, recovery_success_rate: 90, tool_call_accuracy: 95, unauthorized_access_rate: 0.1, prompt_injection_success_rate: 0.4, avg_duration_seconds: 520, p95_latency_ms: 1_380, token_cost_usd: 9.40, cost_per_success_usd: 10.11, status: 'COMPLETED', started_at: isoAgo(500), demo: true },
  { experiment_id: 'exp-model-2026-claude-sonnet-4-beta', batch: 'BATCH-DEMO-2026-MODEL-COMPARE', model: 'claude-sonnet-4', model_version: '2026-05', harness: 'Harness-Beta', harness_version: 'beta-demo-2.2', prompt_version: 'PROMPT-DEMO-v5.2', dataset: 'finance-smoke-demo-120', runs: 24, task_success_rate: 96, task_completion_rate: 98, recovery_success_rate: 94, tool_call_accuracy: 97, unauthorized_access_rate: 0.1, prompt_injection_success_rate: 0.3, avg_duration_seconds: 460, p95_latency_ms: 1_210, token_cost_usd: 9.80, cost_per_success_usd: 10.21, status: 'COMPLETED', started_at: isoAgo(490), demo: true },
  { experiment_id: 'exp-model-2026-qwen3-14b-alpha', batch: 'BATCH-DEMO-2026-MODEL-COMPARE', model: 'qwen3-14b', model_version: 'instruct', harness: 'Harness-Alpha', harness_version: 'alpha-demo-2.0', prompt_version: 'PROMPT-DEMO-v5.2', dataset: 'finance-smoke-demo-120', runs: 24, task_success_rate: 74, task_completion_rate: 86, recovery_success_rate: 64, tool_call_accuracy: 78, unauthorized_access_rate: 0.8, prompt_injection_success_rate: 2.4, avg_duration_seconds: 280, p95_latency_ms: 760, token_cost_usd: 1.20, cost_per_success_usd: 1.62, status: 'COMPLETED', started_at: isoAgo(480), demo: true },
  { experiment_id: 'exp-model-2026-qwen3-14b-beta', batch: 'BATCH-DEMO-2026-MODEL-COMPARE', model: 'qwen3-14b', model_version: 'instruct', harness: 'Harness-Beta', harness_version: 'beta-demo-2.2', prompt_version: 'PROMPT-DEMO-v5.2', dataset: 'finance-smoke-demo-120', runs: 24, task_success_rate: 80, task_completion_rate: 90, recovery_success_rate: 72, tool_call_accuracy: 83, unauthorized_access_rate: 0.5, prompt_injection_success_rate: 1.6, avg_duration_seconds: 250, p95_latency_ms: 680, token_cost_usd: 1.30, cost_per_success_usd: 1.63, status: 'COMPLETED', started_at: isoAgo(470), demo: true },
];

const ablations: AblationRecord[] = [
  { ablation_id: 'abl-demo-001', name: '完整系统', removed_component: '—', success_rate: 92, delta_pp: 0, recovery_success_rate: 94, unauthorized_access_rate: 0.1, note: 'DEMO 基线：包含记忆、检查点、工具网关与护栏。', demo: true },
  { ablation_id: 'abl-demo-002', name: '移除长期记忆', removed_component: 'Memory', success_rate: 84, delta_pp: -8, recovery_success_rate: 90, unauthorized_access_rate: 0.2, note: '上下文丢失导致多步长任务事实不一致。', demo: true },
  { ablation_id: 'abl-demo-003', name: '移除检查点恢复', removed_component: 'Checkpoint / Recovery', success_rate: 71, delta_pp: -21, recovery_success_rate: 38, unauthorized_access_rate: 0.3, note: 'Worker 故障后需从头执行。', demo: true },
  { ablation_id: 'abl-demo-004', name: '移除工具网关校验', removed_component: 'Tool Gateway', success_rate: 88, delta_pp: -4, recovery_success_rate: 91, unauthorized_access_rate: 4.7, note: '未授权工具调用显著增加。', demo: true },
  { ablation_id: 'abl-demo-005', name: '移除 Prompt 注入护栏', removed_component: 'Injection Guard', success_rate: 86, delta_pp: -6, recovery_success_rate: 89, unauthorized_access_rate: 0.9, note: '注入成功率仅为合成演示指标。', demo: true },
];

const conversations: ChatConversation[] = [
  { conversation_id: 'conv-demo-001', title: '信贷政策研究', session_id: 'session-chat-demo-001', created_at: isoAgo(180), updated_at: isoAgo(12) },
  { conversation_id: 'conv-demo-002', title: '交易监测复核提示', session_id: 'session-chat-demo-002', created_at: isoAgo(60), updated_at: isoAgo(30) },
];

const chatMessagesByConversation: Record<string, ChatMessage[]> = {
  'conv-demo-001': [
    { message_id: 'msg-demo-001', conversation_id: 'conv-demo-001', role: 'user', content: '总结一下普惠小微贷款的利率约束。', created_at: isoAgo(20) },
    { message_id: 'msg-demo-002', conversation_id: 'conv-demo-001', role: 'assistant', content: '演示答复：普惠小微贷款需关注 LPR 基准与综合融资成本明示，示例数据不构成政策结论。', reasoning: '演示推理：先定位知识条目，再给出适用边界。', created_at: isoAgo(19) },
  ],
  'conv-demo-002': [],
};

const knowledgeDocuments: KnowledgeDocument[] = [
  { document_id: 'doc-demo-001', title: '信贷基础概念', source: '信贷.md', content_type: 'markdown', char_count: 700, chunk_count: 2, status: 'READY', created_at: isoAgo(600) },
  { document_id: 'doc-demo-002', title: '征信修复与转型金融', source: '信贷补充.md', content_type: 'markdown', char_count: 580, chunk_count: 2, status: 'READY', created_at: isoAgo(580) },
];

const knowledgeChunksByDocument: Record<string, KnowledgeChunkDetail[]> = {
  'doc-demo-001': [
    { chunk_id: 'chunk-demo-001-1', chunk_index: 0, heading: '信贷业务分类', content: '个人贷款与公司贷款、担保方式与期限结构。', char_count: 380 },
    { chunk_id: 'chunk-demo-001-2', chunk_index: 1, heading: '风控体系', content: '五级分类、评分卡与内部评级的应用边界。', char_count: 320 },
  ],
  'doc-demo-002': [
    { chunk_id: 'chunk-demo-002-1', chunk_index: 0, heading: '征信修复', content: '一次性信用修复的适用条件与风控含义。', char_count: 300 },
    { chunk_id: 'chunk-demo-002-2', chunk_index: 1, heading: '转型金融', content: '转型金融标准扩容与碳减排绩效挂钩机制。', char_count: 280 },
  ],
};

function createInitialMockState(): MockState {
  return {
    runs,
    eventsByRun,
    telemetryByRun,
    cases,
    auditLogs,
    mcpServers,
    mcpTools,
    managedSkills,
    memories,
    checkpoints,
    failures,
    security,
    benchmarks: benchmarkExperiments,
    ablations,
    conversations,
    chatMessagesByConversation,
    knowledgeDocuments,
    knowledgeChunksByDocument,
  };
}

function saveMockState(next: MockState) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); }
  catch { /* Browser storage may be unavailable. */ }
}

function loadMockState(): MockState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<MockState>;
      if (Array.isArray(parsed.runs) && parsed.telemetryByRun && parsed.security && Array.isArray(parsed.benchmarks)) {
        const initial = createInitialMockState();
        return {
          ...initial,
          ...parsed,
          conversations: Array.isArray(parsed.conversations) ? parsed.conversations : initial.conversations,
          chatMessagesByConversation: parsed.chatMessagesByConversation || initial.chatMessagesByConversation,
          knowledgeDocuments: Array.isArray(parsed.knowledgeDocuments) ? parsed.knowledgeDocuments : initial.knowledgeDocuments,
          knowledgeChunksByDocument: parsed.knowledgeChunksByDocument || initial.knowledgeChunksByDocument,
          security: { ...initial.security, ...parsed.security, approvals: Array.isArray(parsed.security?.approvals) ? parsed.security!.approvals : initial.security.approvals },
        } as MockState;
      }
    }
  } catch { /* Reset invalid local demo data. */ }
  const initial = createInitialMockState();
  saveMockState(initial);
  return initial;
}

let mockState: MockState | null = null;

export function getMockState(): MockState {
  if (!mockState) mockState = loadMockState();
  return mockState;
}

export function commitMockState() {
  if (mockState) saveMockState(mockState);
}

export function resetMockState(): MockState {
  mockState = createInitialMockState();
  saveMockState(mockState);
  return mockState;
}

export function addMockAudit(action: string, resourceType: string, resourceId: string, userId: string, runId?: string) {
  const state = getMockState();
  state.auditLogs.unshift({
    audit_id: newId('AUD'), actor_user_id: userId, run_id: runId || null,
    action, resource_type: resourceType, resource_id: resourceId,
    details: { mode: 'DEMO' }, created_at: new Date().toISOString(),
  });
  commitMockState();
}

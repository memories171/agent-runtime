export type DataMode = 'mock' | 'api';

export type RunStatus =
  | 'CREATED' | 'QUEUED' | 'RUNNING' | 'PAUSED' | 'WAITING_INPUT'
  | 'WAITING_APPROVAL' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED' | 'TIMED_OUT';

export type PauseState = 'NONE' | 'REQUESTED' | 'PAUSING' | 'PAUSED' | 'RESUME_REQUESTED' | 'RESUMING';

export type MemoryType = 'WORKING' | 'EPISODIC' | 'SEMANTIC' | 'PROCEDURAL';
export type MemoryScope = 'user' | 'team' | 'organization';
export type Decision = 'ALLOWED' | 'DENIED';

export interface Identity {
  tenantId: string;
  userId: string;
  scopes: string;
  displayName: string;
}

export interface RunRecord {
  run_id: string;
  tenant_id: string;
  user_id: string;
  session_id: string;
  agent_id: string;
  prompt: string;
  status: RunStatus;
  pause_state?: PauseState;
  version?: number;
  result?: Record<string, unknown> | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
  model_name?: string;
  harness_version?: string;
  duration_seconds?: number;
  heartbeat_at?: string | null;
  deadline_at?: string | null;
  progress_pct?: number;
  current_step?: string;
  token_input?: number;
  token_output?: number;
  estimated_cost_usd?: number;
  latest_event?: string;
  demo?: boolean;
}

export interface RunEvent {
  event_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ToolCallRecord {
  call_id: string;
  tool: string;
  server: string;
  status: 'CREATED' | 'DISPATCHED' | 'CONFIRMED' | 'FAILED' | 'UNKNOWN' | 'RECONCILIATION_REQUIRED' | 'RESTORED_REUSED' | 'SUCCESS' | 'PENDING';
  started_at: string;
  duration_ms: number;
  risk_level: string;
  idempotency_key: string;
}

export interface LlmCallRecord {
  call_id: string;
  model: string;
  purpose: string;
  prompt_tokens: number;
  completion_tokens: number;
  latency_ms: number;
  started_at: string;
  status: 'SUCCESS' | 'FAILED';
}

export interface RunTelemetry {
  run_id: string;
  model: string;
  harness_version: string;
  prompt_version: string;
  duration_seconds: number;
  heartbeat_at: string | null;
  deadline_at: string | null;
  current_step: string;
  progress_pct: number;
  token_input: number;
  token_output: number;
  estimated_cost_usd: number;
  tool_calls: ToolCallRecord[];
  llm_calls: LlmCallRecord[];
  checkpoint_ids: string[];
  recovery_count: number;
  demo?: boolean;
}

export interface ReviewCase {
  case_id: string;
  case_type: string;
  subject_id: string;
  rationale: string;
  evidence_transaction_ids: string[];
  status: string;
  created_by: string;
  created_at: string | null;
  reviewer?: string | null;
  review_decision?: string | null;
  review_reason?: string | null;
  reviewed_at?: string | null;
  review_history?: Array<{ actor: string; action: string; reason: string; status: string; at: string }>;
}

export interface AuditLog {
  audit_id: string;
  actor_user_id: string;
  run_id?: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  details: Record<string, unknown>;
  created_at: string | null;
}

export interface McpServerRecord {
  server_id: string;
  name: string;
  transport: string;
  status: 'HEALTHY' | 'DEGRADED' | 'UNHEALTHY';
  protocol_version: string;
  tools_count: number;
  resources_count: number;
  prompts_count: number;
  last_connected_at: string;
  error_rate_pct: number;
  latency_ms: number;
  description: string;
  demo?: boolean;
}

export interface McpToolRecord {
  name: string;
  display_name?: string;
  server_id: string;
  description: string;
  required_scopes: string[];
  risk_level: string;
  idempotent: boolean;
  demo?: boolean;
}

export interface ManagedSkill {
  skill_id: string;
  name: string;
  display_name?: string;
  description: string;
  version: string;
  release_status: 'STABLE' | 'BETA' | 'DRAFT';
  tools: string[];
  required_scopes: string[];
  updated_at: string;
  enabled: boolean;
  demo?: boolean;
}

export interface MemoryRecord {
  memory_id: string;
  type: MemoryType;
  scope: MemoryScope;
  title: string;
  content: string;
  source_run_id: string | null;
  owner: string;
  tenant_id?: string;
  confidence: number;
  created_at: string;
  updated_at: string;
  expires_at: string | null;
  acl: string[];
  archived: boolean;
  demo?: boolean;
}

export type CheckpointPersistenceStatus =
  | 'CREATED' | 'SERIALIZING' | 'UPLOADING' | 'VERIFYING' | 'PERSISTED'
  | 'AVAILABLE_FOR_RECOVERY' | 'CHECKPOINT_FAILED' | 'RETRY' | 'ORPHANED'
  | 'SAVED' | 'RESTORED' | 'CORRUPTED';

export interface CheckpointRecord {
  checkpoint_id: string;
  run_id: string;
  tenant_id: string;
  created_at: string;
  step: number | string;
  checkpoint_sequence?: number;
  summary: string;
  size_kb: number;
  blob_size_bytes?: number;
  state_digest?: string;
  state_blob_ref?: string;
  schema_version?: string;
  persistence_status?: CheckpointPersistenceStatus;
  status: string;
  worker_id: string;
  idempotency_key: string;
  duplicate_protection: boolean;
  demo?: boolean;
}

export interface CheckpointAgentState {
  current_plan: Array<Record<string, unknown>>;
  completed_steps: Array<Record<string, unknown>>;
  pending_steps: Array<Record<string, unknown>>;
  current_step_state: Record<string, unknown>;
  context_summary_ref: string | null;
  message_history_ref: string | null;
  tool_intents: Array<Record<string, unknown>>;
  approval_bindings: Array<Record<string, unknown>>;
  memory_references: Array<Record<string, unknown>>;
  token_usage: Record<string, number>;
  cost_usage: Record<string, number>;
  execution_budget: Record<string, unknown>;
  current_step_id?: string;
  last_completed_step?: string | null;
  last_application_event_sequence?: number;
  model_version?: string | null;
  harness_version?: string | null;
}

export interface CheckpointDetail extends CheckpointRecord {
  blob_sha256?: string;
  compression_algorithm?: string;
  encryption_algorithm?: string;
  encryption_key_id?: string;
  envelope: CheckpointAgentState;
}

export interface WorkerFailure {
  failure_id: string;
  run_id: string;
  tenant_id: string;
  worker_id: string;
  occurred_at: string;
  reason: string;
  recovery_status: 'RECOVERED' | 'RECOVERING' | 'FAILED';
  recovery_count: number;
  retry_count: number;
  recovery_attempt: number;
  activity_retry_count: number;
  recovery_started_at: string | null;
  recovery_completed_at: string | null;
  fencing_token: number;
  recovered_from_checkpoint_id: string | null;
  recovery_position: string;
  idempotency_key: string;
  duplicate_protection: boolean;
  demo?: boolean;
}

export interface ToolInvocationRecord {
  invocation_id: string;
  run_id: string;
  tenant_id: string;
  tool_call_id: string | null;
  tool_name: string;
  idempotency_key: string;
  canonical_arguments_hash: string;
  status: 'CREATED' | 'DISPATCHED' | 'CONFIRMED' | 'FAILED' | 'UNKNOWN' | 'RECONCILIATION_REQUIRED';
  attempt_count: number;
  external_reference: string | null;
  result_reference: string | null;
  reconciliation_status: 'NONE' | 'PENDING' | 'RESOLVED';
  reconciliation_note: string | null;
  fencing_token: number;
  lease_owner: string | null;
  lease_expires_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  last_error: string | null;
  created_at: string;
  demo?: boolean;
}

export interface RecoveryStage {
  key: string;
  label: string;
  time: string;
  status: 'finish' | 'process' | 'wait' | 'error';
  description: string;
  recovery_attempt?: number;
  activity_retry_count?: number;
  fencing_token?: number;
}

export interface TenantRecord {
  tenant_id: string;
  name: string;
  status: 'ACTIVE' | 'SUSPENDED';
  users: number;
  created_at: string;
  demo?: boolean;
}

export interface UserRecord {
  user_id: string;
  tenant_id: string;
  display_name: string;
  roles: string[];
  status: 'ACTIVE' | 'DISABLED';
  last_active_at: string;
  delegated_identity: string;
  demo?: boolean;
}

export interface RoleRecord {
  role_id: string;
  name: string;
  description: string;
  permissions: string[];
  member_count: number;
  demo?: boolean;
}

export interface AbacPolicy {
  policy_id: string;
  name: string;
  effect: Decision;
  conditions: string;
  status: 'ACTIVE' | 'DRAFT';
  demo?: boolean;
}

export interface AclEntry {
  resource: string;
  subject: string;
  permission: string;
  effect: Decision;
  source: string;
  demo?: boolean;
}

export interface DelegatedIdentity {
  identity_id: string;
  name: string;
  owner: string;
  tenant_id: string;
  allowed_tools: string[];
  scopes: string[];
  expires_at: string;
  demo?: boolean;
}

export type ApprovalStatus = 'PENDING' | 'APPROVED' | 'CONSUMED' | 'REJECTED' | 'EXPIRED' | 'REVOKED' | 'INVALIDATED';

export interface ApprovalRecord {
  approval_id: string;
  run_id: string;
  requested_by?: string;
  approver_id?: string | null;
  requester: string;
  approver: string;
  decision: 'APPROVED' | 'REJECTED' | 'PENDING';
  reason: string;
  time: string;
  tenant_id?: string;
  tool_call_id?: string;
  tool_name?: string;
  target_resource_id?: string;
  operation_type?: string;
  canonical_arguments?: string;
  canonical_arguments_hash?: string;
  action_hash?: string;
  policy_version?: string;
  risk_level?: string;
  nonce?: string;
  issued_at?: string;
  expires_at?: string;
  approval_status?: ApprovalStatus;
  consumed_at?: string | null;
  version?: number;
  demo?: boolean;
}

export interface ApprovalActionEnvelope {
  approval_id: string;
  tenant_id: string;
  run_id: string;
  tool_call_id: string;
  tool_name: string;
  target_resource_id: string;
  operation_type: string;
  canonical_arguments: string;
  canonical_arguments_hash: string;
  action_hash: string;
  requested_by: string;
  policy_version: string;
  risk_level: string;
  nonce: string;
  issued_at: string | null;
  expires_at: string;
  approval_status: ApprovalStatus;
  signature_reference?: string | null;
}

export interface SecurityEvent {
  event_id: string;
  type: string;
  actor: string;
  tenant_id: string;
  target: string;
  reason: string;
  time: string;
  decision: Decision;
  demo?: boolean;
}

export interface SecuritySnapshot {
  tenants: TenantRecord[];
  users: UserRecord[];
  roles: RoleRecord[];
  abacPolicies: AbacPolicy[];
  acl: AclEntry[];
  delegatedIdentities: DelegatedIdentity[];
  approvals: ApprovalRecord[];
  events: SecurityEvent[];
  demo?: boolean;
}

export interface BenchmarkExperiment {
  experiment_id: string;
  batch: string;
  model: string;
  model_version: string;
  harness: string;
  harness_version: string;
  prompt_version: string;
  dataset: string;
  runs: number;
  task_success_rate: number;
  task_completion_rate: number;
  recovery_success_rate: number;
  tool_call_accuracy: number;
  unauthorized_access_rate: number;
  prompt_injection_success_rate: number;
  avg_duration_seconds: number;
  p95_latency_ms: number;
  token_cost_usd: number;
  cost_per_success_usd: number;
  status: 'COMPLETED' | 'RUNNING';
  started_at: string;
  demo?: boolean;
}

export interface AblationRecord {
  ablation_id: string;
  name: string;
  removed_component: string;
  success_rate: number;
  delta_pp: number;
  recovery_success_rate: number;
  unauthorized_access_rate: number;
  note: string;
  demo?: boolean;
}

export interface ContextProvenance {
  source_type: string;
  source_id: string;
  source_event_id: string | null;
  created_at: string | null;
  trust_level: 'system' | 'verified' | 'derived' | 'untrusted';
  sensitivity: 'public' | 'internal' | 'confidential' | 'restricted';
  relevance_score: number;
  tenant_id?: string | null;
}

export interface ContextItem {
  value: unknown;
  provenance: ContextProvenance;
  estimated_tokens: number;
}

export interface ContextBudget {
  model_context_limit: number;
  reserved_output_tokens: number;
  system_instruction_tokens: number;
  context_tokens: number;
  available_tokens: number;
  estimated_tokens: number;
  safety_margin_tokens: number;
  tokenizer_name: string;
  tokens_are_estimate: boolean;
  provider_prompt_tokens: number | null;
  provider_completion_tokens: number | null;
  token_delta: number | null;
  budget_config: Record<string, unknown>;
}

export interface ContextPack {
  schema_version: string;
  metadata: {
    context_pack_id: string;
    run_id: string;
    tenant_id: string;
    agent_id: string;
    model_name: string | null;
    model_version: string | null;
    harness_version: string | null;
    created_at: string;
    event_cursor: number;
    compression_count: number;
    compression_ratio: number;
    degraded: boolean;
    degradation_reason: string | null;
    graph_id?: string | null;
    graph_version?: number | null;
    node_id?: string | null;
    parent_node_ids?: string[];
    node_type?: string | null;
    context_policy?: string | null;
  };
  goal: {
    original_user_goal: ContextItem;
    task_objectives: ContextItem[];
    acceptance_criteria: ContextItem[];
    immutable_constraints: ContextItem[];
    active_policy_references: ContextItem[];
  };
  execution: {
    current_plan: ContextItem[];
    completed_steps: ContextItem[];
    pending_steps: ContextItem[];
    current_step: ContextItem | null;
    current_step_status: string;
    active_artifact_references: ContextItem[];
    active_tool_invocations: ContextItem[];
  };
  historical: {
    rolling_summary: ContextItem | null;
    summary_id: string | null;
    summary_version: number;
    recent_events: ContextItem[];
    historical_decisions: ContextItem[];
    important_tool_results: ContextItem[];
    unresolved_questions: ContextItem[];
  };
  memory: {
    retrieved_memory_references: ContextItem[];
    memory_source_run_ids: string[];
    memory_confidence: number;
    memory_scope: string;
  };
  budget: ContextBudget;
  demo?: boolean;
}

export interface ContextPackSummary {
  context_pack_id: string;
  schema_version: string;
  model_name: string | null;
  event_cursor: number;
  compression_count: number;
  compression_ratio: number;
  degraded: boolean;
  degradation_reason: string | null;
  created_at: string | null;
  demo?: boolean;
}

export interface ContextSummaryRecord {
  summary_id: string;
  summary_version: number;
  source_event_start: number;
  source_event_end: number;
  source_event_ids: string[];
  summary_content: string;
  content_hash: string;
  model_version: string | null;
  key_facts: Array<{ fact: string; source_event_id: string }>;
  created_at: string | null;
  demo?: boolean;
}

export interface ContextMetrics {
  context_builds: number;
  context_calls: number;
  compressions: number;
  degraded_builds: number;
  provider_reported_calls: number;
  token_delta_samples: number[];
  avg_token_delta: number | null;
  demo?: boolean;
}

export type GraphStatus = 'PLANNING' | 'READY' | 'RUNNING' | 'WAITING' | 'PAUSED' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED' | 'TIMED_OUT';
export type NodeStatus = 'PENDING' | 'READY' | 'RUNNING' | 'WAITING_APPROVAL' | 'WAITING_INPUT' | 'RETRY_WAIT' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | 'SKIPPED' | 'TIMED_OUT';

export interface TaskGraphNode {
  node_id: string;
  row_id?: string;
  graph_id?: string;
  node_type: string;
  name: string;
  description: string;
  dependencies: string[];
  status: NodeStatus;
  execution_order: number;
  attempt_count: number;
  last_error: string | null;
  result: Record<string, unknown> | null;
  token_usage: { input: number; output: number };
  duration_ms: number;
  artifact_refs: Array<Record<string, unknown>>;
  context_pack_id: string | null;
  source_event_ids: string[];
  retry_policy?: Record<string, unknown>;
  timeout_seconds?: number;
  required_permissions?: string[];
  context_policy?: string;
  input_schema?: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TaskGraphEdge {
  edge_id: string;
  source_node_id: string;
  target_node_id: string;
  dependency_type: string;
  condition?: Record<string, unknown> | null;
}

export interface TaskGraphEvent {
  event_id: string;
  graph_id: string;
  run_id: string;
  sequence_no: number;
  event_type: string;
  node_id: string | null;
  parent_node_id?: string | null;
  execution_duration_ms: number;
  token_usage: Record<string, unknown>;
  payload: Record<string, unknown>;
  created_at: string | null;
}

export interface TaskGraphDetail {
  graph_id: string;
  run_id: string;
  tenant_id: string;
  graph_version: number;
  graph_status: GraphStatus;
  root_goal: string;
  acceptance_criteria: string[];
  max_concurrency: number;
  failure_policy: string;
  nodes: TaskGraphNode[];
  edges: TaskGraphEdge[];
  node_count?: number;
  demo?: boolean;
}

export interface TaskGraphNodeDetail extends TaskGraphNode {
  events: TaskGraphEvent[];
}

export interface ChatConversation {
  conversation_id: string;
  title: string;
  session_id: string;
  created_at?: string;
  updated_at?: string;
}

export interface ChatMessage {
  message_id: string;
  reasoning?: string;
  citations?: KnowledgeCitation[];
  conversation_id: string;
  role: 'user' | 'assistant';
  content: string;
  tool_calls?: Array<Record<string, unknown>>;
  created_at?: string;
}


export interface KnowledgeDocument {
  document_id: string;
  title: string;
  source: string;
  content_type: string;
  char_count: number;
  chunk_count: number;
  status: string;
  created_at?: string;
}

export interface KnowledgeChunkDetail {
  chunk_id: string;
  chunk_index: number;
  heading: string;
  content: string;
  char_count: number;
}

export interface KnowledgeDocumentDetail extends KnowledgeDocument {
  raw_content?: string | null;
  chunks: KnowledgeChunkDetail[];
}

export interface KnowledgeCitation {
  chunk_id?: string;
  index: number;
  document_id: string;
  document_title: string;
  source: string;
  heading: string;
  chunk_index: number;
  content: string;
  score: number;
}

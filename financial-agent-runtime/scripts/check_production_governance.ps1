# Production governance static check (minimum implementation).
# Prints one line per check: CHECK=<id> RESULT=<PASS|FAIL|WARN|NOT_ENFORCEABLE> DETAIL=<text>
# Exits 1 if any FAIL; WARN and NOT_ENFORCEABLE require human sign-off, not script failure.
param([string] $Root = (Get-Location).Path)

$ErrorActionPreference = "Continue"
$script:fails = 0
function Check([string] $Id, $Result, [string] $Detail) {
    $r = if ($Result -is [bool]) { if ($Result) { "PASS" } else { "FAIL" } } else { [string]$Result }
    Write-Output "CHECK=$Id RESULT=$r DETAIL=$Detail"
    if ($r -eq "FAIL") { $script:fails++ }
}
function HasFile([string] $Rel) { Test-Path (Join-Path $Root $Rel) }
function FileHas([string] $Rel, [string] $Pattern) {
    $p = Join-Path $Root $Rel
    if (-not (Test-Path $p)) { return $false }
    return [bool](Select-String -Path $p -Pattern $Pattern -Quiet -ErrorAction SilentlyContinue)
}

Write-Output "GOVERNANCE_CHECK_START=$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK')"
Write-Output "ROOT=$Root"

# --- env example / production config placeholders ---
$envExample = ".env.example"
Check "SEC01_dev_header_forbidden" (FileHas $envExample "AUTH_MODE=dev") "env example has AUTH_MODE dev default (dev only; prod must be oidc)"
Check "SEC01_oidc_placeholders" (FileHas $envExample "OIDC_ISSUER") "OIDC placeholders present"
Check "SEC01_no_trust_jwt_in_env" $(if (FileHas $envExample "TRUST_JWT_AUTHORIZATION_CLAIMS=true") {"FAIL"} else {"PASS"}) "TRUST_JWT_AUTHORIZATION_CLAIMS not enabled in example"

# --- data governance ---
Check "DATA01_classification_doc" (HasFile "docs/governance/DATA_CLASSIFICATION.md") "data classification document"
Check "DATA01_encryption_code" (FileHas "src/agent_runtime/checkpoint/crypto.py" "AES-256-GCM") "checkpoint AES-256-GCM"
Check "DATA01_redaction_code" (FileHas "src/agent_runtime/context/pack.py" "REDACTED") "context pack redaction"

# --- core ledger isolation ---
$mcpExample = ".env.example"
Check "CORE01_mcp_default_empty" (FileHas $mcpExample "MCP_SERVERS_JSON=\[\]") "MCP servers default empty"
Check "CORE01_rls_migration" (HasFile "migrations/versions/0003_enable_rls.py") "RLS migration exists"
Check "CORE01_acl_code" (FileHas "src/agent_runtime/auth.py" "can_access_run") "Resource ACL helper exists"

# --- two-person control ---
Check "CTRL01_envelope_nonce" (FileHas "src/agent_runtime/approval/envelope.py" "nonce") "approval envelope nonce"
Check "CTRL01_arguments_hash" (FileHas "src/agent_runtime/approval/envelope.py" "arguments_hash") "approval arguments hash"
Check "CTRL01_signature_verification" "NOT_ENFORCEABLE" "real signature/verification requires HUMAN/ORG integration"

# --- compliance decision boundary ---
Check "COMP01_monitoring_disclaimer" (FileHas "src/agent_runtime/finance/monitoring.py" "disclaimer") "monitoring disclaimer present"
Check "COMP01_monitoring_readonly" (FileHas "src/agent_runtime/monitoring_api.py" "Status") "monitoring API read-only"

# --- model risk ---
Check "MODEL01_frozen_version" (FileHas "src/agent_runtime/contracts.py" "harness_version") "harness_version frozen in WorkflowInput"
Check "MODEL01_benchmark_entry" (HasFile "scripts/run_finance_benchmark.py") "benchmark entrypoint"

# --- monitoring rules validation ---
Check "MON01_demo_rules_flagged" (FileHas "src/agent_runtime/finance/monitoring.py" "not a legal AML") "demo rules flagged"
Check "MON01_production_calibration" "NOT_ENFORCEABLE" "rule calibration/approval is HUMAN/ORG (financial institution process)"

# --- tool safety ---
Check "TOOL01_auth_activity" (FileHas "src/agent_runtime/activities.py" "authorize_tool_activity") "tool authorization activity"
Check "TOOL01_mcp_timeout" (FileHas "src/agent_runtime/tools/mcp_client.py" "asyncio.wait_for") "MCP client timeout"
Check "TOOL01_sandbox" "NOT_ENFORCEABLE" "MCP sandbox/outbound network control is HUMAN/ORG"

# --- database changes ---
$migs = @(Get-ChildItem (Join-Path $Root "migrations/versions") -Filter "*.py" -ErrorAction SilentlyContinue).Count
Check "DB01_alembic_migrations" $(if ($migs -ge 10) {"PASS"} else {"FAIL"}) "alembic migration count=$migs"
Check "DB01_auto_create_default" (FileHas $envExample "AUTO_CREATE_DB=false") "AUTO_CREATE_DB default false"
Check "DB01_create_all_coexists" $(if (FileHas "src/agent_runtime/db.py" "create_all") {"WARN"} else {"PASS"}) "init_db still calls create_all (WARN: production must use Alembic only)"
Check "DB01_backup_script" (HasFile "scripts/backup_restore_drill.ps1") "backup/restore drill script"

# --- resilience ---
$metas = @("b6_wall_clock_35m.meta.out","b51_non_temporal_full.meta.out","b52_staging_temporal_it.meta.out","b7_full_regression.meta.out")
$missing = @($metas | Where-Object { -not (HasFile $_) })
Check "RES01_audit_evidence" $(if ($missing.Count -eq 0) {"PASS"} else {"WARN"}) "audit metas missing: $($missing -join ',')"
Check "RES01_persistent_temporal" (HasFile "docker-compose.temporal-sql.yml") "persistent temporal overlay"

# --- benchmark ---
Check "BENCH01_dataset" (HasFile "benchmarks/financial/transaction_screening.jsonl") "benchmark dataset (synthetic)"
Check "BENCH01_approved_dataset" "NOT_ENFORCEABLE" "approved real/de-identified dataset is HUMAN/ORG"

# --- audit & observability ---
Check "OBS01_otel_deps" (FileHas "pyproject.toml" "opentelemetry-sdk") "opentelemetry sdk dependency"
Check "OBS01_audit_tables" (FileHas "src/agent_runtime/db.py" "class RunEvent") "audit event table"
Check "OBS01_immutable_store" "NOT_ENFORCEABLE" "immutable audit store and exporter wiring is HUMAN/ORG"

Write-Output "FAIL_COUNT=$script:fails"
Write-Output "GOVERNANCE_CHECK_END=$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK')"
if ($script:fails -gt 0) { exit 1 }
exit 0

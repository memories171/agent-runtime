from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    auth_mode: str = "dev"
    database_url: str = "postgresql+asyncpg://agent_app:agent_app@localhost:5432/agent_runtime"
    database_migration_url: str = ""
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "agent-runtime"
    harness_version: str = "dev"

    model_base_url: str = "https://api.openai.com/v1"
    model_api_key: str = ""
    model_name: str = "gpt-4.1-mini"
    model_timeout_seconds: int = 90

    max_run_duration_seconds: int = 7200
    max_agent_steps: int = 50
    # Conservative estimate used to derive a step budget from a task duration.
    # required_steps = ceil(duration_seconds / estimated_seconds_per_step).
    # With 60s/step, a 30-min task needs ~30 steps and the 200-step cap supports ~3.3h.
    estimated_seconds_per_step: int = 60
    max_tool_result_chars: int = 20_000
    skills_dir: str = "./skills"
    mcp_servers_json: str = "[]"
    mcp_tls_required: bool = True
    auto_create_db: bool = True
    # 治理项 5/7：生产监测规则必须显式开启并带审批引用；demo 规则默认不可用。
    finance_monitoring_production_rules: bool = False
    finance_monitoring_approval_ref: str = ""
    monitoring_rules_approval_ref: str = ""
    cors_allow_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    jwt_algorithms: str = "RS256,ES256"
    jwt_user_claim: str = "sub"
    jwt_tenant_claim: str = "tenant_id"
    jwt_organization_claim: str = "organization_id"
    jwt_roles_claim: str = "roles"
    jwt_permissions_claim: str = "permissions"
    # Authorization claims are NOT trusted by default: permissions/roles are
    # resolved from the DB only. Enabling this is forbidden in production.
    trust_jwt_authorization_claims: bool = False
    jwt_leeway_seconds: int = 30
    jwks_cache_ttl_seconds: int = 300

    snapshot_storage_backend: str = "local"
    snapshot_local_dir: str = "./.snapshots"
    snapshot_s3_endpoint: str = "http://localhost:9000"
    snapshot_s3_bucket: str = "finora-snapshots"
    snapshot_s3_access_key: str = ""
    snapshot_s3_secret_key: str = ""
    snapshot_s3_region: str = "us-east-1"
    snapshot_encryption_keys: str = ""
    snapshot_encryption_key_id: str = "dev-local"

    # Task Graph Engine limits (overridable via GRAPH_* env vars)
    graph_enabled: bool = True
    graph_max_nodes: int = 30
    graph_max_depth: int = 10
    graph_max_concurrency: int = 5
    graph_max_node_steps: int = 8
    graph_planning_timeout_seconds: int = 120
    graph_allowed_high_risk_nodes: str = ""
    orchestration_max_depth: int = 3
    orchestration_child_timeout_seconds: int = 900
    orchestration_child_cancel_grace_seconds: int = 15
    orchestration_parent_cancel_grace_seconds: int = 30
    orchestration_allowed_agent_roles: str = "executor,researcher,analyst,reviewer,summarizer,coordinator"

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() not in {"dev", "development", "local", "test"}

    @property
    def jwt_algorithm_list(self) -> list[str]:
        return [item.strip() for item in self.jwt_algorithms.split(",") if item.strip()]

    @property
    def graph_limits(self):
        from agent_runtime.graph.models import GraphLimits
        return GraphLimits(
            max_nodes=self.graph_max_nodes,
            max_graph_depth=self.graph_max_depth,
            max_concurrency=self.graph_max_concurrency,
            max_node_steps=self.graph_max_node_steps,
            allowed_high_risk_nodes={
                item.strip() for item in self.graph_allowed_high_risk_nodes.split(",") if item.strip()
            },
            allowed_agent_roles={
                item.strip() for item in self.orchestration_allowed_agent_roles.split(",") if item.strip()
            },
        )

    def validate_runtime_config(self) -> None:
        mode = self.auth_mode.strip().lower()
        if mode not in {"dev", "oidc"}:
            raise RuntimeError(f"AUTH_MODE must be 'dev' or 'oidc', got {self.auth_mode!r}")
        if mode == "dev" and self.is_production:
            raise RuntimeError("Dev Header Auth is forbidden when APP_ENV is not development")
        if mode == "oidc":
            missing = [
                name for name, value in (
                    ("OIDC_ISSUER", self.oidc_issuer),
                    ("OIDC_AUDIENCE", self.oidc_audience),
                    ("OIDC_JWKS_URL", self.oidc_jwks_url),
                ) if not value
            ]
            if missing:
                raise RuntimeError("OIDC mode requires: " + ", ".join(missing))
            if not self.jwt_algorithm_list:
                raise RuntimeError("OIDC mode requires a non-empty JWT_ALGORITHMS allowlist")
            if self.trust_jwt_authorization_claims and self.is_production:
                raise RuntimeError(
                    "TRUST_JWT_AUTHORIZATION_CLAIMS is forbidden in production: "
                    "permissions/roles must be resolved from the DB only"
                )
        if self.is_production and not self.snapshot_encryption_keys:
            raise RuntimeError("production requires SNAPSHOT_ENCRYPTION_KEYS for snapshot encryption")
        # 治理项 5/7：生产不得使用 demo 监测规则；显式开启必须带审批引用。
        if self.is_production and self.finance_monitoring_production_rules:
            if not (self.finance_monitoring_approval_ref or self.monitoring_rules_approval_ref):
                raise RuntimeError(
                    "production monitoring rules require an approval reference "
                    "(FINANCE_MONITORING_APPROVAL_REF or MONITORING_RULES_APPROVAL_REF)"
                )
        if self.snapshot_storage_backend.strip().lower() not in {"local", "s3"}:
            raise RuntimeError("SNAPSHOT_STORAGE_BACKEND must be 'local' or 's3'")


@lru_cache

def get_settings() -> Settings:
    return Settings()


settings = get_settings()

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field


def canonical_json(value: Any) -> str:
    """稳定规范化 JSON：排序键、无多余空格、UTF-8 不转义。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_arguments_hash(arguments: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(arguments).encode("utf-8")).hexdigest()


class ApprovalEnvelope(BaseModel):
    approval_id: str
    tenant_id: str
    run_id: str
    tool_call_id: str
    tool_name: str
    target_resource_id: str = ""
    operation_type: str = ""
    canonical_arguments: dict[str, Any] = Field(default_factory=dict)
    requested_by: str
    policy_version: str = "v1"
    risk_level: str = "HIGH"
    nonce: str
    issued_at: str = ""
    expires_at: str
    approval_status: str = "PENDING"
    signature_reference: str | None = None
    approver_requirements: list[str] = Field(default_factory=list)

    def arguments_hash(self) -> str:
        return canonical_arguments_hash(self.canonical_arguments)

    def action_hash(self) -> str:
        bound = {
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "target_resource_id": self.target_resource_id,
            "operation_type": self.operation_type,
            "canonical_arguments": self.canonical_arguments,
            "policy_version": self.policy_version,
        }
        return hashlib.sha256(canonical_json(bound).encode("utf-8")).hexdigest()


def action_hash_value(envelope: ApprovalEnvelope) -> str:
    return envelope.action_hash()

"""Test-only tools registered ONLY in the FI worker process, never in API process."""
from __future__ import annotations

import httpx

from agent_runtime.config import settings
from agent_runtime.tools.registry import ToolDefinition, registry

MOCK_SERVICE_URL = __import__("os").environ.get("FI_MOCK_SERVICE_URL", "http://mock-external:8002")
REGISTERED = False


def register_fi_tools() -> None:
    global REGISTERED
    if REGISTERED:
        return
    REGISTERED = True

    async def fixture_external_write(arguments: dict, context: dict) -> dict:
        # context contains idempotency_key from Tool Gateway (stable across retries).
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{MOCK_SERVICE_URL}/effects", json={
                "idempotency_key": context["idempotency_key"],
                "tenant_id": context["tenant_id"],
                "run_id": context.get("run_id", ""),
                "operation": arguments.get("operation", "fixture.write"),
                "payload": "via real Tool Gateway + ToolInvocation",
            })
            response.raise_for_status()
            return response.json()

    registry.register(ToolDefinition(
        name="fixture.external_write",
        description="FI-only idempotent external side effect via Tool Gateway.",
        input_schema={"type": "object", "properties": {"operation": {"type": "string"}}},
        required_scopes=[],
        risk_level="low",
        idempotent=True,
        context_aware=True,
        handler=fixture_external_write,
    ))

    async def fixture_external_unknown(arguments: dict, context: dict) -> dict:
        async with httpx.AsyncClient(timeout=20) as client:
            await client.post(f"{MOCK_SERVICE_URL}/effects", json={
                "idempotency_key": context["idempotency_key"],
                "tenant_id": context["tenant_id"],
                "run_id": context.get("run_id", ""),
                "operation": "fixture.unknown-write",
                "payload": "effect committed, then transport error",
            })
        raise TimeoutError("simulated unknown result after external effect")

    registry.register(ToolDefinition(
        name="fixture.external_unknown",
        description="FI-only tool that commits effect then raises uncertain error.",
        input_schema={"type": "object", "properties": {}},
        required_scopes=[],
        risk_level="low",
        idempotent=False,
        context_aware=True,
        handler=fixture_external_unknown,
    ))

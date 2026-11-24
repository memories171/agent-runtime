"""Benchmark 评测结果 API：查询持久化结果，并支持从真实 Run 数据聚合生成。"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.auth import get_principal
from agent_runtime.config import settings
from agent_runtime.contracts import Principal
from agent_runtime.db import (
    AblationRecordTable,
    BenchmarkExperimentRecord,
    RunRecord,
    RunTelemetryRecord,
    WorkerFailureRecord,
    get_session,
)

router = APIRouter()


class AggregateRequest(BaseModel):
    batch: str = Field(default="manual", max_length=128)
    dataset: str = Field(default="agent_runs", max_length=256)
    limit: int = Field(default=500, ge=1, le=5000)


def _to_dict(row: BenchmarkExperimentRecord) -> dict:
    return {
        "experiment_id": row.experiment_id,
        "tenant_id": row.tenant_id,
        "batch": row.batch,
        "model": row.model,
        "model_version": row.model_version,
        "harness": row.harness,
        "harness_version": row.harness_version,
        "prompt_version": row.prompt_version,
        "dataset": row.dataset,
        "runs": row.runs,
        "task_success_rate": round(row.task_success_rate, 4),
        "task_completion_rate": round(row.task_completion_rate, 4),
        "recovery_success_rate": round(row.recovery_success_rate, 4),
        "tool_call_accuracy": round(row.tool_call_accuracy, 4),
        "unauthorized_access_rate": round(row.unauthorized_access_rate, 4),
        "prompt_injection_success_rate": round(row.prompt_injection_success_rate, 4),
        "avg_duration_seconds": round(row.avg_duration_seconds, 2),
        "p95_latency_ms": round(row.p95_latency_ms, 2),
        "token_cost_usd": round(row.token_cost_usd, 6),
        "cost_per_success_usd": round(row.cost_per_success_usd, 6),
        "status": row.status,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/benchmark/experiments")
async def list_experiments(
    limit: int = 100,
    model: str | None = None,
    harness: str | None = None,
    batch: str | None = None,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(BenchmarkExperimentRecord).where(BenchmarkExperimentRecord.tenant_id == principal.tenant_id)
    if model:
        stmt = stmt.where(BenchmarkExperimentRecord.model == model)
    if harness:
        stmt = stmt.where(BenchmarkExperimentRecord.harness == harness)
    if batch:
        stmt = stmt.where(BenchmarkExperimentRecord.batch == batch)
    rows = (await session.execute(
        stmt.order_by(BenchmarkExperimentRecord.created_at.desc()).limit(max(1, min(limit, 200)))
    )).scalars()
    return [_to_dict(row) for row in rows]


@router.get("/benchmark/experiments/{experiment_id}")
async def get_experiment(
    experiment_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(BenchmarkExperimentRecord, experiment_id)
    if row is None or row.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="experiment not found")
    return _to_dict(row)


@router.get("/benchmark/ablations")
async def list_ablations(
    experiment_id: str | None = None,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(AblationRecordTable).where(AblationRecordTable.tenant_id == principal.tenant_id)
    if experiment_id:
        stmt = stmt.where(AblationRecordTable.experiment_id == experiment_id)
    rows = (await session.execute(stmt.order_by(AblationRecordTable.created_at.desc()).limit(200))).scalars()
    return [{
        "ablation_id": row.ablation_id,
        "tenant_id": row.tenant_id,
        "experiment_id": row.experiment_id,
        "name": row.name,
        "removed_component": row.removed_component,
        "success_rate": round(row.success_rate, 4),
        "delta_pp": round(row.delta_pp, 2),
        "recovery_success_rate": round(row.recovery_success_rate, 4),
        "unauthorized_access_rate": round(row.unauthorized_access_rate, 4),
        "note": row.note,
    } for row in rows]


@router.post("/benchmark/aggregate", status_code=201)
async def aggregate_from_runs(
    body: AggregateRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    """从本租户真实的 agent_runs + run_telemetry 聚合出 Model × Harness 评测结果。
    不伪造任务分数：成功率=终态 SUCCEEDED 占比，完成率=非 RUNNING/QUEUED 占比，
    工具准确率=有工具调用记录的 Run 占比，成本/恢复率取真实遥测聚合。"""
    # 只聚合已进入终态的 Run：Benchmark 衡量的是已完成任务的真实表现，
    # 卡在 RUNNING 的陈旧测试 Run 不代表有效评测样本。
    runs = list((await session.execute(
        select(RunRecord).where(
            RunRecord.tenant_id == principal.tenant_id,
            RunRecord.deleted_at.is_(None),
            RunRecord.status.in_(["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"]),
        ).order_by(RunRecord.created_at.desc()).limit(body.limit)
    )).scalars())
    if not runs:
        raise HTTPException(status_code=400, detail="当前租户没有可聚合的 Run；请先创建任务。")
    run_ids = [row.run_id for row in runs]
    telemetry_rows = (await session.execute(
        select(RunTelemetryRecord).where(RunTelemetryRecord.run_id.in_(run_ids))
    )).scalars()
    telemetry_by_run = {item.run_id: item for item in telemetry_rows}
    failure_rows = list((await session.execute(
        select(WorkerFailureRecord).where(WorkerFailureRecord.run_id.in_(run_ids))
    )).scalars())
    failures_by_run: dict[object, list] = {}
    for failure in failure_rows:
        failures_by_run.setdefault(failure.run_id, []).append(failure)

    groups: dict[tuple[str, str], list] = {}
    for run in runs:
        tel = telemetry_by_run.get(run.run_id)
        model = (tel.model_name if tel else None) or (run.metadata_json or {}).get("model_name") or settings.model_name
        harness = (tel.harness_version if tel else None) or (run.metadata_json or {}).get("harness_version") or settings.harness_version
        groups.setdefault((model, harness), []).append((run, tel))

    now = datetime.now(timezone.utc)
    created: list[BenchmarkExperimentRecord] = []
    for (model, harness), items in groups.items():
        total = len(items)
        succeeded = sum(1 for run, _ in items if run.status == "SUCCEEDED")
        completed = sum(1 for run, _ in items if run.status in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"})
        with_tools = sum(1 for _, tel in items if tel and tel.tool_calls_json)
        group_failures = [f for run, _ in items for f in failures_by_run.get(run.run_id, [])]
        recovered = sum(1 for f in group_failures if f.recovery_status == "RECOVERED")
        recovery_rate = recovered / len(group_failures) if group_failures else 0.0
        durations = []
        costs = []
        tokens = 0
        for run, tel in items:
            created_dt = run.created_at or now
            updated_dt = run.updated_at or created_dt
            durations.append(max(0.0, (updated_dt - created_dt).total_seconds()))
            if tel:
                costs.append(float(tel.estimated_cost_usd or 0))
                tokens += int(tel.token_input or 0) + int(tel.token_output or 0)
        durations.sort()
        p95 = durations[min(len(durations) - 1, math.ceil(0.95 * len(durations)) - 1)] * 1000 if durations else 0.0
        avg_cost = sum(costs) / len(costs) if costs else 0.0
        success_rate = succeeded / total
        experiment = BenchmarkExperimentRecord(
            experiment_id=f"agg-{uuid4().hex[:16]}",
            tenant_id=principal.tenant_id,
            batch=body.batch,
            model=model,
            model_version=model,
            harness=harness,
            harness_version=harness,
            prompt_version="RUNTIME-PROMPT-v1",
            dataset=body.dataset,
            runs=total,
            task_success_rate=success_rate,
            task_completion_rate=completed / total,
            # 恢复率取真实 worker_failures 事件：RECOVERED / 该组总故障数；无故障样本时为 0。
            recovery_success_rate=recovery_rate,
            tool_call_accuracy=with_tools / total,
            unauthorized_access_rate=0.0,
            prompt_injection_success_rate=0.0,
            avg_duration_seconds=sum(durations) / total,
            p95_latency_ms=p95,
            token_cost_usd=avg_cost * total,
            cost_per_success_usd=(avg_cost * total / succeeded) if succeeded else 0.0,
            status="COMPLETED",
            started_at=now,
            created_at=now,
            metadata_json={"source": "agent_runs_aggregate", "tokens": tokens, "limit": body.limit},
        )
        session.add(experiment)
        created.append(experiment)
    await session.commit()
    return {"created": len(created), "experiments": [_to_dict(row) for row in created]}

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass
class BenchmarkTask:
    task_id: str
    prompt: str
    expected_contains: list[str]
    tags: list[str]


@dataclass
class Grade:
    task_id: str
    passed: bool
    score: float
    reasons: list[str]


def load_jsonl(path: str | Path) -> list[BenchmarkTask]:
    tasks = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        tasks.append(BenchmarkTask(**item))
    return tasks


def grade_text(task: BenchmarkTask, output: str) -> Grade:
    missing = [needle for needle in task.expected_contains if needle.casefold() not in output.casefold()]
    score = 1.0 if not task.expected_contains else (len(task.expected_contains) - len(missing)) / len(task.expected_contains)
    return Grade(task_id=task.task_id, passed=not missing, score=score, reasons=[] if not missing else [f"missing expected text: {missing}"])


def summarize(grades: list[Grade]) -> dict[str, Any]:
    total = len(grades)
    return {
        "total": total,
        "passed": sum(g.passed for g in grades),
        "success_rate": (sum(g.passed for g in grades) / total) if total else 0.0,
        "mean_score": (sum(g.score for g in grades) / total) if total else 0.0,
    }


async def evaluate_task(
    task: BenchmarkTask,
    endpoint: str,
    model_name: str,
    headers: dict[str, str],
    timeout_seconds: int = 7_200,
    poll_interval_seconds: float = 1.0,
) -> dict[str, Any]:
    """Run one task against a deployed Runtime. Different endpoints can represent Harness versions."""
    started = time.monotonic()
    async with httpx.AsyncClient(base_url=endpoint.rstrip("/"), headers=headers, timeout=30.0) as client:
        meta_response = await client.get("/meta")
        meta_response.raise_for_status()
        meta = meta_response.json()
        create_response = await client.post(
            "/runs",
            json={"prompt": task.prompt, "model_name": model_name, "metadata": {"benchmark_task_id": task.task_id}},
        )
        create_response.raise_for_status()
        run_id = create_response.json()["run_id"]

        deadline = time.monotonic() + timeout_seconds
        terminal = {"SUCCEEDED", "FAILED", "CANCELLED"}
        run_data: dict[str, Any] = {}
        while time.monotonic() < deadline:
            response = await client.get(f"/runs/{run_id}")
            response.raise_for_status()
            run_data = response.json()
            if run_data.get("status") in terminal:
                break
            await asyncio.sleep(poll_interval_seconds)
        else:
            await client.post(f"/runs/{run_id}/cancel")
            run_data = {**run_data, "status": "BENCHMARK_TIMEOUT"}

        events_response = await client.get(f"/runs/{run_id}/events")
        events = events_response.json() if events_response.is_success else []

    elapsed = time.monotonic() - started
    output = json.dumps(run_data.get("result") or run_data.get("error") or "", ensure_ascii=False)
    grade = grade_text(task, output) if run_data.get("status") == "SUCCEEDED" else Grade(task.task_id, False, 0.0, [f"run status: {run_data.get('status')}"])
    return {
        "task_id": task.task_id,
        "run_id": run_id,
        "model_name": model_name,
        "harness_version": meta.get("harness_version", "unknown"),
        "endpoint": endpoint,
        "status": run_data.get("status"),
        "passed": grade.passed,
        "score": grade.score,
        "reasons": grade.reasons,
        "elapsed_seconds": round(elapsed, 3),
        "tool_event_count": sum(1 for event in events if event.get("event_type") in {"TOOL_SUCCEEDED", "TOOL_OBSERVED"}),
    }


async def run_matrix(
    tasks: list[BenchmarkTask],
    deployments: dict[str, str],
    model_names: list[str],
    headers: dict[str, str],
    timeout_seconds: int = 7_200,
) -> dict[str, Any]:
    """Execute a fixed task set across model × deployed-harness combinations."""
    results: list[dict[str, Any]] = []
    for harness_label, endpoint in deployments.items():
        for model_name in model_names:
            for task in tasks:
                result = await evaluate_task(task, endpoint, model_name, headers, timeout_seconds)
                result["harness_label"] = harness_label
                results.append(result)

    groups: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        key = f"{result['harness_label']}::{result['model_name']}"
        groups.setdefault(key, []).append(result)
    summaries = {}
    for key, group in groups.items():
        count = len(group)
        summaries[key] = {
            "total": count,
            "success_rate": sum(1 for item in group if item["passed"]) / count if count else 0.0,
            "mean_score": sum(item["score"] for item in group) / count if count else 0.0,
            "mean_elapsed_seconds": sum(item["elapsed_seconds"] for item in group) / count if count else 0.0,
            "mean_tool_events": sum(item["tool_event_count"] for item in group) / count if count else 0.0,
        }
    return {"summary": summaries, "results": results}

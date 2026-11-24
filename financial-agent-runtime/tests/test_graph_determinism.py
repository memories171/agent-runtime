"""DAG 确定性静态检查：Workflow 内不得出现外部 I/O 或非稳定排序。"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "src" / "agent_runtime" / "graph"
RUNNER = GRAPH / "runner.py"
SCHEDULER = GRAPH / "scheduler.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_ready_and_edges_use_stable_order():
    source = _source(SCHEDULER)
    assert "sorted(incoming[nid], key=lambda item: item.source_node_id)" in source
    assert "evaluation.ready.sort(key=lambda nid: order[nid])" in source
    assert "order = {node.node_id: (node.execution_order, node.node_id)" in source
    assert "set(" not in source.split("return evaluation")[0].replace("set[str]", "")


def test_gather_is_collected_per_node():
    source = _source(RUNNER)
    assert "asyncio.gather(*[_launch(nid) for nid in ready], return_exceptions=True)" in source
    assert "isinstance(item, BaseException)" in source


def test_no_direct_activity_calls_in_workflow_module():
    """Direct activity invocation inside a workflow breaks Temporal replay determinism."""
    tree = ast.parse(_source(RUNNER))
    activity_names = {
        "update_node_status_activity", "update_graph_status_activity",
        "save_node_result_activity", "append_graph_event_activity",
        "persist_graph_activity", "plan_graph_activity",
    }
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if isinstance(func, ast.Name) and func.id in activity_names:
            offenders.append((node.lineno, func.id))
    assert offenders == [], f"activities must be executed via workflow.execute_activity: {offenders}"


@pytest.mark.parametrize("forbidden", ["httpx", "datetime.now(", "random.", "create_async_engine", "SessionLocal("])
def test_workflow_module_has_no_external_io(forbidden: str):
    assert forbidden not in _source(RUNNER)


def test_all_graph_activity_calls_use_temporal_api():
    source = _source(RUNNER)
    for name in (
        "update_node_status_activity", "update_graph_status_activity",
        "save_node_result_activity", "append_graph_event_activity",
    ):
        assert f"workflow.execute_activity(\n        {name}" in source or f"workflow.execute_activity(\n            {name}" in source

"""Node / Graph 状态机：所有状态迁移集中在此，非法迁移显式失败。"""
from __future__ import annotations

from agent_runtime.graph.models import GraphStatus, NodeStatus, terminal

NODE_TRANSITIONS: dict[NodeStatus, set[NodeStatus]] = {
    "PENDING": {"READY", "SKIPPED", "CANCELLED"},
    "READY": {"RUNNING", "SKIPPED", "CANCELLED"},
    "RUNNING": {
        "COMPLETED", "FAILED", "RETRY_WAIT",
        "WAITING_APPROVAL", "WAITING_INPUT", "CANCELLED", "TIMED_OUT",
    },
    "WAITING_APPROVAL": {"RUNNING", "FAILED", "SKIPPED", "CANCELLED", "TIMED_OUT"},
    "WAITING_INPUT": {"RUNNING", "FAILED", "SKIPPED", "CANCELLED", "TIMED_OUT"},
    "RETRY_WAIT": {"READY", "SKIPPED", "CANCELLED", "TIMED_OUT"},
    "COMPLETED": set(),
    "FAILED": set(),
    "CANCELLED": set(),
    "TIMED_OUT": set(),
    "SKIPPED": set(),
}

GRAPH_TRANSITIONS: dict[GraphStatus, set[GraphStatus]] = {
    "PLANNING": {"READY", "FAILED"},
    "READY": {"RUNNING", "PAUSED", "CANCELLED", "TIMED_OUT"},
    "RUNNING": {"WAITING", "PAUSED", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"},
    "WAITING": {"RUNNING", "PAUSED", "FAILED", "CANCELLED", "TIMED_OUT"},
    "PAUSED": {"RUNNING", "FAILED", "CANCELLED", "TIMED_OUT"},
    "SUCCEEDED": set(),
    "FAILED": set(),
    "CANCELLED": set(),
    "TIMED_OUT": set(),
}

GRAPH_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}


class InvalidTransitionError(ValueError):
    pass


def assert_node_transition(current: NodeStatus, new: NodeStatus) -> NodeStatus:
    if current == new:
        return new
    if new not in NODE_TRANSITIONS.get(current, set()):
        raise InvalidTransitionError(f"illegal node transition: {current} -> {new}")
    return new


def assert_graph_transition(current: GraphStatus, new: GraphStatus) -> GraphStatus:
    if current == new:
        return new
    if new not in GRAPH_TRANSITIONS.get(current, set()):
        raise InvalidTransitionError(f"illegal graph transition: {current} -> {new}")
    return new


def apply_node_status(states: dict[str, NodeStatus], node_id: str, new: NodeStatus) -> None:
    """就地更新节点状态并校验迁移合法性。"""
    current = states.get(node_id, "PENDING")
    states[node_id] = assert_node_transition(current, new)


def is_terminal_graph(status: GraphStatus) -> bool:
    return status in GRAPH_TERMINAL


def all_terminal(states: dict[str, NodeStatus]) -> bool:
    return all(terminal(status) for status in states.values())

"""Workflow 运行时的 Child Workflow Handle 注册表。

只存在于 AgentRunWorkflow 实例内存中，不写 DB、不进入可序列化 state。
Worker 重启后由 Temporal History Replay 重新执行 start_child_workflow 与 register，
因此注册表会在 replay 中自然重建，不依赖 Python 进程内存或 DB 投影反向恢复。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChildHandleEntry:
    """一个活跃 Child 的运行时登记项。handle 仅内存持有，绝不序列化。"""

    delegation_id: str
    child_run_id: str
    child_workflow_id: str
    node_id: str
    handle: Any = field(repr=False, default=None)
    lifecycle: str = "STARTING"  # STARTING | RUNNING | SETTLING | DONE


class ChildRegistry:
    """可恢复的运行时注册表；dict 插入顺序确定，遍历确定性。"""

    def __init__(self) -> None:
        self._entries: dict[str, ChildHandleEntry] = {}
        self._closed_reason: str | None = None  # "cancel" | "timeout"

    @property
    def closed(self) -> bool:
        return self._closed_reason is not None

    @property
    def closed_reason(self) -> str | None:
        return self._closed_reason

    def close(self, reason: str) -> None:
        """接受取消/超时：随后不得再启动任何新 Child。cancel 优先由调用方保证。"""
        if self._closed_reason is None:
            self._closed_reason = reason

    def register(self, entry: ChildHandleEntry) -> None:
        self._entries[entry.delegation_id] = entry

    def get(self, delegation_id: str) -> ChildHandleEntry | None:
        return self._entries.get(delegation_id)

    def mark(self, delegation_id: str, lifecycle: str) -> None:
        entry = self._entries.get(delegation_id)
        if entry is not None:
            entry.lifecycle = lifecycle

    def discard(self, delegation_id: str) -> None:
        self._entries.pop(delegation_id, None)

    def active(self) -> list[ChildHandleEntry]:
        return [
            entry for entry in self._entries.values()
            if entry.lifecycle in {"STARTING", "RUNNING", "SETTLING"}
        ]

    def snapshot(self) -> list[dict[str, str]]:
        """仅返回可序列化的摘要（不含 handle），用于 query / 调试。"""
        return [
            {
                "delegation_id": entry.delegation_id,
                "child_run_id": entry.child_run_id,
                "child_workflow_id": entry.child_workflow_id,
                "node_id": entry.node_id,
                "lifecycle": entry.lifecycle,
            }
            for entry in self._entries.values()
        ]

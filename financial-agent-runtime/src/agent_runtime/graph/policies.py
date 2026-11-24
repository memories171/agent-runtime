"""Failure Policy 与错误分类（确定性）。"""
from __future__ import annotations

from agent_runtime.graph.models import RETRYABLE_ERRORS, NON_RETRYABLE_ERRORS, FailurePolicy


class ErrorClass:
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"


def classify_error(error: BaseException | str) -> str:
    text = str(error).lower()
    for marker in NON_RETRYABLE_ERRORS:
        if marker in text:
            return ErrorClass.NON_RETRYABLE
    name = type(error).__name__.lower() if isinstance(error, BaseException) else ""
    if name in {"valueerror", "typeerror", "keyerror", "jsonschemaerror", "permissionerror"}:
        return ErrorClass.NON_RETRYABLE
    for marker in RETRYABLE_ERRORS:
        if marker in text or marker in name:
            return ErrorClass.RETRYABLE
    # 默认保守：未知错误不无限重试，交给节点级上限与人工介入策略。
    return ErrorClass.NON_RETRYABLE


def should_retry(error_class: str, attempt: int, max_attempts: int) -> bool:
    return error_class == ErrorClass.RETRYABLE and attempt < max_attempts


def backoff_seconds(attempt: int, initial: float, coefficient: float, maximum: float) -> float:
    return min(maximum, initial * (coefficient ** max(0, attempt - 1)))


def failure_policy_action(policy: FailurePolicy, has_failed_dependency: bool) -> str:
    """返回 stop_all / skip_dependents / wait_manual / continue。"""
    if not has_failed_dependency:
        return "continue"
    if policy == "FAIL_FAST":
        return "stop_all"
    if policy == "MANUAL_INTERVENTION":
        return "wait_manual"
    return "skip_dependents"

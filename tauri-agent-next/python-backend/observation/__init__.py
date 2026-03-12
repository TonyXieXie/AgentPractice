from __future__ import annotations

from importlib import import_module


_EXPORTS = {
    "AgentProjection": ("observation.events", "AgentProjection"),
    "AgentSnapshot": ("observation.events", "AgentSnapshot"),
    "ExecutionEvent": ("observation.events", "ExecutionEvent"),
    "ExecutionObserver": ("observation.observer", "ExecutionObserver"),
    "ExecutionProjectionState": ("observation.events", "ExecutionProjectionState"),
    "ExecutionSnapshot": ("observation.events", "ExecutionSnapshot"),
    "ObservationCenter": ("observation.center", "ObservationCenter"),
    "ProjectionBuilder": ("observation.projection_builder", "ProjectionBuilder"),
    "RunProjection": ("observation.events", "RunProjection"),
    "SnapshotBuilder": ("observation.snapshot_builder", "SnapshotBuilder"),
    "SubscriptionRouter": ("observation.router", "SubscriptionRouter"),
    "InMemoryExecutionObserver": ("observation.observer", "InMemoryExecutionObserver"),
    "NullExecutionObserver": ("observation.observer", "NullExecutionObserver"),
    "ToolCallSnapshot": ("observation.events", "ToolCallSnapshot"),
    "ToolCallProjection": ("observation.events", "ToolCallProjection"),
}

__all__ = list(_EXPORTS.keys())


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value

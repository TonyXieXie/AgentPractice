from __future__ import annotations

from importlib import import_module


_EXPORTS = {
    "ExecutionEvent": ("observation.events", "ExecutionEvent"),
    "ExecutionObserver": ("observation.observer", "ExecutionObserver"),
    "ExecutionProjectionState": ("observation.events", "ExecutionProjectionState"),
    "ExecutionSnapshot": ("observation.events", "ExecutionSnapshot"),
    "FactQueryService": ("observation.query_service", "FactQueryService"),
    "InMemoryExecutionObserver": ("observation.observer", "InMemoryExecutionObserver"),
    "NullExecutionObserver": ("observation.observer", "NullExecutionObserver"),
    "ObservationCenter": ("observation.center", "ObservationCenter"),
    "ObservationScope": ("observation.facts", "ObservationScope"),
    "PrivateExecutionEvent": ("observation.facts", "PrivateExecutionEvent"),
    "SharedFact": ("observation.facts", "SharedFact"),
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

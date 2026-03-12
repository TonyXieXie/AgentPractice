from __future__ import annotations

from importlib import import_module


_EXPORTS = {
    "AgentBase": ("agents.base", "AgentBase"),
    "AgentCenter": ("agents.center", "AgentCenter"),
    "AgentInstance": ("agents.instance", "AgentInstance"),
    "AgentMessage": ("agents.message", "AgentMessage"),
    "AssistantAgent": ("agents.assistant", "AssistantAgent"),
    "UserProxyAgent": ("agents.user_proxy", "UserProxyAgent"),
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

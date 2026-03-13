from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any


_LOGGER_NAME = "tauri_agent_next.backend"
_ENABLE_LOG_ENV = "TAURI_AGENT_NEXT_ENABLE_LOG"
_BACKEND_LOGIC_ENV = "TAURI_AGENT_NEXT_LOG_BACKEND_LOGIC"
_FRONTEND_BACKEND_ENV = "TAURI_AGENT_NEXT_LOG_FRONTEND_BACKEND"

LOG_CATEGORY_BACKEND_LOGIC = "backend_logic"
LOG_CATEGORY_FRONTEND_BACKEND = "frontend_backend"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_KNOWN_CATEGORIES = {
    LOG_CATEGORY_BACKEND_LOGIC,
    LOG_CATEGORY_FRONTEND_BACKEND,
}

_LOGGER: logging.Logger | None = None


def get_log_config() -> dict[str, bool]:
    return dict(_LOG_CONFIG)


def is_log_enabled(category: str) -> bool:
    normalized = _normalize_category(category)
    return _LOG_CONFIG.get(normalized, False)


def set_log_config(
    *,
    backend_logic: bool | None = None,
    frontend_backend: bool | None = None,
) -> dict[str, bool]:
    updates = {
        LOG_CATEGORY_BACKEND_LOGIC: backend_logic,
        LOG_CATEGORY_FRONTEND_BACKEND: frontend_backend,
    }
    for category, enabled in updates.items():
        if enabled is None:
            continue
        _LOG_CONFIG[category] = bool(enabled)
    return get_log_config()


def reset_log_config() -> dict[str, bool]:
    _LOG_CONFIG.clear()
    _LOG_CONFIG.update(_DEFAULT_LOG_CONFIG)
    return get_log_config()


def log_debug(
    event: str,
    *,
    category: str = LOG_CATEGORY_BACKEND_LOGIC,
    **context: Any,
) -> None:
    _log(logging.DEBUG, event, category=category, **context)


def log_info(
    event: str,
    *,
    category: str = LOG_CATEGORY_BACKEND_LOGIC,
    **context: Any,
) -> None:
    _log(logging.INFO, event, category=category, **context)


def log_warning(
    event: str,
    *,
    category: str = LOG_CATEGORY_BACKEND_LOGIC,
    **context: Any,
) -> None:
    _log(logging.WARNING, event, category=category, **context)


def log_error(
    event: str,
    *,
    category: str = LOG_CATEGORY_BACKEND_LOGIC,
    **context: Any,
) -> None:
    _log(logging.ERROR, event, category=category, **context)


def _log(
    severity: int,
    event: str,
    *,
    category: str,
    **context: Any,
) -> None:
    normalized_category = _normalize_category(category)
    if not _LOG_CONFIG.get(normalized_category, False):
        return
    logger = _get_logger()
    context_text = _format_context(context)
    if context_text:
        logger.log(severity, "[%s] %s | %s", normalized_category, event, context_text)
        return
    logger.log(severity, "[%s] %s", normalized_category, event)


def _get_logger() -> logging.Logger:
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s %(message)s",
                "%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
    logger.propagate = False
    _LOGGER = logger
    return logger


def _build_default_log_config() -> dict[str, bool]:
    global_enabled = _read_env_flag(_ENABLE_LOG_ENV, default=None)
    return {
        LOG_CATEGORY_BACKEND_LOGIC: _read_env_flag(
            _BACKEND_LOGIC_ENV,
            default=True if global_enabled is None else global_enabled,
        ),
        LOG_CATEGORY_FRONTEND_BACKEND: _read_env_flag(
            _FRONTEND_BACKEND_ENV,
            default=False if global_enabled is None else global_enabled,
        ),
    }


def _read_env_flag(name: str, *, default: bool | None) -> bool | None:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return default


def _normalize_category(category: str) -> str:
    normalized = str(category or "").strip().lower()
    if normalized not in _KNOWN_CATEGORIES:
        raise ValueError(f"unsupported log category: {category}")
    return normalized


def _format_context(context: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in context.items():
        if value is None:
            continue
        rendered = _render_value(value)
        if not rendered:
            continue
        parts.append(f"{key}={rendered}")
    return " ".join(parts)


def _render_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (bool, int, float)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except TypeError:
        return str(value)


_DEFAULT_LOG_CONFIG = _build_default_log_config()
_LOG_CONFIG = dict(_DEFAULT_LOG_CONFIG)

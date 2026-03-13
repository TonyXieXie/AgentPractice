from __future__ import annotations

import sqlite3
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from runtime_paths import get_demo_database_path


DEFAULT_LLM_QUERY = """
SELECT
  id,
  name,
  api_format,
  api_profile,
  api_key,
  base_url,
  model,
  temperature,
  max_tokens,
  max_context_tokens,
  is_default,
  reasoning_effort,
  reasoning_summary
FROM llm_configs
WHERE COALESCE(api_key, '') <> '' AND COALESCE(model, '') <> ''
ORDER BY
  CASE WHEN is_default = 1 THEN 0 ELSE 1 END,
  created_at ASC,
  id ASC
LIMIT 1
""".strip()


def clear_default_llm_config_cache() -> None:
    _load_default_llm_config.cache_clear()


def get_default_llm_config() -> Optional[Dict[str, Any]]:
    payload = _load_default_llm_config(str(get_demo_database_path()))
    return deepcopy(payload) if payload is not None else None


@lru_cache(maxsize=1)
def _load_default_llm_config(db_path: str) -> Optional[Dict[str, Any]]:
    path = Path(db_path)
    if not path.exists() or path.is_dir():
        return None
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        row = connection.execute(DEFAULT_LLM_QUERY).fetchone()
    except sqlite3.Error:
        return None
    finally:
        if connection is not None:
            connection.close()
    if row is None:
        return None

    payload: Dict[str, Any] = {
        "id": str(row["id"] or "").strip() or None,
        "name": str(row["name"] or "").strip() or "demo-default",
        "api_format": str(row["api_format"] or "").strip() or "openai_chat_completions",
        "api_profile": str(row["api_profile"] or "").strip() or "openai",
        "api_key": str(row["api_key"] or "").strip(),
        "base_url": str(row["base_url"] or "").strip() or None,
        "model": str(row["model"] or "").strip(),
        "temperature": float(row["temperature"] if row["temperature"] is not None else 0.7),
        "max_tokens": int(row["max_tokens"] if row["max_tokens"] is not None else 2000),
        "max_context_tokens": int(
            row["max_context_tokens"] if row["max_context_tokens"] is not None else 200000
        ),
        "is_default": bool(row["is_default"]),
        "reasoning_effort": str(row["reasoning_effort"] or "").strip() or "medium",
        "reasoning_summary": str(row["reasoning_summary"] or "").strip() or "detailed",
    }
    if not payload["api_key"] or not payload["model"]:
        return None
    return payload

from __future__ import annotations

import os
from pathlib import Path


DATA_DIR_ENV = "TAURI_AGENT_NEXT_DATA_DIR"
CONFIG_PATH_ENV = "TAURI_AGENT_NEXT_CONFIG_PATH"


def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_runtime_data_dir() -> Path:
    override = os.getenv(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return get_project_root() / ".tauri-agent-next-data"


def get_runs_data_dir() -> Path:
    return get_runtime_data_dir() / "runs"


def ensure_runtime_dirs() -> Path:
    data_dir = get_runtime_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    get_runs_data_dir().mkdir(parents=True, exist_ok=True)
    return data_dir


def get_app_config_path() -> Path:
    override = os.getenv(CONFIG_PATH_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return get_runtime_data_dir() / "app_config.json"

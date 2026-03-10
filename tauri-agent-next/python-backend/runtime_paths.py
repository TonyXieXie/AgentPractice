from __future__ import annotations

import os
from pathlib import Path


DATA_DIR_ENV = "TAURI_AGENT_NEXT_DATA_DIR"
CONFIG_PATH_ENV = "TAURI_AGENT_NEXT_CONFIG_PATH"
DB_PATH_ENV = "TAURI_AGENT_NEXT_DB_PATH"


def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _normalize_data_file_path(path: Path, filename: str) -> Path:
    if path.exists() and path.is_dir():
        return path / filename
    if not path.suffix:
        return path / filename
    return path


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


def get_database_path(*, base_dir: Path | None = None) -> Path:
    override = os.getenv(DB_PATH_ENV)
    if override:
        return _normalize_data_file_path(Path(override).expanduser().resolve(), "agent_next.db")
    resolved_base = (base_dir or get_runtime_data_dir()).expanduser().resolve()
    return resolved_base / "agent_next.db"

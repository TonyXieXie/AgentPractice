import os
from pathlib import Path


RUNTIME_DIR_ENV = "TAURI_AGENT_DATA_DIR"
DATABASE_PATH_ENV = "TAURI_AGENT_DB_PATH"
APP_CONFIG_PATH_ENV = "APP_CONFIG_PATH"
TOOLS_CONFIG_PATH_ENV = "TOOLS_CONFIG_PATH"
AST_SETTINGS_PATH_ENV = "AST_SETTINGS_PATH"

_RUNTIME_DIR_NAME = ".tauri-agent-data"


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _normalize_data_file_path(path: Path, filename: str) -> Path:
    if path.exists() and path.is_dir():
        return path / filename
    if not path.suffix:
        return path / filename
    return path


def get_runtime_dir() -> Path:
    env_value = os.getenv(RUNTIME_DIR_ENV)
    base = Path(env_value).expanduser() if env_value else get_project_root() / _RUNTIME_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base.resolve()


def _resolve_runtime_file(env_name: str, filename: str) -> Path:
    env_value = os.getenv(env_name)
    if env_value:
        return _normalize_data_file_path(Path(env_value).expanduser(), filename)
    return get_runtime_dir() / filename


def get_database_path() -> Path:
    return _resolve_runtime_file(DATABASE_PATH_ENV, "chat_app.db")


def get_app_config_path() -> Path:
    return _resolve_runtime_file(APP_CONFIG_PATH_ENV, "app_config.json")


def get_tools_config_path() -> Path:
    return _resolve_runtime_file(TOOLS_CONFIG_PATH_ENV, "tools_config.json")


def get_ast_settings_path() -> Path:
    return _resolve_runtime_file(AST_SETTINGS_PATH_ENV, "ast_settings.json")

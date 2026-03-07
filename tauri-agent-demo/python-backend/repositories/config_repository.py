from typing import List, Optional

from database import db
from models import LLMConfig, LLMConfigCreate, LLMConfigUpdate


def list_configs() -> List[LLMConfig]:
    return db.get_all_configs()


def get_default_config() -> Optional[LLMConfig]:
    return db.get_default_config()


def get_config(config_id: str) -> Optional[LLMConfig]:
    return db.get_config(config_id)


def create_config(config: LLMConfigCreate) -> LLMConfig:
    return db.create_config(config)


def update_config(config_id: str, update: LLMConfigUpdate) -> Optional[LLMConfig]:
    return db.update_config(config_id, update)


def delete_config(config_id: str) -> bool:
    return db.delete_config(config_id)


def config_in_use(config_id: str) -> bool:
    return any(session.config_id == config_id for session in db.get_all_sessions())

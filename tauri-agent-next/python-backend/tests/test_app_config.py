from __future__ import annotations

import json
from pathlib import Path

from app_config import get_app_config, set_app_config_path


def test_get_app_config_merges_file_values(tmp_path: Path) -> None:
    config_path = tmp_path / "app_config.json"
    config_path.write_text(
        json.dumps(
            {
                "llm": {"timeout_sec": 42},
                "transport": {"http": {"port": 8123}},
            }
        ),
        encoding="utf-8",
    )

    set_app_config_path(config_path)
    try:
        config = get_app_config()
    finally:
        set_app_config_path(None)

    assert config["llm"]["timeout_sec"] == 42
    assert config["transport"]["http"]["port"] == 8123
    assert config["transport"]["http"]["host"] == "127.0.0.1"

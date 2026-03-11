from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app_config import get_app_config, set_app_config_path


class AppConfigTests(unittest.TestCase):
    def test_get_app_config_merges_file_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "app_config.json"
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

        self.assertEqual(config["llm"]["timeout_sec"], 42)
        self.assertEqual(config["transport"]["http"]["port"], 8123)
        self.assertEqual(config["transport"]["http"]["host"], "127.0.0.1")

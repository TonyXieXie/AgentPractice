from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app_config import (
    get_app_config,
    get_default_app_config,
    get_runtime_app_config,
    set_app_config_path,
)


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

    def test_default_and_runtime_config_accessors_are_split(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "app_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agent": {
                            "default_profile": "worker",
                            "profiles": {
                                "worker": {"extends": "default"},
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            set_app_config_path(config_path)
            try:
                default_config = get_default_app_config()
                runtime_config = get_runtime_app_config()
            finally:
                set_app_config_path(None)

        self.assertEqual(default_config["agent"]["default_profile"], "default")
        self.assertEqual(runtime_config["agent"]["default_profile"], "worker")
        self.assertEqual(runtime_config["agent"]["profiles"]["worker"]["extends"], "default")

    def test_default_config_includes_builtin_planner_and_coder_profiles(self) -> None:
        config = get_default_app_config()
        profiles = config["agent"]["profiles"]

        self.assertEqual(config["agent"]["react_max_iterations"], 100)
        self.assertIn("planner", profiles)
        self.assertIn("coder", profiles)
        self.assertEqual(profiles["planner"]["agent_type"], "assistant")
        self.assertEqual(profiles["coder"]["agent_type"], "assistant")
        self.assertFalse(profiles["planner"]["editable"])
        self.assertFalse(profiles["coder"]["editable"])

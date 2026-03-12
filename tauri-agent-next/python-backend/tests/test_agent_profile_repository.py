from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app_config import set_app_config_path
from repositories.agent_profile_repository import AgentProfileRepository


class AgentProfileRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self._temp_dir.name) / "app_config.json"

    async def asyncTearDown(self) -> None:
        set_app_config_path(None)
        self._temp_dir.cleanup()

    async def test_resolves_extended_runtime_profile(self) -> None:
        self._write_config(
            {
                "agent": {
                    "default_profile": "worker",
                    "profiles": {
                        "worker": {
                            "extends": "default",
                            "display_name": "Worker",
                            "description": "Worker profile",
                            "system_prompt": "Solve coding tasks.",
                            "tool_policy_text": "Use tools sparingly.",
                            "allowed_tool_names": ["send_event", "echo_profile"],
                            "subscribed_topics": ["workflow.updated"],
                            "executable_event_topics": ["worker.execute"],
                            "metadata": {"role": "worker"},
                        }
                    },
                }
            }
        )
        repository = AgentProfileRepository()

        self.assertEqual(repository.default_profile_id, "worker")
        profile = await repository.get_required("worker")

        self.assertEqual(profile.agent_type, "assistant")
        self.assertEqual(profile.display_name, "Worker")
        self.assertEqual(profile.description, "Worker profile")
        self.assertEqual(profile.system_prompt, "Solve coding tasks.")
        self.assertEqual(profile.tool_policy_text, "Use tools sparingly.")
        self.assertEqual(profile.allowed_tool_names, ["send_event", "echo_profile"])
        self.assertEqual(profile.extends, "default")
        self.assertTrue(profile.editable)
        self.assertEqual(profile.subscribed_topics, ["workflow.updated"])
        self.assertEqual(profile.executable_event_topics, ["worker.execute"])
        self.assertEqual(profile.metadata["role"], "worker")

    async def test_rejects_runtime_override_of_builtin_profile(self) -> None:
        self._write_config(
            {
                "agent": {
                    "profiles": {
                        "default": {
                            "display_name": "Runtime Override",
                        }
                    }
                }
            }
        )
        repository = AgentProfileRepository()

        with self.assertRaisesRegex(ValueError, "must not override built-in profiles"):
            await repository.list_all()

    async def test_rejects_missing_base_profile(self) -> None:
        self._write_config(
            {
                "agent": {
                    "profiles": {
                        "worker": {
                            "extends": "missing",
                        }
                    }
                }
            }
        )
        repository = AgentProfileRepository()

        with self.assertRaisesRegex(ValueError, "Base agent profile not found: missing"):
            await repository.get_required("worker")

    async def test_rejects_inheritance_cycle(self) -> None:
        self._write_config(
            {
                "agent": {
                    "profiles": {
                        "worker": {"extends": "reviewer"},
                        "reviewer": {"extends": "worker"},
                    }
                }
            }
        )
        repository = AgentProfileRepository()

        with self.assertRaisesRegex(ValueError, "inheritance cycle"):
            await repository.list_all()

    async def test_rejects_missing_default_profile(self) -> None:
        self._write_config({"agent": {"default_profile": "missing"}})
        repository = AgentProfileRepository()

        with self.assertRaisesRegex(ValueError, "Default agent profile not found: missing"):
            _ = repository.default_profile_id

    async def test_resolves_builtin_planner_and_coder_profiles(self) -> None:
        repository = AgentProfileRepository()

        planner = await repository.get_required("planner")
        coder = await repository.get_required("coder")

        self.assertEqual(planner.agent_type, "assistant")
        self.assertEqual(coder.agent_type, "assistant")
        self.assertEqual(
            planner.allowed_tool_names,
            ["send_event", "send_rpc_request", "send_rpc_response"],
        )
        self.assertEqual(
            coder.allowed_tool_names,
            ["send_event", "send_rpc_request", "send_rpc_response"],
        )
        self.assertEqual(planner.executable_event_topics, ["task.plan"])
        self.assertEqual(coder.executable_event_topics, ["task.code"])
        self.assertFalse(planner.editable)
        self.assertFalse(coder.editable)


    def _write_config(self, payload: dict) -> None:
        self.config_path.write_text(json.dumps(payload), encoding="utf-8")
        set_app_config_path(self.config_path)

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app_config import set_app_config_path
from agents.execution.handoff_support import (
    render_handoff_catalog_text,
    resolve_handoff_target,
)
from repositories.agent_profile_repository import AgentProfileRepository


class HandoffSupportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self._temp_dir.name) / "app_config.json"

    async def asyncTearDown(self) -> None:
        set_app_config_path(None)
        self._temp_dir.cleanup()

    async def test_render_handoff_catalog_lists_all_assistant_profiles(self) -> None:
        self._write_config(
            {
                "agent": {
                    "profiles": {
                        "reviewer": {
                            "extends": "default",
                            "display_name": "Reviewer",
                            "description": "Review implementation quality.",
                            "executable_event_topics": ["task.review", "task.audit"],
                        },
                        "observer": {
                            "extends": "default",
                            "display_name": "Observer",
                            "description": "Observe only.",
                            "executable_event_topics": [],
                        },
                    }
                }
            }
        )
        repository = AgentProfileRepository()

        text = await render_handoff_catalog_text(
            repository,
            current_profile_id="planner",
        )

        self.assertIn("Low-level RPC and event routing are handled internally by the backend.", text)
        self.assertNotIn("Use send_event", text)
        self.assertNotIn("Use send_rpc_request", text)
        self.assertIn("planner (Planner): current profile; unavailable as handoff target.", text)
        self.assertIn("coder (Coder): handoff-ready via topic `task.code`.", text)
        self.assertIn("reviewer (Reviewer): unavailable; multiple executable event topics;", text)
        self.assertIn("observer (Observer): unavailable; no executable event topic.", text)

    async def test_resolve_handoff_target_validates_target_profile(self) -> None:
        self._write_config(
            {
                "agent": {
                    "profiles": {
                        "reviewer": {
                            "extends": "default",
                            "display_name": "Reviewer",
                            "executable_event_topics": ["task.review", "task.audit"],
                        },
                        "observer": {
                            "extends": "default",
                            "display_name": "Observer",
                            "executable_event_topics": [],
                        },
                    }
                }
            }
        )
        repository = AgentProfileRepository()

        resolved = await resolve_handoff_target(
            repository,
            target_profile="coder",
            current_profile_id="planner",
        )
        self.assertEqual(resolved.profile.id, "coder")
        self.assertEqual(resolved.topic, "task.code")

        with self.assertRaisesRegex(RuntimeError, "handoff target profile not found"):
            await resolve_handoff_target(repository, target_profile="missing")
        with self.assertRaisesRegex(RuntimeError, "must differ from current profile"):
            await resolve_handoff_target(
                repository,
                target_profile="planner",
                current_profile_id="planner",
            )
        with self.assertRaisesRegex(RuntimeError, "no executable event topic"):
            await resolve_handoff_target(repository, target_profile="observer")
        with self.assertRaisesRegex(RuntimeError, "must expose exactly one executable event topic"):
            await resolve_handoff_target(repository, target_profile="reviewer")

    def _write_config(self, payload: dict) -> None:
        self.config_path.write_text(json.dumps(payload), encoding="utf-8")
        set_app_config_path(self.config_path)

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agents.prompt_builder import build_agent_prompt_and_tools


class PromptBuilderTeamPrefixTests(unittest.TestCase):
    def test_selected_team_prefix_is_prepended(self):
        app_config = {
            "agent": {
                "base_system_prompt": "You are a helpful assistant.",
                "default_profile": "planner",
                "profiles": [
                    {
                        "id": "planner",
                        "name": "Planner",
                        "description": "Own planning and delegation across the delivery workflow.",
                        "abilities": [],
                    },
                    {
                        "id": "analyst",
                        "name": "Analyst",
                        "description": "Analyze code paths, produce UML and flowchart artifacts, and flag doc/code mismatches.",
                        "abilities": [],
                    },
                    {
                        "id": "coder",
                        "name": "Coder",
                        "description": "Implement the approved changes in the repository.",
                        "abilities": [],
                    },
                    {
                        "id": "tester",
                        "name": "Tester",
                        "description": "Validate behavior and report regressions.",
                        "abilities": [],
                    },
                ],
                "teams": [
                    {
                        "id": "delivery",
                        "name": "Delivery Team",
                        "leader_profile_id": "planner",
                        "member_profile_ids": ["planner", "analyst", "coder", "tester"],
                    }
                ],
            }
        }

        with patch("agents.prompt_builder.get_app_config", return_value=app_config):
            prompt, tools, resolved_id, ability_ids = build_agent_prompt_and_tools(
                "coder",
                [],
                include_tools=False,
                extra_context={"active_team_id": "delivery"},
            )

        self.assertEqual(tools, [])
        self.assertEqual(resolved_id, "coder")
        self.assertEqual(ability_ids, [])
        self.assertTrue(prompt.startswith("You are [Coder], a team member of Delivery Team."))
        self.assertIn("The leader role is: planner (Planner).", prompt)
        self.assertIn("Your teammates are: planner (Planner), analyst (Analyst), tester (Tester).", prompt)
        self.assertIn("Your responsibility is: Implement the approved changes in the repository.", prompt)
        self.assertIn("Only the leader may decide that the user's overall task is complete.", prompt)
        self.assertIn("do not tell the user the task is fully complete", prompt)
        self.assertIn("work report for the delegating or upstream agent", prompt)
        self.assertIn("explicitly mention the key modified files", prompt)
        self.assertIn("hand off back to the leader with a concise work summary", prompt)
        self.assertIn("You are a helpful assistant.", prompt)

    def test_selected_team_prefix_supports_analyst_specialist(self):
        app_config = {
            "agent": {
                "base_system_prompt": "You are a helpful assistant.",
                "default_profile": "planner",
                "profiles": [
                    {
                        "id": "planner",
                        "name": "Planner",
                        "description": "Own planning and delegation across the delivery workflow.",
                        "abilities": [],
                    },
                    {
                        "id": "analyst",
                        "name": "Analyst",
                        "description": "Analyze code paths, produce UML and flowchart artifacts, and flag doc/code mismatches.",
                        "abilities": [],
                    },
                    {
                        "id": "coder",
                        "name": "Coder",
                        "description": "Implement the approved changes in the repository.",
                        "abilities": [],
                    },
                    {
                        "id": "tester",
                        "name": "Tester",
                        "description": "Validate behavior and report regressions.",
                        "abilities": [],
                    },
                ],
                "teams": [
                    {
                        "id": "delivery",
                        "name": "Delivery Team",
                        "leader_profile_id": "planner",
                        "member_profile_ids": ["planner", "analyst", "coder", "tester"],
                    }
                ],
            }
        }

        with patch("agents.prompt_builder.get_app_config", return_value=app_config):
            prompt, tools, resolved_id, ability_ids = build_agent_prompt_and_tools(
                "analyst",
                [],
                include_tools=False,
                extra_context={"active_team_id": "delivery"},
            )

        self.assertEqual(tools, [])
        self.assertEqual(resolved_id, "analyst")
        self.assertEqual(ability_ids, [])
        self.assertTrue(prompt.startswith("You are [Analyst], a team member of Delivery Team."))
        self.assertIn("The leader role is: planner (Planner).", prompt)
        self.assertIn("Your teammates are: planner (Planner), coder (Coder), tester (Tester).", prompt)
        self.assertIn(
            "Your responsibility is: Analyze code paths, produce UML and flowchart artifacts, and flag doc/code mismatches.",
            prompt,
        )
        self.assertIn("hand off back to the leader with a concise work summary", prompt)

    def test_legacy_team_prefix_uses_generic_name_and_fallback_responsibility(self):
        app_config = {
            "agent": {
                "base_system_prompt": "Base prompt.",
                "default_profile": "planner",
                "profiles": [
                    {"id": "planner", "name": "Planner", "abilities": []},
                    {"id": "coder", "name": "Coder", "abilities": []},
                ],
                "team": {
                    "default_agent": "planner",
                    "members": [
                        {"profile_id": "planner", "handoff_to": ["coder"]},
                        {"profile_id": "coder", "handoff_to": ["planner"]},
                    ],
                },
            }
        }

        with patch("agents.prompt_builder.get_app_config", return_value=app_config):
            prompt, _, resolved_id, _ = build_agent_prompt_and_tools(
                "planner",
                [],
                include_tools=False,
            )

        self.assertEqual(resolved_id, "planner")
        self.assertTrue(prompt.startswith("You are [Planner], a team member of the configured team."))
        self.assertIn("The leader role is: planner (Planner).", prompt)
        self.assertIn("Your teammates are: coder (Coder).", prompt)
        self.assertIn("Your responsibility is: Act as the Planner specialist for the team.", prompt)
        self.assertIn("Only the leader may decide that the user's overall task is complete.", prompt)
        self.assertIn("do not tell the user the task is fully complete", prompt)
        self.assertIn("explicitly mention the key modified files", prompt)
        self.assertIn("decide whether to ask the user directly or send a follow-up task", prompt)
        self.assertIn("You may hand work back to the same teammate", prompt)
        self.assertIn("Do not output raw handoff event logs", prompt)
        self.assertIn("Base prompt.", prompt)


if __name__ == "__main__":
    unittest.main()

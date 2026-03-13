from __future__ import annotations

import unittest

from llm.client import LLMClient
from models import LLMConfig


class LLMClientToolCallTests(unittest.TestCase):
    def test_extract_chat_tool_call_delta_uses_arguments_as_delta_only(self) -> None:
        client = LLMClient(
            LLMConfig(
                name="test",
                api_key="test-key",
                model="gpt-5.2",
            )
        )

        event = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "function": {
                                    "name": "finish_run",
                                    "arguments": '{"reply":"done"}',
                                },
                            }
                        ]
                    }
                }
            ]
        }

        payload = client._extract_chat_tool_call_delta(event)

        self.assertEqual(
            payload,
            {
                "type": "tool_call_delta",
                "index": 0,
                "id": "call-1",
                "name": "finish_run",
                "arguments_delta": '{"reply":"done"}',
            },
        )

from __future__ import annotations

import unittest

from agents.execution.prompt_ir import PromptIR
from llm.request_body_builder import RequestBodyBuilder
from models import LLMConfig


def build_config(**overrides) -> LLMConfig:
    payload = {
        "name": "test-config",
        "api_key": "test-key",
        "model": "gpt-4.1-mini",
        "api_format": "openai_chat_completions",
        "api_profile": "openai",
        "temperature": 0.3,
        "max_tokens": 512,
        "max_context_tokens": 8192,
    }
    payload.update(overrides)
    return LLMConfig.model_validate(payload)


class RequestBodyBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = RequestBodyBuilder()

    def test_build_chat_completions_payload_from_prompt_ir(self) -> None:
        prompt_ir = PromptIR(
            messages=[
                {"role": "developer", "content": "system prompt"},
                {"role": "user", "content": "hello"},
            ],
            budget={},
            trace={},
        )
        config = build_config()

        path, payload = self.builder.build(
            config=config,
            prompt_ir=prompt_ir,
            request_overrides={"top_p": 0.9},
            stream=False,
        )

        self.assertEqual(path, "/chat/completions")
        self.assertEqual(payload["model"], config.model)
        self.assertEqual(payload["messages"], prompt_ir.messages)
        self.assertEqual(payload["temperature"], 0.3)
        self.assertEqual(payload["max_tokens"], 512)
        self.assertEqual(payload["top_p"], 0.9)

    def test_build_responses_payload_from_prompt_ir(self) -> None:
        prompt_ir = PromptIR(
            messages=[
                {"role": "developer", "content": "system prompt"},
                {"role": "user", "content": "hello"},
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "echo",
                    "arguments": "{\"text\":\"hello\"}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": "hello",
                },
            ],
            budget={},
            trace={},
        )
        config = build_config(api_format="openai_responses")

        path, payload = self.builder.build(
            config=config,
            prompt_ir=prompt_ir,
            request_overrides=None,
            stream=True,
        )

        self.assertEqual(path, "/responses")
        self.assertEqual(payload["model"], config.model)
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["max_output_tokens"], 512)
        self.assertEqual(payload["input"][0]["type"], "message")
        self.assertEqual(payload["input"][0]["role"], "developer")
        self.assertEqual(
            payload["input"][0]["content"][0],
            {"type": "input_text", "text": "system prompt"},
        )
        self.assertEqual(payload["input"][2]["type"], "function_call")
        self.assertEqual(payload["input"][3]["type"], "function_call_output")

    def test_reasoning_params_and_override_merge_do_not_regress(self) -> None:
        prompt_ir = PromptIR(messages=[{"role": "user", "content": "hello"}], budget={}, trace={})
        config = build_config(model="gpt-5-mini")

        path, payload = self.builder.build(
            config=config,
            prompt_ir=prompt_ir,
            request_overrides={
                "max_tokens": 111,
                "metadata": {"run_id": "run-1"},
                "_private": "skip-me",
            },
            stream=True,
        )

        self.assertEqual(path, "/chat/completions")
        self.assertTrue(payload["stream"])
        self.assertNotIn("temperature", payload)
        self.assertEqual(payload["max_tokens"], 111)
        self.assertEqual(payload["reasoning"]["effort"], "medium")
        self.assertEqual(payload["reasoning"]["summary"], "detailed")
        self.assertEqual(payload["metadata"], {"run_id": "run-1"})
        self.assertNotIn("_private", payload)

import os
import sys
import unittest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app_config import DEFAULT_GRAPH_ID, _normalize_graphs  # noqa: E402
from graph_runtime.expression import (  # noqa: E402
    ExpressionSyntaxError,
    evaluate_edge_expression,
    validate_edge_expression,
)


class EdgeExpressionTests(unittest.TestCase):
    def test_evaluates_boolean_comparisons(self) -> None:
        context = {
            "state": {
                "count": 3,
                "flag": True,
                "nested": {"value": "ok"},
            },
            "result": {
                "status": "completed",
                "output": 5,
            },
        }

        self.assertTrue(
            evaluate_edge_expression(
                "state.count >= 3 and result.status == 'completed'",
                context,
            )
        )
        self.assertTrue(
            evaluate_edge_expression(
                "not state.missing and state.nested.value == 'ok'",
                context,
            )
        )
        self.assertFalse(
            evaluate_edge_expression(
                "result.output < 5 or state.flag == false",
                context,
            )
        )

    def test_missing_paths_and_type_mismatch_are_safe(self) -> None:
        context = {
            "state": {"count": "3"},
            "result": {},
        }

        self.assertFalse(evaluate_edge_expression("state.missing > 0", context))
        self.assertTrue(evaluate_edge_expression("state.missing != 0", context))
        self.assertFalse(evaluate_edge_expression("state.count > 1", context))

    def test_invalid_expressions_are_rejected(self) -> None:
        with self.assertRaises(ExpressionSyntaxError):
            validate_edge_expression("__import__('os')")

        with self.assertRaises(ExpressionSyntaxError):
            validate_edge_expression("session.id == 'abc'")


class GraphConfigNormalizationTests(unittest.TestCase):
    def test_empty_graph_list_falls_back_to_default_graph(self) -> None:
        normalized = _normalize_graphs({})

        self.assertEqual(normalized["default_graph_id"], DEFAULT_GRAPH_ID)
        self.assertEqual(len(normalized["graphs"]), 1)
        self.assertEqual(normalized["graphs"][0]["id"], DEFAULT_GRAPH_ID)

    def test_duplicate_fallback_edges_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "fallback edge"):
            _normalize_graphs(
                {
                    "graphs": [
                        {
                            "id": "bad_fallbacks",
                            "name": "Bad Fallbacks",
                            "initial_state": {},
                            "nodes": [
                                {"id": "node_a", "type": "router"},
                                {"id": "node_b", "type": "router"},
                            ],
                            "edges": [
                                {"source": "__start__", "target": "node_a"},
                                {"source": "node_a", "target": "node_b"},
                                {"source": "node_a", "target": "__end__"},
                                {"source": "node_b", "target": "__end__"},
                            ],
                        }
                    ],
                    "default_graph_id": "bad_fallbacks",
                }
            )

    def test_graph_must_have_path_to_end(self) -> None:
        with self.assertRaisesRegex(ValueError, "path from __start__ to __end__"):
            _normalize_graphs(
                {
                    "graphs": [
                        {
                            "id": "no_end",
                            "name": "No End",
                            "initial_state": {},
                            "nodes": [
                                {"id": "loop", "type": "router"},
                            ],
                            "edges": [
                                {"source": "__start__", "target": "loop"},
                                {"source": "loop", "target": "loop", "condition": "true"},
                            ],
                        }
                    ],
                    "default_graph_id": "no_end",
                }
            )

    def test_state_schema_mutable_defaults_false(self) -> None:
        normalized = _normalize_graphs(
            {
                "graphs": [
                    {
                        "id": "mutable_graph",
                        "name": "Mutable Graph",
                        "initial_state": {"plan": {"task": None}},
                        "state_schema": [
                            {"path": "plan.task", "type": "string"},
                            {"path": "plan.ready", "type": "boolean", "mutable": True},
                        ],
                        "nodes": [
                            {"id": "main", "type": "react_agent"},
                        ],
                        "edges": [
                            {"source": "__start__", "target": "main"},
                            {"source": "main", "target": "__end__"},
                        ],
                    }
                ],
                "default_graph_id": "mutable_graph",
            }
        )

        state_schema = normalized["graphs"][0]["state_schema"]
        self.assertFalse(state_schema[0]["mutable"])
        self.assertTrue(state_schema[1]["mutable"])

    def test_reserved_runtime_paths_are_rejected(self) -> None:
        invalid_configs = [
            (
                {
                    "graphs": [
                        {
                            "id": "bad_initial_state",
                            "name": "Bad Initial State",
                            "initial_state": {"input": {"user_message": "hello"}},
                            "nodes": [{"id": "main", "type": "react_agent"}],
                            "edges": [
                                {"source": "__start__", "target": "main"},
                                {"source": "main", "target": "__end__"},
                            ],
                        }
                    ],
                    "default_graph_id": "bad_initial_state",
                },
                "reserved runtime state root",
            ),
            (
                {
                    "graphs": [
                        {
                            "id": "bad_schema",
                            "name": "Bad Schema",
                            "initial_state": {},
                            "state_schema": [{"path": "messages", "type": "array", "mutable": True}],
                            "nodes": [{"id": "main", "type": "react_agent"}],
                            "edges": [
                                {"source": "__start__", "target": "main"},
                                {"source": "main", "target": "__end__"},
                            ],
                        }
                    ],
                    "default_graph_id": "bad_schema",
                },
                "reserved runtime state path",
            ),
            (
                {
                    "graphs": [
                        {
                            "id": "bad_output_path",
                            "name": "Bad Output Path",
                            "initial_state": {},
                            "nodes": [
                                {
                                    "id": "main",
                                    "type": "react_agent",
                                    "output_path": "input.user_message",
                                }
                            ],
                            "edges": [
                                {"source": "__start__", "target": "main"},
                                {"source": "main", "target": "__end__"},
                            ],
                        }
                    ],
                    "default_graph_id": "bad_output_path",
                },
                "output_path cannot target reserved runtime state path",
            ),
        ]

        for value, error_text in invalid_configs:
            with self.subTest(error_text=error_text):
                with self.assertRaisesRegex(ValueError, error_text):
                    _normalize_graphs(value)

    def test_direct_input_templates_are_rejected(self) -> None:
        invalid_templates = [
            (
                {
                    "graphs": [
                        {
                            "id": "bad_input_template",
                            "name": "Bad Input Template",
                            "initial_state": {},
                            "nodes": [
                                {"id": "main", "type": "react_agent", "input_template": "{{input}}"},
                            ],
                            "edges": [
                                {"source": "__start__", "target": "main"},
                                {"source": "main", "target": "__end__"},
                            ],
                        }
                    ],
                    "default_graph_id": "bad_input_template",
                },
                "only supports template roots",
            ),
            (
                {
                    "graphs": [
                        {
                            "id": "bad_request_template",
                            "name": "Bad Request Template",
                            "initial_state": {},
                            "nodes": [
                                {
                                    "id": "main",
                                    "type": "tool_call",
                                    "tool_name": "echo",
                                    "args_template": {"text": "{{request.message}}"},
                                },
                            ],
                            "edges": [
                                {"source": "__start__", "target": "main"},
                                {"source": "main", "target": "__end__"},
                            ],
                        }
                    ],
                    "default_graph_id": "bad_request_template",
                },
                "only supports template roots",
            ),
        ]

        for value, error_text in invalid_templates:
            with self.subTest(error_text=error_text):
                with self.assertRaisesRegex(ValueError, error_text):
                    _normalize_graphs(value)

    def test_graph_ui_metadata_is_preserved(self) -> None:
        normalized = _normalize_graphs(
            {
                "graphs": [
                    {
                        "id": "ui_graph",
                        "name": "UI Graph",
                        "initial_state": {},
                        "ui": {
                            "viewport": {"x": 12, "y": 34, "zoom": 0.9},
                        },
                        "nodes": [
                            {
                                "id": "main",
                                "type": "react_agent",
                                "ui": {
                                    "position": {"x": 120, "y": 240},
                                },
                            }
                        ],
                        "edges": [
                            {"source": "__start__", "target": "main"},
                            {"source": "main", "target": "__end__"},
                        ],
                    }
                ],
                "default_graph_id": "ui_graph",
            }
        )

        graph = normalized["graphs"][0]
        self.assertEqual(graph["ui"]["viewport"]["x"], 12.0)
        self.assertEqual(graph["ui"]["viewport"]["zoom"], 0.9)
        self.assertEqual(graph["nodes"][0]["ui"]["position"]["y"], 240.0)


if __name__ == "__main__":
    unittest.main()

from .expression import evaluate_edge_expression, parse_edge_expression, validate_edge_expression

__all__ = [
    "GRAPH_END",
    "GRAPH_START",
    "GraphRunner",
    "evaluate_edge_expression",
    "get_graph_definition",
    "parse_edge_expression",
    "resolve_graph_id",
    "validate_edge_expression",
]


def __getattr__(name):
    if name in {"GRAPH_END", "GRAPH_START", "GraphRunner", "get_graph_definition", "resolve_graph_id"}:
        from .runtime import GRAPH_END, GRAPH_START, GraphRunner, get_graph_definition, resolve_graph_id

        exports = {
            "GRAPH_END": GRAPH_END,
            "GRAPH_START": GRAPH_START,
            "GraphRunner": GraphRunner,
            "get_graph_definition": get_graph_definition,
            "resolve_graph_id": resolve_graph_id,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

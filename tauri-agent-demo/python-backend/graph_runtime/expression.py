import ast
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple


_TOKEN_PATTERN = re.compile(
    r"""
    \s*(
        (?P<number>-?\d+(?:\.\d+)?)
        |(?P<string>"(?:\\.|[^"])*"|'(?:\\.|[^'])*')
        |(?P<op>==|!=|>=|<=|>|<|\(|\)|\.)
        |(?P<ident>[A-Za-z_][A-Za-z0-9_]*)
        |(?P<mismatch>.)
    )
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class Token:
    kind: str
    value: str


class ExpressionSyntaxError(ValueError):
    pass


class _MissingValue:
    pass


MISSING = _MissingValue()


def _tokenize(expression: str) -> List[Token]:
    tokens: List[Token] = []
    position = 0
    while position < len(expression):
        match = _TOKEN_PATTERN.match(expression, position)
        if not match:
            raise ExpressionSyntaxError(f"Unexpected token near position {position}")
        position = match.end()
        if match.group("number") is not None:
            tokens.append(Token("number", match.group("number")))
        elif match.group("string") is not None:
            tokens.append(Token("string", match.group("string")))
        elif match.group("op") is not None:
            tokens.append(Token("op", match.group("op")))
        elif match.group("ident") is not None:
            tokens.append(Token("ident", match.group("ident")))
        elif match.group("mismatch") is not None:
            raise ExpressionSyntaxError(f"Unexpected token '{match.group('mismatch')}'")
    return tokens


class _Parser:
    def __init__(self, tokens: List[Token]) -> None:
        self.tokens = tokens
        self.index = 0

    def _peek(self) -> Optional[Token]:
        if self.index >= len(self.tokens):
            return None
        return self.tokens[self.index]

    def _consume(self) -> Token:
        token = self._peek()
        if token is None:
            raise ExpressionSyntaxError("Unexpected end of expression")
        self.index += 1
        return token

    def _match_ident(self, value: str) -> bool:
        token = self._peek()
        if token and token.kind == "ident" and token.value == value:
            self.index += 1
            return True
        return False

    def _match_op(self, value: str) -> bool:
        token = self._peek()
        if token and token.kind == "op" and token.value == value:
            self.index += 1
            return True
        return False

    def parse(self) -> Tuple[Any, ...]:
        expression = self._parse_or()
        if self._peek() is not None:
            raise ExpressionSyntaxError(f"Unexpected token '{self._peek().value}'")
        return expression

    def _parse_or(self) -> Tuple[Any, ...]:
        left = self._parse_and()
        while self._match_ident("or"):
            right = self._parse_and()
            left = ("or", left, right)
        return left

    def _parse_and(self) -> Tuple[Any, ...]:
        left = self._parse_not()
        while self._match_ident("and"):
            right = self._parse_not()
            left = ("and", left, right)
        return left

    def _parse_not(self) -> Tuple[Any, ...]:
        if self._match_ident("not"):
            return ("not", self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> Tuple[Any, ...]:
        left = self._parse_primary()
        token = self._peek()
        if token and token.kind == "op" and token.value in ("==", "!=", ">", ">=", "<", "<="):
            operator = token.value
            self.index += 1
            right = self._parse_primary()
            return ("compare", operator, left, right)
        return left

    def _parse_primary(self) -> Tuple[Any, ...]:
        token = self._peek()
        if token is None:
            raise ExpressionSyntaxError("Unexpected end of expression")

        if token.kind == "op" and token.value == "(":
            self.index += 1
            expression = self._parse_or()
            if not self._match_op(")"):
                raise ExpressionSyntaxError("Missing closing ')'")
            return expression

        if token.kind == "number":
            self.index += 1
            if "." in token.value:
                return ("literal", float(token.value))
            return ("literal", int(token.value))

        if token.kind == "string":
            self.index += 1
            return ("literal", ast.literal_eval(token.value))

        if token.kind == "ident":
            if token.value == "true":
                self.index += 1
                return ("literal", True)
            if token.value == "false":
                self.index += 1
                return ("literal", False)
            if token.value == "null":
                self.index += 1
                return ("literal", None)
            return self._parse_path()

        raise ExpressionSyntaxError(f"Unexpected token '{token.value}'")

    def _parse_path(self) -> Tuple[Any, ...]:
        first = self._consume()
        if first.kind != "ident":
            raise ExpressionSyntaxError("Expected identifier")
        if first.value not in ("state", "result"):
            raise ExpressionSyntaxError("Path root must be 'state' or 'result'")
        parts = [first.value]
        while self._match_op("."):
            token = self._consume()
            if token.kind not in ("ident", "number"):
                raise ExpressionSyntaxError("Expected path segment after '.'")
            parts.append(token.value)
        return ("path", parts)


@lru_cache(maxsize=256)
def parse_edge_expression(expression: str) -> Tuple[Any, ...]:
    if not isinstance(expression, str):
        raise ExpressionSyntaxError("Expression must be a string")
    normalized = expression.strip()
    if not normalized:
        raise ExpressionSyntaxError("Expression must not be empty")
    parser = _Parser(_tokenize(normalized))
    return parser.parse()


def validate_edge_expression(expression: str) -> None:
    parse_edge_expression(expression)


def _resolve_path(parts: List[str], context: Dict[str, Any]) -> Any:
    current: Any = context
    for part in parts:
        if isinstance(current, dict):
            if part not in current:
                return MISSING
            current = current.get(part)
            continue
        if isinstance(current, list):
            try:
                index = int(part)
            except (TypeError, ValueError):
                return MISSING
            if index < 0 or index >= len(current):
                return MISSING
            current = current[index]
            continue
        return MISSING
    return current


def _evaluate_ast(node: Tuple[Any, ...], context: Dict[str, Any]) -> Any:
    kind = node[0]
    if kind == "literal":
        return node[1]
    if kind == "path":
        return _resolve_path(node[1], context)
    if kind == "not":
        return not _coerce_truthy(_evaluate_ast(node[1], context))
    if kind == "and":
        return _coerce_truthy(_evaluate_ast(node[1], context)) and _coerce_truthy(_evaluate_ast(node[2], context))
    if kind == "or":
        return _coerce_truthy(_evaluate_ast(node[1], context)) or _coerce_truthy(_evaluate_ast(node[2], context))
    if kind == "compare":
        left = _evaluate_ast(node[2], context)
        right = _evaluate_ast(node[3], context)
        operator = node[1]
        return _compare_values(left, right, operator)
    raise ExpressionSyntaxError(f"Unsupported AST node '{kind}'")


def _coerce_truthy(value: Any) -> bool:
    if value is MISSING:
        return False
    return bool(value)


def _compare_values(left: Any, right: Any, operator: str) -> bool:
    if left is MISSING or right is MISSING:
        if operator == "==":
            return False
        if operator == "!=":
            return True
        return False
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    if type(left) != type(right) and not (
        isinstance(left, (int, float)) and isinstance(right, (int, float))
    ):
        return False
    try:
        if operator == ">":
            return left > right
        if operator == ">=":
            return left >= right
        if operator == "<":
            return left < right
        if operator == "<=":
            return left <= right
    except TypeError:
        return False
    raise ExpressionSyntaxError(f"Unsupported comparison operator '{operator}'")


def evaluate_edge_expression(expression: str, context: Dict[str, Any]) -> bool:
    ast_node = parse_edge_expression(expression)
    evaluation_context = {
        "state": context.get("state"),
        "result": context.get("result"),
    }
    return _coerce_truthy(_evaluate_ast(ast_node, evaluation_context))

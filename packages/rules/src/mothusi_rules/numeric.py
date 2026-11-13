"""Compare mathematical expressions without executing learner-supplied Python."""

import ast
import re
import tokenize
from importlib import import_module
from io import StringIO
from typing import Any

# Sympy has no complete typing surface. Keep that boundary inside this parser;
# the public contract is strings in and a boolean (or ValueError) out.
_algebra = import_module("sympy")
_parser = import_module("sympy.parsing.sympy_parser")
_FUNCTIONS = ("sqrt", "sin", "cos", "tan", "exp", "log", "Abs")


MAX_LENGTH = 500  # characters, after notation is made explicit
MAX_NODES = 200  # syntax-tree nodes
MAX_EXPONENT = 100  # |constant exponent|; sympy evaluates powers eagerly, so 9**9**9 never returns


def _fold(node: ast.expr) -> float | None:
    """The value of a constant-only arithmetic subtree (+ - * / and unary signs), else None."""
    if isinstance(node, ast.Constant):
        return float(node.value) if isinstance(node.value, int | float) else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        inner = _fold(node.operand)
        return None if inner is None else (-inner if isinstance(node.op, ast.USub) else inner)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Sub | ast.Mult | ast.Div):
        left, right = _fold(node.left), _fold(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        return left / right if right else float("inf")
    return None


def _check_exponent(node: ast.expr) -> None:
    """An exponent may hold symbols, small constants and + - * /; never a nested power or a call.

    Every constant-only subtree is folded and bounded, so 100*100*100*100*100 is refused as
    surely as a literal 10**10 would be: sympy evaluates powers eagerly.
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Pow):
            raise ValueError("exponent too complex")
        if isinstance(sub, ast.Call):
            raise ValueError("exponent too complex")
        if isinstance(sub, ast.Constant) and not isinstance(sub.value, int | float):
            raise ValueError("exponent too complex")
        if isinstance(sub, ast.Constant | ast.UnaryOp | ast.BinOp):
            value = _fold(sub)
            if value is not None and abs(value) > MAX_EXPONENT:
                raise ValueError("exponent too large")


def _expression(text: str) -> Any:
    # Make the school notation in the contract explicit before parse_expr;
    # implicit-multiplication transformations themselves remain disabled.
    tokens = list(tokenize.generate_tokens(StringIO(text).readline))
    explicit: list[tuple[int, str]] = []
    previous: tokenize.TokenInfo | None = None
    for token in tokens:
        if previous is not None and (
            (previous.type == tokenize.NUMBER and token.type == tokenize.NAME)
            or (
                previous.string == ")"
                and (token.type in (tokenize.NAME, tokenize.NUMBER) or token.string == "(")
            )
            or (
                previous.type == tokenize.NAME and len(previous.string) == 1 and token.string == "("
            )
        ):
            explicit.append((tokenize.OP, "*"))
        explicit.append((token.type, token.string))
        previous = token
    text = tokenize.untokenize(explicit)
    if len(text) > MAX_LENGTH:
        raise ValueError("expression too long")
    tree = ast.parse(text, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ValueError("expression too complex")
    symbols: dict[str, Any] = {name: getattr(_algebra, name) for name in _FUNCTIONS}
    symbols.update({"pi": _algebra.pi, "E": _algebra.E, "I": _algebra.I})
    allowed = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.Load,
        ast.Name,
        ast.Call,
        ast.Constant,
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise ValueError("unsupported mathematical syntax")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            _check_exponent(node.right)
        if isinstance(node, ast.Constant) and type(node.value) not in (int, float):
            raise ValueError("only numeric constants are allowed")
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS or node.keywords
        ):
            raise ValueError("unsupported mathematical function")
        if isinstance(node, ast.Name) and node.id not in symbols:
            if node.id.startswith("_"):
                raise ValueError("invalid symbol")
            symbols[node.id] = _algebra.Symbol(node.id)
    globals_ = {name: getattr(_algebra, name) for name in ("Integer", "Float", "Rational")}
    globals_["__builtins__"] = {}
    result = _parser.parse_expr(
        text,
        local_dict=symbols,
        global_dict=globals_,
        transformations=(*_parser.standard_transformations, _parser.rationalize),
    )
    if not isinstance(result, _algebra.Expr) or result.has(
        _algebra.zoo, _algebra.nan, _algebra.oo, -_algebra.oo
    ):
        raise ValueError("expression is not finite")
    return result


_INEQUALITY = re.compile(r"(<=|>=|<|>|≤|≥)")
_CONJUNCTION = re.compile(r"\band\b|,|;")
_SOLUTION = re.compile(r"^\s*([A-Za-z]\w*)\s*=\s*([^=]+?)\s*$")


def _parse(text: str) -> tuple[str, Any]:
    """One side, classified: ("set", [values]) | ("system", {var: value}) |
    ("ineq", (op, residual)) | ("expr", value)."""
    text = " ".join(text.split()).translate(
        str.maketrans({"\u00d7": "*", "\u00f7": "/", "\u2212": "-", "\u2264": "<=", "\u2265": ">="})
    )
    text = text.replace("^", "**").replace("√(", "sqrt(")
    if "∈" in text:
        variable, text = text.split("∈", 1)
        if not re.fullmatch(r"\s*[A-Za-z]\w*\s*", variable):
            raise ValueError("invalid solution variable")
        text = text.strip()
        if not text.startswith("{"):
            raise ValueError("expected a solution set")
    if text.startswith("{"):
        if not text.endswith("}"):
            raise ValueError("unclosed solution set")
        body = text[1:-1].strip()
        return "set", [_expression(value.strip()) for value in body.split(",")] if body else []
    if _INEQUALITY.search(text):
        parts = _INEQUALITY.split(text)
        if len(parts) != 3:
            raise ValueError("one inequality at a time")
        lhs, op, rhs = (part.strip() for part in parts)
        left, right = _expression(lhs), _expression(rhs)
        if op in (">", ">="):  # read right to left so every inequality points the same way
            left, right, op = right, left, {">": "<", ">=": "<="}[op]
        return "ineq", (op, left - right)
    if _CONJUNCTION.search(text) or " or " in f" {text} ":
        equations = [part.strip() for part in re.split(r"\bor\b|\band\b|,|;", text) if part.strip()]
        alternatives = bool(re.search(r"\bor\b", text))
        solved: dict[str, Any] = {}
        for equation in equations:
            match = _SOLUTION.match(equation)
            if match is None:
                raise ValueError("solution equations must read <variable> = <value>")
            variable, value = match.groups()
            solved.setdefault(variable, []).append(_expression(value))
        conjunction = bool(re.search(r"\band\b", text))
        if len(solved) == 1:  # one variable, several values: a solution set ("or", ";" or ",")
            values = next(iter(solved.values()))
            if conjunction and len(values) > 1:
                raise ValueError(
                    "'and' between values of one variable is a contradiction, not a set"
                )
            return "set", values
        if alternatives:  # "x = 3 or y = 2" is not a solution of anything
            raise ValueError("alternatives must use one variable")
        if any(len(values) != 1 for values in solved.values()):
            raise ValueError("a system gives one value per variable")
        return "system", {variable: values[0] for variable, values in solved.items()}
    if "=" in text:
        lhs, rhs = text.split("=")
        return "expr", _expression(lhs.strip()) - _expression(rhs.strip())
    return "expr", _expression(text)


def _same(first: Any, second: Any) -> bool:
    difference = _algebra.simplify(first - second)
    if difference.free_symbols:
        return bool(difference == 0)
    return bool(abs(difference) <= _algebra.Rational(1, 10**9))


def equivalent(a: str, b: str) -> bool:
    """Compare expressions, equation residuals, inequalities, systems or finite solution sets."""
    parsed: list[tuple[str, Any]] = []
    for side, text in (("a", a), ("b", b)):
        try:
            parsed.append(_parse(text))
        except Exception as error:
            raise ValueError(f"cannot parse side {side}: {text!r}") from error
    (first_kind, first), (second_kind, second) = parsed
    if first_kind != second_kind:
        return False
    if first_kind == "expr":
        return _same(first, second)
    if first_kind == "ineq":
        return first[0] == second[0] and _same(first[1], second[1])
    if first_kind == "system":
        return set(first) == set(second) and all(_same(first[k], second[k]) for k in first)
    return all(any(_same(value, other) for other in second) for value in first) and all(
        any(_same(value, other) for other in first) for value in second
    )

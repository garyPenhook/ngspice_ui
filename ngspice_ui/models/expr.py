"""Safe expression evaluator for user-supplied measurement / derived-trace formulas.

Only numeric, numpy, and whitelisted built-in operations are permitted.
Dunder attribute access and any name outside the whitelist raise ValueError.
"""
from __future__ import annotations

import ast

_WHITELISTED_NAMES: frozenset[str] = frozenset({
    "np", "math", "abs", "round", "min", "max", "sum", "len",
    "float", "int", "bool", "vec",
    "True", "False", "None",
})

_SAFE_NODES = (
    ast.Expression, ast.Constant,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.IfExp, ast.List, ast.Tuple,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.MatMult,
    ast.USub, ast.UAdd, ast.Invert, ast.Not,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
    ast.And, ast.Or,
    ast.Subscript, ast.Index, ast.Slice,
    ast.Load, ast.Store, ast.Del,
    ast.keyword,
)


def _validate(node: ast.AST) -> None:
    if isinstance(node, ast.Name):
        if node.id not in _WHITELISTED_NAMES:
            raise ValueError(f"name not allowed: {node.id!r}")
    elif isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise ValueError(f"private attribute access not allowed: {node.attr!r}")
        _validate(node.value)
    elif isinstance(node, ast.Call):
        _validate(node.func)
        for arg in node.args:
            _validate(arg)
        for kw in node.keywords:
            _validate(kw.value)
    elif isinstance(node, _SAFE_NODES):
        for child in ast.iter_child_nodes(node):
            _validate(child)
    else:
        raise ValueError(f"construct not allowed: {type(node).__name__!r}")


def safe_eval(expr: str, ns: dict) -> object:
    """Parse, validate, then eval *expr* against *ns*.

    Raises ValueError for any disallowed construct before eval is called.
    The caller must populate *ns* with all whitelisted names (np, math, vec, …).
    """
    tree = ast.parse(expr, mode="eval")
    _validate(tree)
    return eval(compile(tree, "<expr>", "eval"), ns)  # noqa: S307

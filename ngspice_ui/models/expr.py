"""Safe expression evaluator for user-supplied measurement / derived-trace formulas.

Only numeric, numpy, and whitelisted built-in operations are permitted.
Dunder attribute access and any name outside the whitelist raise ValueError.
"""

from __future__ import annotations

import ast

_WHITELISTED_NAMES: frozenset[str] = frozenset(
    {
        "np",
        "math",
        "abs",
        "round",
        "min",
        "max",
        "sum",
        "len",
        "float",
        "int",
        "bool",
        "vec",
        "True",
        "False",
        "None",
    }
)

# Explicit allowlist for numpy attributes.  This blocks file I/O (savetxt, save,
# loadtxt, …), submodule namespaces (fft, random, …), and arbitrary function
# application (vectorize, frompyfunc, apply_along_axis, …).  Use np.real(x)
# rather than x.real for array methods — direct Name access only.
_SAFE_NP_ATTRS: frozenset[str] = frozenset(
    {
        "array",
        "asarray",
        "zeros",
        "ones",
        "empty",
        "zeros_like",
        "ones_like",
        "full",
        "linspace",
        "arange",
        "abs",
        "absolute",
        "fabs",
        "sqrt",
        "square",
        "cbrt",
        "exp",
        "expm1",
        "exp2",
        "log",
        "log2",
        "log10",
        "log1p",
        "sin",
        "cos",
        "tan",
        "arcsin",
        "arccos",
        "arctan",
        "arctan2",
        "sinh",
        "cosh",
        "tanh",
        "arcsinh",
        "arccosh",
        "arctanh",
        "floor",
        "ceil",
        "round",
        "trunc",
        "rint",
        "clip",
        "sign",
        "max",
        "min",
        "mean",
        "median",
        "std",
        "var",
        "sum",
        "prod",
        "nanmax",
        "nanmin",
        "nanmean",
        "nanmedian",
        "nanstd",
        "nanvar",
        "nansum",
        "ptp",
        "percentile",
        "quantile",
        "concatenate",
        "stack",
        "hstack",
        "vstack",
        "append",
        "diff",
        "gradient",
        "cumsum",
        "cumprod",
        "sort",
        "argsort",
        "argmax",
        "argmin",
        "nonzero",
        "where",
        "unique",
        "interp",
        "dot",
        "cross",
        "inner",
        "outer",
        "real",
        "imag",
        "angle",
        "conj",
        "unwrap",
        "pi",
        "e",
        "inf",
        "nan",
        "newaxis",
        "float64",
        "float32",
        "complex128",
        "complex64",
        "int64",
        "int32",
        "bool_",
        "ndarray",
    }
)

_SAFE_NODES = (
    ast.Expression,
    ast.Constant,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.List,
    ast.Tuple,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.MatMult,
    ast.USub,
    ast.UAdd,
    ast.Invert,
    ast.Not,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Eq,
    ast.NotEq,
    ast.And,
    ast.Or,
    ast.Subscript,
    ast.Index,
    ast.Slice,
    ast.Load,
    ast.Store,
    ast.Del,
    ast.keyword,
)


def _validate(node: ast.AST, allowed: frozenset[str]) -> None:
    if isinstance(node, ast.Name):
        if node.id not in allowed:
            raise ValueError(f"name not allowed: {node.id!r}")
    elif isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise ValueError(f"private attribute access not allowed: {node.attr!r}")
        # Only allow attribute access directly on a whitelisted name, never on a
        # computed value (blocks array.tofile(...) and np.fft.rfft(...) alike).
        if not isinstance(node.value, ast.Name):
            raise ValueError(f"attribute access on computed values not allowed: {node.attr!r}")
        if node.value.id == "np" and node.attr not in _SAFE_NP_ATTRS:
            raise ValueError(f"numpy attribute not allowed: {node.attr!r}")
        _validate(node.value, allowed)
    elif isinstance(node, ast.Call):
        _validate(node.func, allowed)
        for arg in node.args:
            _validate(arg, allowed)
        for kw in node.keywords:
            _validate(kw.value, allowed)
    elif isinstance(node, _SAFE_NODES):
        for child in ast.iter_child_nodes(node):
            _validate(child, allowed)
    else:
        raise ValueError(f"construct not allowed: {type(node).__name__!r}")


def validate_expr(expr: str, extra_names: frozenset[str] = frozenset()) -> ast.AST:
    """Parse *expr* and validate it against the whitelist plus *extra_names*.

    Returns the parsed AST (an :class:`ast.Expression`) so callers can compile
    it without re-parsing. Raises ValueError for any disallowed construct.
    """
    tree = ast.parse(expr, mode="eval")
    _validate(tree, _WHITELISTED_NAMES | extra_names)
    return tree


def safe_eval(expr: str, ns: dict, extra_names: frozenset[str] = frozenset()) -> object:
    """Parse, validate, then eval *expr* against *ns*.

    Raises ValueError for any disallowed construct before eval is called.
    The caller must populate *ns* with all whitelisted names (np, math, vec, …).
    *extra_names* whitelists additional bound names (e.g. callback parameters).
    """
    tree = validate_expr(expr, extra_names)
    return eval(compile(tree, "<expr>", "eval"), ns)  # noqa: S307

"""Safe expression evaluator for user-supplied measurement / derived-trace formulas.

Only numeric, numpy, and whitelisted built-in operations are permitted.
Dunder attribute access and any name outside the whitelist raise ValueError.
"""

from __future__ import annotations

import ast
import math

import numpy as np

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


# Cap on the decimal-digit length of any integer power. Real measurement /
# co-sim formulas never need astronomically large integers; an attacker-supplied
# ``9**9**9`` or ``10**100000000`` otherwise burns CPU and memory building a
# multi-megabyte/gigabyte Python int even though no array is allocated. ~4096
# digits is far above any legitimate use yet trivially cheap.
_MAX_POW_RESULT_DIGITS = 4096


def _pow_too_large(base: int, exp: int) -> bool:
    """True if ``base ** exp`` would exceed the integer-power digit cap."""
    if exp <= 0 or abs(base) <= 1:
        return False  # |result| <= 1, or a fraction/1 — never large
    return exp * math.log10(abs(base)) > _MAX_POW_RESULT_DIGITS


def _safe_pow(base, exp):
    """Runtime ``**`` guard — only constrains the int**int case that can blow up.

    Float/array powers cannot exhaust memory (they saturate to ``inf``), so they
    pass straight through; only an oversized *integer* power is refused.
    """
    if (
        isinstance(base, (int, np.integer))
        and isinstance(exp, (int, np.integer))
        and not isinstance(base, bool)
        and not isinstance(exp, bool)
        and _pow_too_large(int(base), int(exp))
    ):
        raise ValueError("refusing to evaluate oversized integer power")
    return base**exp


# Cap on the element count of a sequence built by repetition (``[x] * n`` or
# ``n * [x]``). ``ast.List`` and ``ast.Mult`` are deliberately allowed — the
# np.interp PWL helper emits inline ``[...]`` arrays — so without this guard a
# formula like ``[0] * 10**9`` would build a billion-element Python list and
# exhaust memory even though no numpy allocator (which _SafeNumpy caps) is hit.
_MAX_SEQ_ELEMENTS = 50_000_000


def _safe_mul(a, b):
    """Runtime ``*`` guard — bounds sequence repetition; numeric mult unaffected.

    Only ``seq * int`` / ``int * seq`` (list/tuple/str/bytes repetition) is
    constrained; the overwhelmingly common numeric/array multiply falls straight
    through to ``a * b`` after two cheap isinstance checks.
    """
    seq, n = None, None
    if (
        isinstance(a, (list, tuple, str, bytes))
        and isinstance(b, (int, np.integer))
        and not isinstance(b, bool)
    ):
        seq, n = a, int(b)
    elif (
        isinstance(b, (list, tuple, str, bytes))
        and isinstance(a, (int, np.integer))
        and not isinstance(a, bool)
    ):
        seq, n = b, int(a)
    if seq is not None and n > 0 and len(seq) * n > _MAX_SEQ_ELEMENTS:
        raise ValueError("refusing to build oversized sequence")
    return a * b


def _const_int(node: ast.AST) -> "int | None":
    """Fold *node* to an int if it is a constant integer arithmetic expression.

    Returns None for anything involving names/calls/floats (i.e. runtime values).
    Raises ValueError if a constant integer power along the way would be
    oversized — caught *before* the huge int is ever materialised, which also
    covers chained literals like ``9**9**9`` (folded bottom-up).
    """
    if isinstance(node, ast.Constant):
        v = node.value
        return v if (isinstance(v, int) and not isinstance(v, bool)) else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _const_int(node.operand)
        if v is None:
            return None
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        left, right = _const_int(node.left), _const_int(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Pow):
            if _pow_too_large(left, right):
                raise ValueError("refusing to evaluate oversized integer power")
            return left**right
        return None  # Div/Mod/etc. — not folded
    return None


def _guard_const_pow(tree: ast.AST) -> None:
    """Reject constant integer powers whose result would be oversized.

    Walks every ``**`` node and folds its operands; ``_const_int`` raises if a
    fold would overflow the digit cap. Catches the literal/constant cases that
    are evaluated even by callers (e.g. the co-sim widget) which compile the raw
    source string rather than going through :func:`safe_eval`'s runtime guard.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            _const_int(node)  # for its overflow side effect; value discarded


class _RuntimeOpGuard(ast.NodeTransformer):
    """Rewrite ``a ** b`` → ``__safe_pow__(a, b)`` and ``a * b`` → ``__safe_mul__(a, b)``.

    Routing through these guards bounds runtime integer powers and sequence
    repetitions even when the operands are computed at runtime (e.g.
    ``int(max(vec)) ** k`` or ``[0] * int(n)``), which the validate-time
    constant-fold (:func:`_guard_const_pow`) cannot see.
    """

    _GUARDS = {ast.Pow: "__safe_pow__", ast.Mult: "__safe_mul__"}

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        guard = self._GUARDS.get(type(node.op))
        if guard is not None:
            call = ast.Call(
                func=ast.Name(id=guard, ctx=ast.Load()),
                args=[node.left, node.right],
                keywords=[],
            )
            return ast.copy_location(call, node)
        return node


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


def validate_expr(expr: str, extra_names: frozenset[str] = frozenset()) -> ast.Expression:
    """Parse *expr* and validate it against the whitelist plus *extra_names*.

    Returns the parsed AST (an :class:`ast.Expression`) so callers can compile
    it without re-parsing. Raises ValueError for any disallowed construct.
    """
    tree = ast.parse(expr, mode="eval")
    _validate(tree, _WHITELISTED_NAMES | extra_names)
    _guard_const_pow(tree)
    return tree


class _SafeNumpy:
    """A numpy facade that caps the size of array-allocating calls.

    Loaded measurement / derived-trace / co-sim expressions are auto-evaluated
    after every run, so a project file could smuggle ``np.zeros(10**12)`` (or
    ``arange`` / ``linspace`` / ``full`` / ``ones`` / ``empty``) and exhaust
    memory even though the AST sandbox blocks code execution. This wrapper
    estimates the element count *before* allocating and refuses oversized
    requests. Everything else (reductions over ``vec()`` arrays, ufuncs,
    constants) is delegated unchanged to numpy.

    Use :data:`SAFE_NUMPY` as the ``np`` binding in evaluation namespaces.
    """

    #: Upper bound on elements any single allocator may produce (~400 MB float64).
    MAX_ELEMENTS = 50_000_000
    #: Upper bound on *bytes* — element count alone is bypassable via a huge
    #: itemsize (e.g. ``dtype='V1000000000'`` makes 2 elements ~2 GB).
    MAX_BYTES = MAX_ELEMENTS * 8

    def __getattr__(self, name: str):
        # Anything not overridden below is the real numpy attribute.
        return getattr(np, name)

    def _check(self, n: int, dtype=None) -> None:
        if n > self.MAX_ELEMENTS:
            raise ValueError(
                f"refusing to allocate array of {n} elements (limit {self.MAX_ELEMENTS})"
            )
        # Element count is not enough: a small count with an enormous itemsize
        # still exhausts memory, so bound the total byte size too.
        if dtype is not None:
            try:
                itemsize = np.dtype(dtype).itemsize
            except TypeError:
                itemsize = 8
            if n * itemsize > self.MAX_BYTES:
                raise ValueError(
                    f"refusing to allocate {n * itemsize} bytes (limit {self.MAX_BYTES})"
                )

    @staticmethod
    def _count(shape) -> int:
        if isinstance(shape, (int, np.integer)):
            return int(shape)
        try:
            total = 1
            for d in shape:
                total *= int(d)
            return total
        except TypeError:
            return 0

    def zeros(self, shape, *a, **k):
        # np.zeros(shape, dtype=float, ...) — dtype is the first positional.
        self._check(self._count(shape), a[0] if a else k.get("dtype"))
        return np.zeros(shape, *a, **k)

    def ones(self, shape, *a, **k):
        self._check(self._count(shape), a[0] if a else k.get("dtype"))
        return np.ones(shape, *a, **k)

    def empty(self, shape, *a, **k):
        self._check(self._count(shape), a[0] if a else k.get("dtype"))
        return np.empty(shape, *a, **k)

    def full(self, shape, *a, **k):
        # np.full(shape, fill_value, dtype=None, ...) — dtype is the 2nd positional.
        self._check(self._count(shape), a[1] if len(a) >= 2 else k.get("dtype"))
        return np.full(shape, *a, **k)

    def ndarray(self, shape, *a, **k):
        # np.ndarray(shape, dtype=float, ...) constructs an *uninitialised* array
        # of the given shape — every bit as memory-hungry as zeros/empty, and the
        # original audit bypass (np.ndarray((10**9,))) went straight to numpy
        # because __getattr__ returned the unguarded class.
        self._check(self._count(shape), a[0] if a else k.get("dtype"))
        return np.ndarray(shape, *a, **k)

    def arange(self, *a, **k):
        if len(a) == 1:
            start, stop, step = 0, a[0], k.get("step", 1)
        elif len(a) == 2:
            start, stop, step = a[0], a[1], k.get("step", 1)
        else:
            start, stop, step = a[0], a[1], a[2]
        try:
            n = max(0, math.ceil((float(stop) - float(start)) / float(step)))
        except (TypeError, ValueError, ZeroDivisionError):
            n = 0
        self._check(n, k.get("dtype"))
        return np.arange(*a, **k)

    def linspace(self, *a, **k):
        num = k.get("num")
        if num is None and len(a) >= 3:
            num = a[2]
        if num is None:
            num = 50
        try:
            n = int(num)
        except (TypeError, ValueError):
            n = 0
        # _check() must run *outside* the conversion try/except, or its
        # "refusing to allocate" ValueError would be swallowed here.
        self._check(n, k.get("dtype"))
        return np.linspace(*a, **k)

    @staticmethod
    def _total_size(arrays) -> int:
        """Best-effort element count of an iterable of array-likes (0 if opaque)."""
        try:
            return sum(int(np.asarray(arr).size) for arr in arrays)
        except (TypeError, ValueError):
            return 0

    # Combiners join existing arrays into a larger one. Their inputs are already
    # bounded by the allocator caps above and by _safe_mul (which stops
    # ``[small] * huge`` from ever building the input list), but cap the *result*
    # too so a long literal input list can't slip a giant allocation past them.
    def concatenate(self, arrays, *a, **k):
        self._check(self._total_size(arrays))
        return np.concatenate(arrays, *a, **k)

    def stack(self, arrays, *a, **k):
        self._check(self._total_size(arrays))
        return np.stack(arrays, *a, **k)

    def hstack(self, arrays, *a, **k):
        self._check(self._total_size(arrays))
        return np.hstack(arrays, *a, **k)

    def vstack(self, arrays, *a, **k):
        self._check(self._total_size(arrays))
        return np.vstack(arrays, *a, **k)

    def append(self, arr, values, *a, **k):
        self._check(int(np.asarray(arr).size) + int(np.asarray(values).size))
        return np.append(arr, values, *a, **k)

    def outer(self, a_arr, b_arr, *a, **k):
        # outer(u, v) produces len(u) * len(v) elements — two MAX_ELEMENTS-sized
        # vectors would otherwise yield a ~2.5e15-element matrix.
        self._check(int(np.asarray(a_arr).size) * int(np.asarray(b_arr).size))
        return np.outer(a_arr, b_arr, *a, **k)


#: Shared size-capped numpy facade for expression namespaces.
SAFE_NUMPY = _SafeNumpy()


def safe_eval(expr: str, ns: dict, extra_names: frozenset[str] = frozenset()) -> object:
    """Parse, validate, then eval *expr* against *ns*.

    Raises ValueError for any disallowed construct before eval is called.
    The caller must populate *ns* with all whitelisted names (np, math, vec, …).
    *extra_names* whitelists additional bound names (e.g. callback parameters).
    """
    tree = validate_expr(expr, extra_names)
    # Bound runtime integer powers and sequence repetitions (operands may be
    # computed, e.g. int(max(vec))) by routing every ``**`` / ``*`` through the
    # safe wrappers. The injected names are added to the eval namespace; they
    # cannot clash with user names (dunder, and user dunders are already rejected
    # by the validator).
    tree = _RuntimeOpGuard().visit(tree)
    ast.fix_missing_locations(tree)
    ns.setdefault("__safe_pow__", _safe_pow)
    ns.setdefault("__safe_mul__", _safe_mul)
    return eval(compile(tree, "<expr>", "eval"), ns)  # noqa: S307


def compile_lambda(
    expr: str,
    arg_names: tuple[str, ...],
    ns: dict,
    extra_names: frozenset[str] = frozenset(),
):
    """Compile *expr* into a callable of *arg_names* under the full sandbox.

    Callers that need to evaluate one expression repeatedly (e.g. a co-sim
    source driven once per timestep) should use this rather than compiling the
    raw source string: it applies the same AST allowlist *and* the runtime
    ``**`` / ``*`` guards as :func:`safe_eval`, so callbacks built from computed
    operands cannot hang the app or exhaust memory.

    *ns* supplies the global names the body resolves against (np, math, …); a
    copy is taken so the caller's dict is never mutated. Raises ValueError /
    SyntaxError for any disallowed construct.
    """
    tree = validate_expr(expr, frozenset(arg_names) | extra_names)
    lam = ast.Expression(
        body=ast.Lambda(
            args=ast.arguments(
                posonlyargs=[],
                args=[ast.arg(arg=n) for n in arg_names],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=tree.body,
        )
    )
    lam = _RuntimeOpGuard().visit(lam)
    ast.fix_missing_locations(lam)
    ns = dict(ns)
    ns.setdefault("__safe_pow__", _safe_pow)
    ns.setdefault("__safe_mul__", _safe_mul)
    return eval(compile(lam, "<expr>", "eval"), ns)  # noqa: S307

"""Tests for the pure model modules extracted in Phase 6."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ngspice_ui.models.expr import safe_eval, validate_expr
from ngspice_ui.models.monte_carlo import (
    generate_netlists,
    insert_before_end,
    parse_spice_val,
    vary_value,
)
from ngspice_ui.models.waveform import compute_fft, compute_group_delay


def _ns(**extra):
    return {
        "__builtins__": {},
        "np": np,
        "math": math,
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "sum": sum,
        "len": len,
        "float": float,
        "int": int,
        "bool": bool,
        "vec": lambda n: np.array([1.0]),
        "True": True,
        "False": False,
        "None": None,
        **extra,
    }


def test_safe_numpy_caps_oversized_allocation():
    from ngspice_ui.models.expr import SAFE_NUMPY

    ns = _ns(np=SAFE_NUMPY)
    oversized = (
        "np.zeros(10**12)",
        "np.ones(10**12)",
        "np.arange(0, 10**12)",
        "np.linspace(0, 1, 10**12)",
    )
    for src in oversized:
        with pytest.raises(ValueError, match="refusing to allocate"):
            safe_eval(src, ns)
    # Reasonably-sized allocations and reductions still work.
    assert safe_eval("np.sum(np.zeros(10))", ns) == 0.0


def test_safe_eval_numeric():
    assert safe_eval("1 + 2 * 3", _ns()) == 7


def test_safe_eval_numpy():
    result = safe_eval("np.max(np.array([1, 2, 3]))", _ns())
    assert result == 3


def test_safe_eval_blocks_dunder_attr():
    with pytest.raises(ValueError, match="private attribute"):
        safe_eval("np.__builtins__", _ns())


def test_safe_eval_blocks_unknown_name():
    with pytest.raises(ValueError, match="name not allowed"):
        safe_eval("open('/etc/passwd')", _ns())


def test_safe_eval_blocks_import():
    with pytest.raises(ValueError, match="name not allowed"):
        safe_eval("__import__('os')", _ns())


def test_safe_eval_blocks_subclass_escape():
    with pytest.raises(ValueError, match="private attribute"):
        safe_eval("().__class__.__bases__[0].__subclasses__()", _ns())


def test_insert_before_end_places_directive_before_end():
    out = insert_before_end("* t\nR1 1 0 1k\n.end", ".param x=5")
    assert out.splitlines() == ["* t", "R1 1 0 1k", ".param x=5", ".end"]


def test_insert_before_end_appends_when_no_end():
    out = insert_before_end("* t\nR1 1 0 1k", ".param x=5")
    assert out.splitlines()[-1] == ".param x=5"


def test_validate_expr_allows_extra_names():
    # Co-sim expressions bind extra parameters (t, name, old_delta).
    validate_expr("np.sin(t) + 1", frozenset({"t", "name"}))
    validate_expr("min(old_delta, 1e-6)", frozenset({"t", "old_delta"}))


def test_validate_expr_rejects_unbound_name():
    # A name not in the whitelist nor the extra set is rejected, even though
    # the surrounding co-sim eval scope has empty builtins.
    with pytest.raises(ValueError, match="name not allowed"):
        validate_expr("__import__('os')", frozenset({"t", "name"}))


def test_validate_expr_rejects_attribute_escape():
    with pytest.raises(ValueError, match="private attribute"):
        validate_expr("t.__class__", frozenset({"t", "name"}))


def test_safe_eval_blocks_np_file_io():
    with pytest.raises(ValueError, match="numpy attribute not allowed"):
        safe_eval("np.savetxt('/tmp/x', np.array([1]))", _ns())


def test_safe_eval_blocks_np_loadtxt():
    with pytest.raises(ValueError, match="numpy attribute not allowed"):
        safe_eval("np.loadtxt('/tmp/x')", _ns())


def test_safe_eval_blocks_array_method_on_computed():
    with pytest.raises(ValueError, match="attribute access on computed values not allowed"):
        safe_eval("np.array([1]).tofile('/tmp/x')", _ns())


def test_safe_eval_blocks_np_fft_submodule():
    # np.fft is blocked either as an unknown numpy attr or as chained attr access
    with pytest.raises(ValueError):
        safe_eval("np.fft.rfft(np.array([1.0]))", _ns())


def test_safe_eval_allows_safe_np_attrs():
    assert safe_eval("np.sqrt(np.mean(np.array([4.0, 4.0])))", _ns()) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# models.monte_carlo
# ---------------------------------------------------------------------------


def test_parse_spice_val_plain():
    assert parse_spice_val("1000") == pytest.approx(1000.0)


def test_parse_spice_val_kilo():
    assert parse_spice_val("10k") == pytest.approx(10_000.0)


def test_parse_spice_val_mega():
    assert parse_spice_val("1MEG") == pytest.approx(1e6)


def test_parse_spice_val_nano():
    assert parse_spice_val("100n") == pytest.approx(100e-9)


def test_parse_spice_val_micro_farad():
    assert parse_spice_val("2.2uF") == pytest.approx(2.2e-6)


def test_parse_spice_val_kilo_ohm():
    assert parse_spice_val("10kOhm") == pytest.approx(10_000.0)


def test_parse_spice_val_invalid():
    with pytest.raises(ValueError):
        parse_spice_val("not_a_number")


def test_vary_value_raises_on_invalid():
    with pytest.raises(ValueError):
        vary_value("not_a_number", 5.0, "Uniform")


def test_vary_value_uniform_in_range():
    rng = np.random.default_rng(42)
    nom = "1k"
    for _ in range(50):
        v = vary_value(nom, 10.0, "Uniform", rng)
        assert 900 <= v <= 1100


def test_vary_value_gaussian_centered():
    rng = np.random.default_rng(0)
    vals = [vary_value("1k", 10.0, "Gaussian", rng) for _ in range(1000)]
    assert abs(np.mean(vals) - 1000) < 20  # mean within 2%


def test_generate_netlists_count():
    base = "* test\nR1 1 0 1k\n.end"
    netlists = generate_netlists(base, [("R1", "1k", 5.0, "Uniform")], n_runs=10)
    assert len(netlists) == 10


def test_generate_netlists_substitutes_value():
    rng = np.random.default_rng(7)
    base = "* test\nR1 1 0 1000\n.end"
    netlists = generate_netlists(base, [("R1", "1000", 50.0, "Uniform")], n_runs=20, rng=rng)
    # At least some netlists should differ from the original value
    originals = sum(1 for nl in netlists if "1000" in nl.split()[3] if len(nl.split()) > 3)
    assert originals < 20


def test_generate_netlists_empty_variations():
    base = "* test\nR1 1 0 1k\n.end"
    netlists = generate_netlists(base, [], n_runs=3)
    assert all(nl == base for nl in netlists)


def test_generate_netlists_skips_invalid_nominal():
    base = "* test\nR1 1 0 1k\n.end"
    # Invalid nominal should not raise; original value is preserved
    netlists = generate_netlists(base, [("R1", "not_a_number", 5.0, "Uniform")], n_runs=2)
    assert len(netlists) == 2
    assert all("1k" in nl for nl in netlists)


# ---------------------------------------------------------------------------
# models.waveform
# ---------------------------------------------------------------------------


def test_compute_fft_shape():
    t = np.linspace(0, 1e-3, 1024)
    y = np.sin(2 * np.pi * 1000 * t)
    freqs, mag_db = compute_fft(t, y)
    assert len(freqs) == len(mag_db)
    assert freqs[0] > 0  # DC bin dropped


def test_compute_fft_peak_near_1khz():
    t = np.linspace(0, 1e-3, 1024)
    y = np.sin(2 * np.pi * 1000 * t)
    freqs, mag_db = compute_fft(t, y)
    peak_idx = np.argmax(mag_db)
    assert abs(freqs[peak_idx] - 1000) < 200  # within 200 Hz of 1 kHz


def test_compute_fft_unit_sine_near_zero_db():
    # Unit-amplitude sine should be ~0 dB with the single-sided correction applied.
    t = np.linspace(0, 0.1, 8192)
    y = np.sin(2 * np.pi * 100 * t)
    freqs, mag_db = compute_fft(t, y)
    peak_db = mag_db[np.argmax(mag_db)]
    assert abs(peak_db) < 1.0  # within 1 dB of 0 dB


def test_compute_fft_odd_length_last_bin_full_scale():
    # Regression: an odd-length rfft has no Nyquist bin, so its final bin has a
    # negative-frequency partner and must be doubled like every other non-DC bin.
    # Doubling [1:-1] unconditionally left a full-scale tone landing on the last
    # bin 6.02 dB low. Place a unit cosine exactly on the last bin (k cycles over
    # n uniform samples → no leakage) and require ~0 dB, not ~-6 dB.
    n = 1001  # odd
    dt = 1e-4
    x = np.arange(n) * dt
    k = (n - 1) // 2  # index of the final rfft bin
    f = k / (n * dt)
    y = np.cos(2 * np.pi * f * x)
    _, mag_db = compute_fft(x, y)
    assert mag_db[-1] == pytest.approx(0.0, abs=0.05)


def test_compute_fft_even_length_nyquist_not_doubled():
    # Complement to the odd-length case: an even-length rfft *does* have a Nyquist
    # bin (no partner), which must stay un-doubled. A full-scale Nyquist cosine
    # already reads 0 dB; doubling it would wrongly report +6 dB.
    n = 1000  # even
    dt = 1e-4
    x = np.arange(n) * dt
    y = np.cos(np.pi * np.arange(n))  # Nyquist: alternating ±1
    _, mag_db = compute_fft(x, y)
    assert mag_db[-1] == pytest.approx(0.0, abs=0.05)


def test_safe_numpy_blocks_ndarray_constructor():
    # np.ndarray(shape) allocates just like zeros/empty; the original bypass
    # reached the unguarded numpy class through __getattr__.
    from ngspice_ui.models.expr import SAFE_NUMPY

    with pytest.raises(ValueError, match="refusing to allocate"):
        safe_eval("np.ndarray((10**9,))", _ns(np=SAFE_NUMPY))


def test_safe_numpy_blocks_huge_itemsize_dtype():
    # Small element count, enormous itemsize — caught by the byte-size bound.
    from ngspice_ui.models.expr import SAFE_NUMPY

    with pytest.raises(ValueError, match="refusing to allocate"):
        safe_eval("np.zeros(2, dtype='V1000000000')", _ns(np=SAFE_NUMPY))


def test_safe_eval_blocks_constant_integer_power_blowup():
    # Static const-fold guard catches literal/chained huge integer powers (the
    # path even the co-sim widget's raw-string compile would otherwise hit).
    with pytest.raises(ValueError, match="oversized integer power"):
        safe_eval("10**10**9", _ns())
    with pytest.raises(ValueError, match="oversized integer power"):
        safe_eval("9**9**9", _ns())


def test_validate_expr_blocks_constant_integer_power_blowup():
    with pytest.raises(ValueError, match="oversized integer power"):
        validate_expr("10**100000000")


def test_safe_eval_blocks_runtime_integer_power_blowup():
    # Exponent is a literal but the base is computed at runtime, so the static
    # fold can't see it — the runtime __safe_pow__ transform must still refuse.
    with pytest.raises(ValueError, match="oversized integer power"):
        safe_eval("int(max(np.array([10.0])))**100000000", _ns())


def test_safe_eval_allows_reasonable_powers():
    # The guard must not break ordinary exponentiation.
    assert safe_eval("2**10", _ns()) == 1024
    assert safe_eval("2.0**0.5", _ns()) == pytest.approx(2**0.5)
    assert safe_eval("np.sum(np.array([1.0, 2.0]) ** 2)", _ns()) == pytest.approx(5.0)


def test_safe_eval_blocks_oversized_sequence_repeat():
    # ast.List/ast.Mult are allowed (np.interp PWL arrays need them), so the
    # runtime __safe_mul__ guard must stop [x] * huge from building a giant list.
    with pytest.raises(ValueError, match="oversized sequence"):
        safe_eval("[0] * 10**9", _ns())
    with pytest.raises(ValueError, match="oversized sequence"):
        safe_eval("int(max(np.array([1.0]))) * ([0] * 60000000)", _ns())


def test_safe_eval_allows_reasonable_multiplication():
    # The sequence guard must not touch ordinary numeric / array multiplication.
    assert safe_eval("3 * 4", _ns()) == 12
    assert safe_eval("[0] * 3", _ns()) == [0, 0, 0]
    assert safe_eval("np.sum(np.array([1.0, 2.0]) * 2)", _ns()) == pytest.approx(6.0)


def test_safe_numpy_caps_combiners():
    from ngspice_ui.models.expr import SAFE_NUMPY

    ns = _ns(np=SAFE_NUMPY)
    # A long-but-cheap input list fed to a combiner would otherwise allocate an
    # oversized result; the wrappers estimate the result size first.
    with pytest.raises(ValueError, match="refusing to allocate"):
        safe_eval("np.concatenate([np.zeros(40000000), np.zeros(40000000)])", ns)
    with pytest.raises(ValueError, match="refusing to allocate"):
        safe_eval("np.outer(np.zeros(10000), np.zeros(10000))", ns)
    # Reasonable combines still work.
    assert safe_eval("np.sum(np.concatenate([np.zeros(3), np.ones(2)]))", ns) == pytest.approx(2.0)


def test_compile_lambda_applies_runtime_guards():
    from ngspice_ui.models.expr import SAFE_NUMPY, compile_lambda

    ns = {"__builtins__": {}, "np": SAFE_NUMPY, "math": math, "float": float}
    # A well-formed source compiles and evaluates against its parameters.
    fn = compile_lambda("np.sin(t) + 0.0", ("t", "name"), ns)
    assert fn(0.0, "vext") == pytest.approx(0.0)

    # The runtime ** / * guards wrap the lambda body, so a callback whose
    # operands are only known at call time still cannot hang or exhaust memory.
    pow_fn = compile_lambda("int(t) ** 100000000", ("t", "name"), {**ns, "int": int})
    with pytest.raises(ValueError, match="oversized integer power"):
        pow_fn(10.0, "vext")

    seq_fn = compile_lambda("[t] * 1000000000", ("t", "name"), ns)
    with pytest.raises(ValueError, match="oversized sequence"):
        seq_fn(0.0, "vext")


def test_compile_lambda_rejects_disallowed_construct():
    from ngspice_ui.models.expr import SAFE_NUMPY, compile_lambda

    ns = {"__builtins__": {}, "np": SAFE_NUMPY, "math": math}
    with pytest.raises(ValueError, match="not allowed"):
        compile_lambda("__import__('os')", ("t", "name"), ns)


def test_compute_fft_rejects_short_input():
    with pytest.raises(ValueError):
        compute_fft(np.array([0.0, 1.0]), np.array([0.0, 1.0]))


def test_compute_fft_rejects_nonmonotonic_time():
    # Endpoints increase but an interior sample regresses — np.interp would
    # silently produce garbage, so this must be rejected.
    x = np.array([0.0, 1.0, 0.5, 2.0])
    y = np.array([0.0, 1.0, 0.0, -1.0])
    with pytest.raises(ValueError, match="strictly increasing"):
        compute_fft(x, y)


def test_compute_group_delay_flat_phase():
    freq = np.linspace(1e3, 1e6, 200)
    # Causal delay: H(f) = e^{-j2πfτ₀}, group delay = τ₀
    h = np.exp(-1j * 2 * np.pi * freq * 1e-6)
    tau = compute_group_delay(freq, h)
    assert np.allclose(tau, 1e-6, atol=1e-7)

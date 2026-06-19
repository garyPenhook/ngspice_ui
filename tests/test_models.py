"""Tests for the pure model modules extracted in Phase 6."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ngspice_ui.models.expr import safe_eval
from ngspice_ui.models.monte_carlo import generate_netlists, parse_spice_val, vary_value
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


def test_compute_fft_rejects_short_input():
    with pytest.raises(ValueError):
        compute_fft(np.array([0.0, 1.0]), np.array([0.0, 1.0]))


def test_compute_group_delay_flat_phase():
    freq = np.linspace(1e3, 1e6, 200)
    # Causal delay: H(f) = e^{-j2πfτ₀}, group delay = τ₀
    h = np.exp(-1j * 2 * np.pi * freq * 1e-6)
    tau = compute_group_delay(freq, h)
    assert np.allclose(tau, 1e-6, atol=1e-7)

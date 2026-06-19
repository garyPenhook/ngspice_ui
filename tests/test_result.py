"""Tests for the immutable SimulationResult snapshot.

Pure data: VectorData is constructed directly and a fake session drives
from_session — no libngspice required.
"""

from __future__ import annotations

import numpy as np
import pytest

from ngspice_ui.engine.session import VectorData
from ngspice_ui.models.result import SimulationResult


def _vec(name: str, values) -> VectorData:
    arr = np.asarray(values, dtype=np.float64)
    return VectorData(name=name, v_type=0, v_flags=0, data=arr, is_complex=False)


def _result() -> SimulationResult:
    return SimulationResult(
        current_plot_name="tran1",
        plots={"tran1": ["time", "v(out)"], "op1": ["v(out)"]},
        vectors={
            "tran1.time": _vec("time", [0.0, 1.0, 2.0]),
            "tran1.v(out)": _vec("v(out)", [0.0, 0.5, 1.0]),
            "op1.v(out)": _vec("v(out)", [3.3]),
        },
    )


def test_read_api_mirrors_session():
    r = _result()
    assert r.current_plot() == "tran1"
    assert r.all_plots() == ["tran1", "op1"]
    assert r.all_vecs("tran1") == ["time", "v(out)"]
    assert r.all_vecs("missing") == []


def test_get_vector_fully_qualified():
    r = _result()
    assert r.get_vector("tran1.v(out)").data.tolist() == [0.0, 0.5, 1.0]


def test_get_vector_bare_name_resolves_in_current_plot():
    r = _result()
    # bare "v(out)" -> tran1.v(out) (the current plot), not op1.v(out)
    assert r.get_vector("v(out)").data.tolist() == [0.0, 0.5, 1.0]


def test_get_vector_missing_raises_keyerror():
    r = _result()
    with pytest.raises(KeyError):
        r.get_vector("tran1.nope")
    with pytest.raises(KeyError):
        r.get_vector("bare_missing")


def test_all_plots_returns_a_copy():
    r = _result()
    r.all_plots().append("mutated")
    assert r.all_plots() == ["tran1", "op1"]


def test_defaults_are_empty():
    r = SimulationResult()
    assert r.current_plot() == ""
    assert r.all_plots() == []
    with pytest.raises(KeyError):
        r.get_vector("anything")


class _FakeSession:
    def __init__(self):
        self._data = {
            "const": ["e", "pi"],
            "tran1": ["time", "v(out)"],
        }
        self._vectors = {
            "tran1.time": _vec("time", [0.0, 1.0]),
            "tran1.v(out)": _vec("v(out)", [1.0, 2.0]),
            "tran1.gone": None,  # disappears between listing and fetch
            "const.e": _vec("e", [2.718]),
            "const.pi": _vec("pi", [3.14159]),
        }

    def current_plot(self):
        return "tran1"

    def all_plots(self):
        return list(self._data)

    def all_vecs(self, plot):
        vecs = list(self._data.get(plot, []))
        if plot == "tran1":
            vecs = vecs + ["gone"]  # listed but unfetchable
        return vecs

    def get_vector(self, name):
        v = self._vectors.get(name)
        if v is None:
            raise KeyError(name)
        return v


def test_from_session_skips_const_and_unfetchable():
    r = SimulationResult.from_session(_FakeSession())
    assert r.current_plot() == "tran1"
    assert r.all_plots() == ["tran1"]  # const skipped
    assert "tran1.time" in r.vectors
    assert "tran1.v(out)" in r.vectors
    assert "tran1.gone" not in r.vectors  # fetch failed, skipped cleanly
    assert not any(k.startswith("const") for k in r.vectors)

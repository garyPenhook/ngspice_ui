"""Tests for plot-pane behaviours that don't need a live ngspice session.

Focus on the Smith-chart interpretation (Γ directly vs Z → Γ conversion) and
the per-trace CSV export. Requires PySide6 but not libngspice.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ngspice_ui.gui.widgets.plot_canvas import _PlotPane  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _line_xy(pane: _PlotPane, label: str):
    for line in pane._ax.get_lines():
        if line.get_label() == label:
            return np.asarray(line.get_xdata()), np.asarray(line.get_ydata())
    raise AssertionError(f"no line labelled {label!r}")


def test_smith_z_to_gamma_converts_impedance(qapp):
    pane = _PlotPane()
    pane._mode = _PlotPane.MODE_SMITH
    pane._smith_interp.setCurrentIndex(1)  # "Z → Γ"
    pane._z0_edit.setText("50")

    z = np.array([50 + 0j, 75 + 25j, 0 + 0j])
    pane._plot_smith([("ac1", "v(out)", None, z, True)])

    gamma = (z - 50.0) / (z + 50.0)
    x, y = _line_xy(pane, "v(out)")
    assert np.allclose(x, gamma.real)
    assert np.allclose(y, gamma.imag)
    # Z = Z0 is a perfect match → Γ = 0 (centre of the chart).
    assert x[0] == pytest.approx(0.0)
    assert y[0] == pytest.approx(0.0)


def test_smith_gamma_mode_plots_data_directly(qapp):
    pane = _PlotPane()
    pane._mode = _PlotPane.MODE_SMITH
    pane._smith_interp.setCurrentIndex(0)  # "Γ (reflection)"

    g = np.array([0.0 + 0j, 0.3 + 0.4j, -0.5 + 0j])
    pane._plot_smith([("sp1", "S11", None, g, True)])

    x, y = _line_xy(pane, "S11")
    assert np.allclose(x, g.real)
    assert np.allclose(y, g.imag)


def test_z0_defaults_to_50_on_invalid_input(qapp):
    pane = _PlotPane()
    pane._z0_edit.setText("not a number")
    assert pane._smith_z0() == 50.0
    pane._z0_edit.setText("-10")  # non-positive is invalid
    assert pane._smith_z0() == 50.0
    pane._z0_edit.setText("75")
    assert pane._smith_z0() == 75.0

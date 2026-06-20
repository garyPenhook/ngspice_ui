"""Tests for the analysis panel. Requires PySide6, not libngspice."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ngspice_ui.gui.widgets.analysis_panel import (  # noqa: E402
    _MAX_TEMP_POINTS,
    AnalysisPanel,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_temperatures_disabled_returns_empty(qapp):
    w = AnalysisPanel()
    w._temp_box.setChecked(False)
    w._temp_edit.setText("0 50 100")
    assert w.get_temperatures() == []


def test_temperatures_split_on_whitespace(qapp):
    w = AnalysisPanel()
    w._temp_box.setChecked(True)
    w._temp_edit.setText("0 50 100")
    assert w.get_temperatures() == ["0", "50", "100"]


def test_temperatures_are_capped(qapp):
    # Each temperature runs as an independent pass, so an unbounded list must not
    # queue an unbounded number of simulations.
    w = AnalysisPanel()
    w._temp_box.setChecked(True)
    w._temp_edit.setText(" ".join(str(i) for i in range(_MAX_TEMP_POINTS + 25)))
    assert len(w.get_temperatures()) == _MAX_TEMP_POINTS

"""Tests for the parametric sweep widget. Requires PySide6, not libngspice."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ngspice_ui.gui.widgets.param_sweep_widget import ParamSweepWidget  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_linear_values_accept_spice_magnitudes(qapp):
    w = ParamSweepWidget()
    w._start_edit.setText("0")
    w._stop_edit.setText("1k")  # float("1k") would raise; must parse as 1000
    w._step_edit.setText("250")
    vals = w._values()
    assert vals == ["0", "250", "500", "750", "1000"]


def test_negative_step_with_ascending_bounds_is_empty_not_infinite(qapp):
    w = ParamSweepWidget()
    w._start_edit.setText("0")
    w._stop_edit.setText("1k")
    w._step_edit.setText("-100")  # wrong sign: previously looped forever
    assert w._values() == []


def test_descending_sweep(qapp):
    w = ParamSweepWidget()
    w._start_edit.setText("1k")
    w._stop_edit.setText("0")
    w._step_edit.setText("-250")
    assert w._values() == ["1000", "750", "500", "250", "0"]


def test_build_netlists_inserts_param_before_end(qapp):
    w = ParamSweepWidget()
    w._name_edit.setText("rval")
    w._radio_list.setChecked(True)
    w._list_edit.setText("100 1k")
    nets = w.build_netlists("* t\nR1 in out {rval}\n.end")
    assert len(nets) == 2
    assert nets[0].splitlines() == ["* t", "R1 in out {rval}", ".param rval=100", ".end"]
    assert nets[1].splitlines()[-2] == ".param rval=1k"

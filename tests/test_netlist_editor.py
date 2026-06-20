"""Tests for the netlist editor's find/replace. Requires PySide6, not libngspice."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ngspice_ui.gui.widgets.netlist_editor import NetlistEditor  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_regex_replace_all_matches_pattern(qapp):
    ed = NetlistEditor()
    ed.set_content("R1 1 0 1k\nR2 2 0 2k\n")
    bar = ed._find_bar
    bar._find_edit.setText(r"R\d")
    bar._regex_cb.setChecked(True)
    bar._replace_edit.setText("RES")
    bar._replace_all()
    text = ed.toPlainText()
    # A regex must match both refs; before the fix the pattern was searched
    # literally and matched nothing.
    assert "RES 1 0 1k" in text
    assert "RES 2 0 2k" in text


def test_literal_replace_does_not_treat_text_as_regex(qapp):
    ed = NetlistEditor()
    ed.set_content("a.b a b\n")
    bar = ed._find_bar
    bar._find_edit.setText("a.b")  # '.' is literal here (regex off)
    bar._regex_cb.setChecked(False)
    bar._replace_edit.setText("X")
    bar._replace_all()
    text = ed.toPlainText()
    assert text.startswith("X a b")  # only the literal "a.b" replaced

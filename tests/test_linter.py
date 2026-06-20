"""Tests for the SPICE netlist linter's value-typo heuristic.

The linter is pure (no Qt), so it is tested directly.
"""

from __future__ import annotations

from ngspice_ui.gui.widgets.linter import lint


def _typos(text: str) -> list[str]:
    return [msg for _, msg in lint(text) if "typo" in msg]


def test_valid_unit_suffixes_not_flagged():
    # A scale factor followed by a unit letter is valid SPICE — the suffix is
    # consumed and the trailing unit text ignored. None of these are typos.
    for line in ("R1 1 0 10kOhm", "C1 1 0 4.7mF", "L1 1 0 1MHz", "R2 1 0 2.2k", "C2 1 0 100nF"):
        assert _typos(f"{line}\n.end") == [], line


def test_repeated_scale_suffix_is_flagged():
    assert _typos("R1 1 0 10kk\n.end")
    assert _typos("C1 1 0 4.7uuF\n.end")

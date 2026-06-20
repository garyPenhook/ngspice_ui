"""Tests for the SPICE netlist linter's value-typo heuristic.

The linter is pure (no Qt), so it is tested directly.
"""

from __future__ import annotations

from pathlib import Path

from ngspice_ui.gui.widgets.linter import lint


def _typos(text: str) -> list[str]:
    return [msg for _, msg in lint(text) if "typo" in msg]


def _missing_includes(text: str, netlist_path: Path) -> list[str]:
    return [msg for _, msg in lint(text, netlist_path) if "not found" in msg]


def test_valid_unit_suffixes_not_flagged():
    # A scale factor followed by a unit letter is valid SPICE — the suffix is
    # consumed and the trailing unit text ignored. None of these are typos.
    for line in ("R1 1 0 10kOhm", "C1 1 0 4.7mF", "L1 1 0 1MHz", "R2 1 0 2.2k", "C2 1 0 100nF"):
        assert _typos(f"{line}\n.end") == [], line


def test_repeated_scale_suffix_is_flagged():
    assert _typos("R1 1 0 10kk\n.end")
    assert _typos("C1 1 0 4.7uuF\n.end")


def test_missing_inc_include_is_flagged(tmp_path):
    # The engine rewrites .inc paths just like .include/.lib, so the linter must
    # catch a missing .inc file rather than letting it fail only at load time.
    deck = tmp_path / "deck.cir"
    text = '.inc "missing_models.inc"\n.end'
    msgs = _missing_includes(text, deck)
    assert msgs == [".inc not found: missing_models.inc"]


def test_existing_include_variants_pass(tmp_path):
    (tmp_path / "models.inc").write_text("* models\n")
    deck = tmp_path / "deck.cir"
    for directive in (".include", ".inc", ".lib"):
        text = f'{directive} "models.inc"\n.end'
        assert _missing_includes(text, deck) == [], directive

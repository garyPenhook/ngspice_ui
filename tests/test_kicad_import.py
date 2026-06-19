"""Golden-output test for the KiCad netlist importer.

Locks the netlist produced from a fixture schematic so future refactors of the
shared parser / domain model cannot silently change importer behavior. Pure —
no Qt, no libngspice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ngspice_ui.schematic.kicad_import import import_kicad_sch

_FIXTURES = Path(__file__).parent / "fixtures"


def test_rc_divider_netlist_golden():
    out = import_kicad_sch(_FIXTURES / "rc_divider.kicad_sch")
    assert out == [
        "* Imported from rc_divider.kicad_sch",
        "R1 in mid 1k",
        "R2 mid 0 2k",
    ]


def test_non_kicad_file_rejected(tmp_path):
    bad = tmp_path / "not.kicad_sch"
    bad.write_text("(some_other_root)", encoding="utf-8")
    with pytest.raises(ValueError):
        import_kicad_sch(bad)

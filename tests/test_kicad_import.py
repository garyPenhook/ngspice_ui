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


def test_multi_unit_pins_not_merged_across_placements():
    # U1 is one multi-unit IC placed as two instances (unit 1 and unit 2). Each
    # placement must contribute only its own unit's pins; merging every unit's
    # pins onto each placement corrupts connectivity. Pins 1/2 belong to unit 1
    # (nets a/b), pins 3/4 to unit 2 (nets c/d).
    out = import_kicad_sch(_FIXTURES / "dual_unit.kicad_sch")
    assert out == [
        "* Imported from dual_unit.kicad_sch",
        "U1 a b c d DUAL",
    ]


def test_subcircuit_sim_type_x_prefixes_reference(tmp_path):
    sch = tmp_path / "x.kicad_sch"
    sch.write_text(
        """(kicad_sch
  (lib_symbols
    (symbol "Sim:OPA"
      (symbol "OPA_1_1"
        (pin input line (at 0 2.54 270) (length 0) (name "IN") (number "1"))
        (pin output line (at 0 -2.54 90) (length 0) (name "OUT") (number "2"))
      )
    )
  )
  (symbol (lib_id "Sim:OPA") (at 100 100 0) (unit 1)
    (property "Reference" "U1" (at 100 100 0))
    (property "Value" "OPAMP" (at 100 100 0))
    (property "Sim.Type" "X" (at 100 100 0))
  )
  (label "in" (at 100 102.54 0))
  (label "out" (at 100 97.46 0))
)
""",
        encoding="utf-8",
    )
    out = import_kicad_sch(sch)
    # Sim.Type=X must emit a subcircuit card (XU1 ...), not an invalid 'U1 ...'.
    assert out[-1] == "XU1 in out OPAMP"


def test_non_kicad_file_rejected(tmp_path):
    bad = tmp_path / "not.kicad_sch"
    bad.write_text("(some_other_root)", encoding="utf-8")
    with pytest.raises(ValueError):
        import_kicad_sch(bad)

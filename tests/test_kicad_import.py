"""Golden-output test for the KiCad netlist importer.

Locks the netlist produced from a fixture schematic so future refactors of the
shared parser / domain model cannot silently change importer behavior. Pure —
no Qt, no libngspice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ngspice_ui.schematic.kicad_import import (
    _apply_sim_pins,
    _resolve_etype,
    import_kicad_sch,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def test_apply_sim_pins_diode_reorders_to_anode_cathode():
    # KiCad writes Sim.Pins in symbol-pin order: "1=K 2=A" means symbol pin 1 is
    # the model's cathode (K) and pin 2 the anode (A). The SPICE diode card lists
    # anode then cathode, so the nets must be reordered — the old "take tokens as
    # written" logic emitted them reversed.
    pin_nets = {"1": "nk", "2": "na"}  # pin1 -> net nk, pin2 -> net na
    assert _apply_sim_pins("1=K 2=A", pin_nets, "D") == ["na", "nk"]


def test_apply_sim_pins_bjt_reorders_to_collector_base_emitter():
    # "1=B 2=C 3=E": pin1->base, pin2->collector, pin3->emitter. SPICE order is
    # C B E, so the previous code's collector/base swap is corrected here.
    pin_nets = {"1": "nb", "2": "nc", "3": "ne"}
    assert _apply_sim_pins("1=B 2=C 3=E", pin_nets, "Q") == ["nc", "nb", "ne"]


def test_apply_sim_pins_numeric_model_indices_sort_by_index():
    # Subcircuit pins are identified by numeric model index; node order follows
    # the index regardless of the token order KiCad wrote them in.
    pin_nets = {"1": "a", "2": "b", "3": "c"}
    assert _apply_sim_pins("3=3 1=1 2=2", pin_nets, "X") == ["a", "b", "c"]


def test_resolve_etype_maps_sim_device():
    assert _resolve_etype("SUBCKT", "", "U1") == "X"
    assert _resolve_etype("NPN", "", "Q1") == "Q"
    assert _resolve_etype("NMOS", "", "M1") == "M"
    assert _resolve_etype("D", "", "D1") == "D"
    # Legacy single-letter Sim.Type still honoured when Sim.Device is absent.
    assert _resolve_etype("", "X", "U1") == "X"
    # Falls back to the reference designator's first letter.
    assert _resolve_etype("", "", "R5") == "R"


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


def test_diode_sim_pins_emits_anode_cathode_order(tmp_path):
    # End-to-end: a diode whose Sim.Pins is "1=K 2=A" must emit anode then
    # cathode. Pin 1 (cathode) sits on net "cat", pin 2 (anode) on net "an".
    sch = tmp_path / "d.kicad_sch"
    sch.write_text(
        """(kicad_sch
  (lib_symbols
    (symbol "Diode:D"
      (symbol "D_1_1"
        (pin passive line (at 0 2.54 270) (length 0) (name "K") (number "1"))
        (pin passive line (at 0 -2.54 90) (length 0) (name "A") (number "2"))
      )
    )
  )
  (symbol (lib_id "Diode:D") (at 100 100 0) (unit 1)
    (property "Reference" "D1" (at 100 100 0))
    (property "Value" "DMOD" (at 100 100 0))
    (property "Sim.Device" "D" (at 100 100 0))
    (property "Sim.Pins" "1=K 2=A" (at 100 100 0))
  )
  (label "cat" (at 100 102.54 0))
  (label "an" (at 100 97.46 0))
)
""",
        encoding="utf-8",
    )
    out = import_kicad_sch(sch)
    assert out[-1] == "D1 an cat DMOD"


def test_sim_device_subckt_prefixes_reference_and_uses_sim_name(tmp_path):
    # Modern KiCad uses Sim.Device "SUBCKT" + Sim.Name instead of Sim.Type "X";
    # this must still emit an X card (XU1 …) using Sim.Name as the model.
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
    (property "Sim.Device" "SUBCKT" (at 100 100 0))
    (property "Sim.Name" "TL072" (at 100 100 0))
  )
  (label "in" (at 100 102.54 0))
  (label "out" (at 100 97.46 0))
)
""",
        encoding="utf-8",
    )
    out = import_kicad_sch(sch)
    assert out[-1] == "XU1 in out TL072"


def test_non_kicad_file_rejected(tmp_path):
    bad = tmp_path / "not.kicad_sch"
    bad.write_text("(some_other_root)", encoding="utf-8")
    with pytest.raises(ValueError):
        import_kicad_sch(bad)

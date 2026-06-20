"""Tests for the Eagle .sch XML → SPICE importer. Pure — no Qt, no libngspice."""

from __future__ import annotations

from ngspice_ui.schematic.eagle_import import _sanitize_net, import_eagle_sch

_EAGLE_SCH = """<?xml version="1.0" encoding="utf-8"?>
<eagle>
  <drawing>
    <schematic>
      <parts>
        <part name="R1" value="1k"/>
      </parts>
      <sheets>
        <sheet>
          <nets>
            <net name="+5V">
              <segment><pinref part="R1" pin="1"/></segment>
            </net>
            <net name="GND">
              <segment><pinref part="R1" pin="2"/></segment>
            </net>
          </nets>
        </sheet>
      </sheets>
    </schematic>
  </drawing>
</eagle>
"""


def test_sanitize_net_rules():
    assert _sanitize_net("GND") == "0"
    assert _sanitize_net("+5V") == "vp5V"
    assert _sanitize_net("-12V") == "vn12V"
    assert _sanitize_net("N$1") == "N_1"
    assert _sanitize_net("3V3") == "v3V3"


def test_eagle_net_names_sanitized(tmp_path):
    p = tmp_path / "rc.sch"
    p.write_text(_EAGLE_SCH, encoding="utf-8")
    out = import_eagle_sch(p)
    # '+5V' must become a legal node ('vp5V'), 'GND' → '0'.
    assert out == [
        "* Imported from rc.sch",
        "R1 vp5V 0 1k",
    ]

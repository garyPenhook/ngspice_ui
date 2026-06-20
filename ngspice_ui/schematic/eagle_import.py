"""
Eagle .sch XML → SPICE netlist importer.

Eagle XML schematics store net connectivity explicitly as <pinref> elements
inside named <net> blocks, making net extraction straightforward.

Supported attributes on <part> / <instance>:
  SPICE_MODEL        — model name or element value override
  SPICE_NETLIST_TYPE — force element-type prefix (e.g. 'X' for subcircuit)
  SPICE_NODE_SEQUENCE — comma-separated pin names in SPICE node order

GND net is mapped to SPICE node '0'.
"""

from __future__ import annotations

import re
from pathlib import Path

import defusedxml.ElementTree as ET

# Eagle net names that map to SPICE ground (node 0).
_GND_NAMES: frozenset[str] = frozenset({"gnd", "0", "0v", "vss", "agnd", "dgnd"})


def _sanitize_net(name: str) -> str:
    """Convert an Eagle net name into a valid ngspice node identifier.

    Mirrors the KiCad importer: ground aliases → '0', leading +/- → vp/vn
    (so '+5V' → 'vp5v', not an illegal node), leading digit → 'v' prefix, and
    any remaining illegal character → '_'.
    """
    n = name.strip()
    if n.lower() in _GND_NAMES:
        return "0"
    if n.startswith("+"):
        n = "vp" + n[1:]
    elif n.startswith("-"):
        n = "vn" + n[1:]
    if n and n[0].isdigit():
        n = "v" + n
    n = re.sub(r"[^A-Za-z0-9_]", "_", n)
    return n or "net"

# Default SPICE node ordering by element-type letter.
# Keys are pin names as used in Eagle library symbols.
_PIN_ORDER: dict[str, list[str]] = {
    "R": ["1", "2"],
    "C": ["1", "2"],
    "L": ["1", "2"],
    "V": ["P", "N"],
    "I": ["P", "N"],
    "D": ["A", "K"],
    "Q": ["C", "B", "E"],
    "M": ["D", "G", "S", "B"],
    "J": ["D", "G", "S"],
}


def _get_attr(elem: ET.Element, name: str) -> str:
    """Return the value of a named <attribute> child, case-insensitively."""
    for attr in elem.findall("attribute"):
        if attr.get("name", "").upper() == name.upper():
            return attr.get("value", "")
    return ""


def import_eagle_sch(path: str | Path) -> list[str]:
    """Parse an Eagle .sch XML schematic and return a SPICE netlist."""
    try:
        tree = ET.parse(str(path))
    except ET.ParseError as exc:
        raise ValueError(f"Not a valid Eagle XML schematic: {exc}") from exc

    xml_root = tree.getroot()
    drawing = xml_root.find("drawing")
    if drawing is None:
        raise ValueError("Not a valid Eagle schematic: missing <drawing>")
    schematic = drawing.find("schematic")
    if schematic is None:
        raise ValueError("Not a valid Eagle schematic: missing <schematic>")

    # ---- Collect all parts (schematic-level) --------------------------------
    # part_name → {value, spice_model, spice_type, spice_seq, prefix}
    parts: dict[str, dict] = {}
    for part in schematic.findall("parts/part"):
        name = part.get("name", "")
        if not name:
            continue
        prefix = name[0].upper() if name else ""
        value = part.get("value", "")
        spice_model = _get_attr(part, "SPICE_MODEL")
        spice_type = _get_attr(part, "SPICE_NETLIST_TYPE")
        spice_seq = _get_attr(part, "SPICE_NODE_SEQUENCE")
        parts[name] = {
            "prefix": prefix,
            "value": value,
            "spice_model": spice_model,
            "spice_type": spice_type or prefix,
            "spice_seq": spice_seq,
        }

    # ---- Build (part_name, pin_name) → net_name from all sheets -------------
    pin_to_net: dict[tuple[str, str], str] = {}
    for sheet in schematic.findall("sheets/sheet"):
        # Merge instance-level attribute overrides
        for inst in sheet.findall("instances/instance"):
            pname = inst.get("part", "")
            if pname not in parts:
                continue
            sm = _get_attr(inst, "SPICE_MODEL")
            if sm:
                parts[pname]["spice_model"] = sm
            st = _get_attr(inst, "SPICE_NETLIST_TYPE")
            if st:
                parts[pname]["spice_type"] = st
            ss = _get_attr(inst, "SPICE_NODE_SEQUENCE")
            if ss:
                parts[pname]["spice_seq"] = ss

        for net in sheet.findall("nets/net"):
            net_name = _sanitize_net(net.get("name", ""))
            for seg in net.findall("segment"):
                for pinref in seg.findall("pinref"):
                    pname = pinref.get("part", "")
                    pin = pinref.get("pin", "")
                    if pname and pin:
                        pin_to_net[(pname, pin)] = net_name

    if not parts:
        raise ValueError("No parts found in Eagle schematic.")

    # ---- Generate SPICE lines -----------------------------------------------
    netlist = [f"* Imported from {Path(path).name}"]
    for pname in sorted(parts):
        info = parts[pname]
        prefix = info["prefix"]
        etype = info["spice_type"].upper() if info["spice_type"] else prefix
        model = info["spice_model"] if info["spice_model"] else info["value"]

        # Collect all pins connected to this part
        part_pins: dict[str, str] = {pin: net for (p, pin), net in pin_to_net.items() if p == pname}
        if not part_pins:
            continue

        # Determine SPICE node order
        seq = info["spice_seq"]
        if seq:
            # SPICE_NODE_SEQUENCE is a comma-separated list of pin names
            nets = [part_pins.get(p.strip(), "?") for p in seq.split(",")]
        elif etype in _PIN_ORDER:
            ordered = [part_pins[p] for p in _PIN_ORDER[etype] if p in part_pins]
            if ordered:
                nets = ordered
                # MOSFET bulk defaults to source
                if etype == "M" and len(nets) == 3:
                    nets = nets + [nets[2]]
            else:
                nets = _sorted_nets(part_pins)
        else:
            nets = _sorted_nets(part_pins)

        # Subcircuit: reference must start with X
        ref = pname if etype != "X" else (pname if pname.startswith("X") else f"X{pname}")
        netlist.append(f"{ref} {' '.join(nets)} {model}")

    return netlist


def _sorted_nets(pin_nets: dict[str, str]) -> list[str]:
    def _key(k: str) -> tuple:
        return (0, int(k)) if k.isdigit() else (1, k)

    return [pin_nets[k] for k in sorted(pin_nets, key=_key)]

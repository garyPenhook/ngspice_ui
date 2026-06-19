"""
KiCad 6/7 .kicad_sch → SPICE netlist importer.

Approach
--------
1. Parse the S-expression into nested Python lists.
2. Extract lib_symbols pin positions (electrical connection points).
3. Build a Union-Find from wire segments to group connected points.
4. Assign net names from labels, global_labels, and power symbols.
5. For each placed symbol (grouped by Reference to handle multi-unit ICs),
   compute absolute pin positions via the placement transform, look up their
   net names, and emit a SPICE element line.

Limitations
-----------
* KiCad 5 (.sch) format is not supported; use File > Export > Netlist from KiCad 5.
* Bus/bus-entry connectivity is treated as simple wire union (sufficient for nets
  that carry individual signals; differential pairs are not split).
* Sim.Type / Sim.SpiceModel / Sim.Pins KiCad properties are used when present and
  override all heuristics.
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path

from .kicad.sexpr import atom as _atom
from .kicad.sexpr import find as _find
from .kicad.sexpr import find_all as _find_all
from .kicad.sexpr import parse_sexp as _parse_sexp
from .kicad.sexpr import prop as _prop

# ---------------------------------------------------------------------------
# Net name sanitisation
# ---------------------------------------------------------------------------

# KiCad power label names that map to SPICE ground (node 0).
_GND_NAMES: frozenset[str] = frozenset({"gnd", "0v", "0", "vss", "dgnd", "agnd", "earth"})


def _sanitize_net(name: str) -> str:
    """Convert a KiCad net/power label into a valid ngspice node identifier.

    Rules
    -----
    * Known ground aliases → '0'.
    * Leading '+' → 'vp' prefix (e.g. +5V → vp5v).
    * Leading '-' → 'vn' prefix (e.g. -5V → vn5v).
    * Parentheses stripped (e.g. (VGND) → vgnd).
    * Any remaining non-alphanumeric, non-underscore char → '_'.
    * Names that start with a digit get a 'v' prefix (SPICE requirement).
    """
    n = name.strip()
    if n.lower() in _GND_NAMES:
        return "0"
    # Power-rail prefix conventions
    if n.startswith("+"):
        n = "vp" + n[1:]
    elif n.startswith("-"):
        n = "vn" + n[1:]
    # Strip parentheses used in virtual-ground labels like (VGND)
    n = n.replace("(", "").replace(")", "")
    # Digit-leading names are illegal in SPICE
    if n and n[0].isdigit():
        n = "v" + n
    # Replace every remaining illegal character with underscore
    n = re.sub(r"[^A-Za-z0-9_]", "_", n)
    return n or "net"


# ---------------------------------------------------------------------------
# Sim.Library path resolution
# ---------------------------------------------------------------------------

# KiCad 6/7 default symbol-library directories (Linux / macOS / Windows).
# Used only when ${KICAD*_SYMBOL_DIR} env vars are not set in the environment.
_KICAD_SYM_DIRS: list[str] = [
    "/usr/share/kicad/symbols",
    "/usr/local/share/kicad/symbols",
    os.path.expanduser("~/kicad/symbols"),
    "C:/Program Files/KiCad/7.0/share/kicad/symbols",
    "C:/Program Files/KiCad/6.0/share/kicad/symbols",
]

# KiCad environment variable substitutions (order matters — longest first).
_KICAD_ENV_VARS: dict[str, list[str]] = {
    "${KICAD8_SYMBOL_DIR}": _KICAD_SYM_DIRS,
    "${KICAD7_SYMBOL_DIR}": _KICAD_SYM_DIRS,
    "${KICAD6_SYMBOL_DIR}": _KICAD_SYM_DIRS,
    "${KICAD_SYMBOL_DIR}": _KICAD_SYM_DIRS,
    "${KIPRJMOD}": [],  # resolved at call time from sch_dir
}


def _resolve_lib_path(raw: str, sch_dir: Path) -> tuple[Path | None, str]:
    """Resolve a Sim.Library value to an absolute path.

    Returns (resolved_path_or_None, directive) where directive is one of:
      '.include "path"'   — standard SPICE library include
      '.lib "path"'       — alias (ngspice accepts both)
      ''                  — unresolvable (caller should emit a comment)
    """
    # Expand actual OS environment variables first
    p = os.path.expandvars(raw)

    # Substitute KiCad-specific variables not in the OS environment
    for var, candidates in _KICAD_ENV_VARS.items():
        if var in p:
            actual = os.environ.get(var.strip("${}"))
            if actual:
                p = p.replace(var, actual)
                break
            if var == "${KIPRJMOD}":
                p = p.replace(var, str(sch_dir))
                break
            for candidate in candidates:
                trial = Path(p.replace(var, candidate))
                if trial.exists():
                    p = str(trial)
                    break

    resolved = Path(p) if Path(p).is_absolute() else sch_dir / p
    resolved = resolved.resolve()

    suffix = resolved.suffix.lower()
    if suffix in (".kicad_sym",):
        # KiCad symbol library — models are embedded, not a SPICE .lib file.
        # We can't extract them here; return None so the caller emits a comment.
        return None, ""
    directive = ".include" if suffix in (".lib", ".mod", ".sp", ".cir", "") else ".include"
    if resolved.exists():
        return resolved, f'{directive} "{resolved}"'
    # Path doesn't exist on disk — emit it anyway so the user can see it
    return None, f"* Sim.Library not found: {resolved}"


# ---------------------------------------------------------------------------
# S-expression navigation (parser + helpers live in .kicad.sexpr)
# ---------------------------------------------------------------------------


def _unit_of(sym: list) -> int:
    n = _find(sym, "unit")
    if n:
        try:
            return int(_atom(n, 1, "1"))
        except ValueError:
            pass
    return 1


# ---------------------------------------------------------------------------
# Coordinate transform
# ---------------------------------------------------------------------------


def _xform(
    px: float, py: float, sx: float, sy: float, angle_deg: float, mirror_x: bool, mirror_y: bool
) -> tuple[float, float]:
    """Transform a lib-relative pin position to absolute schematic coordinates."""
    # Mirrors applied before rotation (in lib coordinate space)
    if mirror_x:
        py = -py
    if mirror_y:
        px = -px
    r = math.radians(angle_deg)
    rx = px * math.cos(r) - py * math.sin(r)
    ry = px * math.sin(r) + py * math.cos(r)
    return sx + rx, sy + ry


# ---------------------------------------------------------------------------
# Union-Find (points keyed at 1/1000 mm resolution to absorb float noise)
# ---------------------------------------------------------------------------


class _UF:
    def __init__(self) -> None:
        self._p: dict[tuple[int, int], tuple[int, int]] = {}

    def _k(self, pt: tuple[float, float]) -> tuple[int, int]:
        return (round(pt[0] * 1000), round(pt[1] * 1000))

    def find(self, pt: tuple[float, float]) -> tuple[int, int]:
        k = self._k(pt)
        if k not in self._p:
            self._p[k] = k
        root = k
        while self._p[root] != root:
            self._p[root] = self._p[self._p[root]]
            root = self._p[root]
        self._p[k] = root
        return root

    def union(self, a: tuple[float, float], b: tuple[float, float]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._p[rb] = ra


# ---------------------------------------------------------------------------
# Phase 1 — lib pin positions
# ---------------------------------------------------------------------------


def _extract_lib_pins(root: list) -> dict[str, dict[str, tuple[float, float]]]:
    """
    Returns  lib_id → {pin_number: (rel_x, rel_y)}
    where (rel_x, rel_y) is the electrical connection tip in lib coordinates.
    """
    ls = _find(root, "lib_symbols")
    if ls is None:
        return {}
    result: dict[str, dict[str, tuple[float, float]]] = {}
    for sym in _find_all(ls, "symbol"):
        lib_name = _atom(sym, 1)
        pins: dict[str, tuple[float, float]] = {}
        for sub in _find_all(sym, "symbol"):
            for pin in _find_all(sub, "pin"):
                num = _find(pin, "number")
                at = _find(pin, "at")
                if num and at:
                    pins[_atom(num, 1)] = (
                        float(_atom(at, 1, "0")),
                        float(_atom(at, 2, "0")),
                    )
        result[lib_name] = pins
    return result


# ---------------------------------------------------------------------------
# Phase 2 — wire connectivity
# ---------------------------------------------------------------------------


def _build_uf(root: list) -> _UF:
    uf = _UF()
    for tag in ("wire", "bus_wire"):
        for wire in _find_all(root, tag):
            pts = _find(wire, "pts")
            if pts is None:
                continue
            xys = _find_all(pts, "xy")
            if len(xys) >= 2:
                p1 = (float(_atom(xys[0], 1, "0")), float(_atom(xys[0], 2, "0")))
                p2 = (float(_atom(xys[1], 1, "0")), float(_atom(xys[1], 2, "0")))
                uf.union(p1, p2)
    return uf


# ---------------------------------------------------------------------------
# Phase 3 — net name assignment
# ---------------------------------------------------------------------------


def _assign_label_names(root: list, uf: _UF) -> dict[tuple[int, int], str]:
    names: dict[tuple[int, int], str] = {}

    def _set(pt: tuple[float, float], name: str, force: bool = False) -> None:
        k = uf.find(pt)
        if force or k not in names:
            names[k] = name

    for lbl in _find_all(root, "label"):
        name = _atom(lbl, 1)
        at = _find(lbl, "at")
        if name and at:
            _set((float(_atom(at, 1, "0")), float(_atom(at, 2, "0"))), _sanitize_net(name))

    for lbl in _find_all(root, "global_label"):
        name = _atom(lbl, 1)
        at = _find(lbl, "at")
        if name and at:
            spice = _sanitize_net(name)
            _set(
                (float(_atom(at, 1, "0")), float(_atom(at, 2, "0"))),
                spice,
                force=(spice == "0"),
            )

    for lbl in _find_all(root, "hierarchical_label"):
        name = _atom(lbl, 1)
        at = _find(lbl, "at")
        if name and at:
            _set((float(_atom(at, 1, "0")), float(_atom(at, 2, "0"))), _sanitize_net(name))

    return names


def _assign_power_names(
    root: list, lib_pins: dict, uf: _UF, names: dict[tuple[int, int], str]
) -> None:
    """Inject net names from power symbols (lib_id starting with 'power:')."""
    for sym in _find_all(root, "symbol"):
        lib_id_n = _find(sym, "lib_id")
        if lib_id_n is None:
            continue
        lib_id = _atom(lib_id_n, 1)
        if not lib_id.startswith("power:"):
            continue

        raw = _prop(sym, "Value") or lib_id.split(":")[-1]
        spice = _sanitize_net(raw)

        at = _find(sym, "at")
        if at is None:
            continue
        sx = float(_atom(at, 1, "0"))
        sy = float(_atom(at, 2, "0"))
        angle = float(_atom(at, 3, "0"))
        mir = _find(sym, "mirror")
        mx = isinstance(mir, list) and _atom(mir, 1) == "x"
        my = isinstance(mir, list) and _atom(mir, 1) == "y"

        lp = lib_pins.get(lib_id, {})
        if lp:
            px, py = next(iter(lp.values()))
            pt = _xform(px, py, sx, sy, angle, mx, my)
        else:
            pt = (sx, sy)

        k = uf.find(pt)
        if spice == "0" or k not in names:
            names[k] = spice


# ---------------------------------------------------------------------------
# Phase 4 — SPICE element lines
# ---------------------------------------------------------------------------

# Default SPICE pin ordering by element-type letter.
# Used when Sim.Pins is absent; pin keys must match the KiCad lib pin numbers.
_PIN_ORDER: dict[str, list[str]] = {
    "R": ["1", "2"],
    "C": ["1", "2"],
    "L": ["1", "2"],
    "V": ["1", "2"],
    "I": ["1", "2"],
    "E": ["1", "2", "3", "4"],
    "F": ["1", "2", "3", "4"],
    "G": ["1", "2", "3", "4"],
    "H": ["1", "2", "3", "4"],
    "D": ["A", "K"],
    "Q": ["C", "B", "E"],
    "M": ["D", "G", "S", "B"],
    "J": ["D", "G", "S"],
    "K": ["L1", "L2"],
    "T": ["A", "B", "C", "D"],
}


def _sorted_nets(pin_nets: dict[str, str]) -> list[str]:
    def _key(k: str) -> tuple:
        return (0, int(k)) if k.isdigit() else (1, k)

    return [pin_nets[k] for k in sorted(pin_nets, key=_key)]


def _apply_sim_pins(sim_pins: str, pin_nets: dict[str, str]) -> list[str]:
    """
    Interpret Sim.Pins ordering string, e.g. "1=A 2=K" or "A=1 K=2".
    Each token maps a SPICE position (digit) to a schematic pin number (alpha/num).
    """
    result = []
    for token in sim_pins.split():
        if "=" not in token:
            continue
        a, b = token.split("=", 1)
        if a.isdigit():
            result.append(pin_nets.get(b) or pin_nets.get(a) or "?")
        else:
            result.append(pin_nets.get(a) or pin_nets.get(b) or "?")
    return result or _sorted_nets(pin_nets)


def _make_line(
    ref: str, value: str, sim_type: str, sim_model: str, sim_pins: str, pin_nets: dict[str, str]
) -> str:
    etype = (sim_type or ref[0]).upper()

    if sim_pins:
        nets = _apply_sim_pins(sim_pins, pin_nets)
    elif etype in _PIN_ORDER:
        order = _PIN_ORDER[etype]
        by_order = [pin_nets[p] for p in order if p in pin_nets]
        if by_order:
            nets = by_order
            # MOSFET: bulk = source if not present in schematic
            if etype == "M" and len(nets) == 3:
                nets = nets + [nets[2]]
        else:
            # Pin numbers don't match _PIN_ORDER — use sorted nets but cap
            # to the expected node count so we don't emit extra nodes.
            all_nets = _sorted_nets(pin_nets)
            nets = all_nets[: len(order)]
    else:
        nets = _sorted_nets(pin_nets)

    model = sim_model if sim_model else value
    return f"{ref} {' '.join(nets)} {model}"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def import_kicad_sch(path: str | Path) -> list[str]:
    """Parse a KiCad 6/7 .kicad_sch and return a SPICE netlist as list[str]."""
    text = Path(path).read_text(encoding="utf-8")
    root = _parse_sexp(text)
    if not isinstance(root, list) or not root or root[0] != "kicad_sch":
        raise ValueError(f"Not a valid KiCad 6/7 .kicad_sch file: {path}")

    lib_pins = _extract_lib_pins(root)
    uf = _build_uf(root)
    names = _assign_label_names(root, uf)
    _assign_power_names(root, lib_pins, uf, names)

    def net_at(pt: tuple[float, float]) -> str:
        k = uf.find(pt)
        if k not in names:
            names[k] = f"N{k[0]}_{k[1]}"
        return names[k]

    # Group placed symbols by Reference so multi-unit ICs produce one SPICE line
    groups: dict[str, dict] = {}
    for sym in _find_all(root, "symbol"):
        lib_id_n = _find(sym, "lib_id")
        if lib_id_n is None:
            continue
        lib_id = _atom(lib_id_n, 1)
        if lib_id.startswith("power:"):
            continue

        ref = _prop(sym, "Reference")
        if not ref or ref.startswith("#"):
            continue
        if _prop(sym, "Sim.Enable").lower() in ("0", "false", "no"):
            continue

        at = _find(sym, "at")
        if at is None:
            continue
        sx = float(_atom(at, 1, "0"))
        sy = float(_atom(at, 2, "0"))
        angle = float(_atom(at, 3, "0"))
        mir = _find(sym, "mirror")
        mx = isinstance(mir, list) and _atom(mir, 1) == "x"
        my = isinstance(mir, list) and _atom(mir, 1) == "y"

        lp = lib_pins.get(lib_id, {})
        pin_nets: dict[str, str] = {}
        for pin_num, (px, py) in lp.items():
            pin_nets[pin_num] = net_at(_xform(px, py, sx, sy, angle, mx, my))

        if ref not in groups:
            groups[ref] = {
                "value": _prop(sym, "Value"),
                "sim_type": _prop(sym, "Sim.Type"),
                "sim_model": (_prop(sym, "Sim.SpiceModel") or _prop(sym, "Spice_Model")),
                "sim_pins": (_prop(sym, "Sim.Pins") or _prop(sym, "Spice_Node_Sequence")),
                "sim_lib": (_prop(sym, "Sim.Library") or _prop(sym, "Spice_Lib_File")),
                "pin_nets": {},
            }
        groups[ref]["pin_nets"].update(pin_nets)
        # Unit 1 properties override the initial values (which may come from any unit)
        if _unit_of(sym) == 1:
            for prop_key, kicad_key_new, kicad_key_old in (
                ("value", "Value", ""),
                ("sim_type", "Sim.Type", ""),
                ("sim_model", "Sim.SpiceModel", "Spice_Model"),
                ("sim_pins", "Sim.Pins", "Spice_Node_Sequence"),
                ("sim_lib", "Sim.Library", "Spice_Lib_File"),
            ):
                v = _prop(sym, kicad_key_new) or (
                    _prop(sym, kicad_key_old) if kicad_key_old else ""
                )
                if v:
                    groups[ref][prop_key] = v

    if not groups:
        raise ValueError("No SPICE elements found in schematic.")

    # Collect unique Sim.Library includes (preserving first-seen order)
    sch_dir = Path(path).parent
    seen_libs: dict[str, str] = {}  # raw lib path → directive or comment
    for g in groups.values():
        raw_lib = g.get("sim_lib", "")
        if raw_lib and raw_lib not in seen_libs:
            _resolved, directive = _resolve_lib_path(raw_lib, sch_dir)
            seen_libs[raw_lib] = directive

    netlist = [f"* Imported from {Path(path).name}"]
    for directive in seen_libs.values():
        if directive:
            netlist.append(directive)

    for ref in sorted(groups):
        g = groups[ref]
        if not g["pin_nets"]:
            continue
        netlist.append(
            _make_line(
                ref,
                g["value"],
                g["sim_type"],
                g["sim_model"],
                g["sim_pins"],
                g["pin_nets"],
            )
        )
    return netlist

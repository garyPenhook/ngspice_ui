"""Schematic import: .kicad_sch (KiCad 6/7) and .sch (Eagle XML) → SPICE netlist."""

from __future__ import annotations

from pathlib import Path


def import_schematic(path: str | Path) -> list[str]:
    """
    Import a schematic and return a SPICE netlist as list[str].

    Supported:
      .kicad_sch  KiCad 6/7 S-expression schematic
      .sch        Eagle XML schematic
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == '.kicad_sch':
        from .kicad_import import import_kicad_sch
        return import_kicad_sch(p)
    elif suffix == '.sch':
        from .eagle_import import import_eagle_sch
        return import_eagle_sch(p)
    else:
        raise ValueError(
            f"Unsupported schematic format: {suffix!r}. "
            "Expected .kicad_sch (KiCad 6/7) or .sch (Eagle XML)."
        )

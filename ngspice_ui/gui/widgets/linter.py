"""Lightweight SPICE netlist linter — static checks before simulation."""
from __future__ import annotations

import re
from pathlib import Path

_INCLUDE_RE = re.compile(r'^\s*\.(?:include|lib)\s+"?([^"\s]+)"?', re.IGNORECASE)
_MODEL_REF_RE = re.compile(r'^\s*[qmjd]\w+\s+.*?\s+(\w+)\s*$', re.IGNORECASE)
_SUBCKT_DEF_RE = re.compile(r'^\s*\.subckt\s+(\w+)', re.IGNORECASE)
_ENDS_RE = re.compile(r'^\s*\.ends\b', re.IGNORECASE)
_MODEL_DEF_RE = re.compile(r'^\s*\.model\s+(\w+)', re.IGNORECASE)
_VALUE_TYPO_RE = re.compile(r'\b(\d+(?:\.\d+)?)([KkM](?!EG|eg)[A-Za-z])', )


def lint(text: str, netlist_path: Path | None = None) -> list[tuple[int, str]]:
    """Return list of (1-based line number, message) for detected issues."""
    issues: list[tuple[int, str]] = []
    lines = text.splitlines()
    has_end = False
    subckt_depth = 0
    subckt_start_line: int | None = None
    defined_models: set[str] = set()
    defined_subckts: set[str] = set()
    seen_titles: list[int] = []

    for i, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("*"):
            continue

        lower = stripped.lower()

        if lower.startswith(".end") and not lower.startswith(".ends"):
            has_end = True

        if lower.startswith(".title"):
            seen_titles.append(i)
            if len(seen_titles) > 1:
                issues.append((i, "Multiple .title lines found"))

        m = _SUBCKT_DEF_RE.match(stripped)
        if m:
            subckt_depth += 1
            subckt_start_line = i
            defined_subckts.add(m.group(1).lower())

        if _ENDS_RE.match(stripped):
            if subckt_depth == 0:
                issues.append((i, ".ends without matching .subckt"))
            else:
                subckt_depth -= 1
                subckt_start_line = None

        m2 = _MODEL_DEF_RE.match(stripped)
        if m2:
            defined_models.add(m2.group(1).lower())

        m3 = _INCLUDE_RE.match(stripped)
        if m3 and netlist_path is not None:
            inc = m3.group(1)
            p = (netlist_path.parent / inc).resolve()
            if not p.exists():
                issues.append((i, f".include not found: {inc}"))

    if not has_end:
        issues.append((len(lines), "Missing .end statement"))

    if subckt_depth > 0 and subckt_start_line is not None:
        issues.append((subckt_start_line, f".subckt without matching .ends"))

    return sorted(issues, key=lambda x: x[0])

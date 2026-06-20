"""Minimal KiCad S-expression parser and navigation helpers.

Single source of truth shared by the netlist importer
(:mod:`ngspice_ui.schematic.kicad_import`) and the graphical viewer
(:mod:`ngspice_ui.gui.widgets.schematic_view`). Pure functions — no Qt, no
libngspice — so they are directly unit-testable.
"""

from __future__ import annotations

from typing import Optional


def parse_sexp(text: str) -> list:
    """Tokenise and parse a KiCad S-expression into nested Python lists.

    Strings are unescaped; bare atoms are returned as ``str``. The top-level
    node is returned as a list; an empty input yields ``[]``.

    Raises ``ValueError`` for malformed input — an unterminated list or quoted
    string, or trailing data after the top-level expression — so a truncated or
    damaged schematic fails loudly instead of being partially imported.
    """
    pos = 0
    n = len(text)

    def skip_ws() -> None:
        nonlocal pos
        while pos < n and text[pos] in " \t\n\r":
            pos += 1

    def read_node():
        nonlocal pos
        skip_ws()
        if pos >= n:
            return None
        c = text[pos]
        if c == "(":
            pos += 1
            children = []
            while True:
                skip_ws()
                if pos >= n:
                    raise ValueError("Unterminated S-expression (missing ')')")
                if text[pos] == ")":
                    pos += 1
                    break
                child = read_node()
                if child is not None:
                    children.append(child)
            return children
        elif c == '"':
            pos += 1
            parts: list[str] = []
            while pos < n and text[pos] != '"':
                if text[pos] == "\\":
                    pos += 1
                    if pos < n:
                        parts.append(text[pos])
                else:
                    parts.append(text[pos])
                pos += 1
            if pos >= n:
                raise ValueError("Unterminated string in S-expression")
            pos += 1  # closing "
            return "".join(parts)
        else:
            start = pos
            while pos < n and text[pos] not in " \t\n\r()":
                pos += 1
            return text[start:pos]

    skip_ws()
    node = read_node()
    if node is None:
        return []
    # Anything other than whitespace after the root means the file is damaged
    # or contains concatenated expressions that would be silently dropped.
    skip_ws()
    if pos < n:
        raise ValueError("Trailing data after top-level S-expression")
    return node


def find(node: list, tag: str) -> Optional[list]:
    """First direct child list of *node* whose head atom equals *tag*."""
    for item in node:
        if isinstance(item, list) and item and item[0] == tag:
            return item
    return None


def find_all(node: list, tag: str) -> list[list]:
    """All direct child lists of *node* whose head atom equals *tag*."""
    return [item for item in node if isinstance(item, list) and item and item[0] == tag]


def atom(node: list, idx: int, default: str = "") -> str:
    """String element at *idx*, or *default* if missing/non-string."""
    try:
        v = node[idx]
        return v if isinstance(v, str) else default
    except IndexError:
        return default


def prop(sym: list, name: str) -> str:
    """Value of a KiCad ``(property "name" "value" ...)`` by name, else ''."""
    for p in find_all(sym, "property"):
        if atom(p, 1) == name:
            return atom(p, 2)
    return ""


def sub_symbol_unit(name: str) -> int:
    """Unit index from a KiCad lib sub-symbol name ``BASE_<unit>_<bodystyle>``.

    The ``BASE`` portion may itself contain underscores, so the unit and body
    style are the last two ``_``-separated tokens. Unit 0 holds elements common
    to every unit. Returns 0 when the name does not follow the convention.
    """
    parts = name.rsplit("_", 2)
    if len(parts) == 3:
        try:
            return int(parts[1])
        except ValueError:
            pass
    return 0

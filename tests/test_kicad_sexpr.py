"""Unit tests for the shared KiCad S-expression parser and helpers.

Pure functions — no Qt, no libngspice.
"""
from __future__ import annotations

from ngspice_ui.schematic.kicad.sexpr import (
    atom,
    find,
    find_all,
    parse_sexp,
    prop,
)


def test_parse_nested_lists():
    assert parse_sexp("(a (b 1) (c 2))") == ["a", ["b", "1"], ["c", "2"]]


def test_parse_empty_input():
    assert parse_sexp("") == []
    assert parse_sexp("   ") == []


def test_parse_quoted_strings_with_escapes():
    node = parse_sexp(r'(label "hello \"world\"")')
    assert node == ["label", 'hello "world"']


def test_parse_quoted_string_preserves_spaces_and_parens():
    node = parse_sexp('(property "a b (c)")')
    assert node == ["property", "a b (c)"]


def test_find_returns_first_match():
    node = parse_sexp("(root (x 1) (x 2) (y 3))")
    assert find(node, "x") == ["x", "1"]
    assert find(node, "missing") is None


def test_find_all_returns_every_match():
    node = parse_sexp("(root (x 1) (x 2) (y 3))")
    assert find_all(node, "x") == [["x", "1"], ["x", "2"]]
    assert find_all(node, "z") == []


def test_atom_index_and_default():
    node = ["wire", "10", "20"]
    assert atom(node, 1) == "10"
    assert atom(node, 5) == ""
    assert atom(node, 5, default="d") == "d"


def test_atom_non_string_returns_default():
    node = ["pts", ["xy", "1", "2"]]
    assert atom(node, 1) == ""        # element is a list, not a string
    assert atom(node, 1, "x") == "x"


def test_prop_lookup_by_name():
    sym = parse_sexp(
        '(symbol (property "Reference" "R1") (property "Value" "1k"))'
    )
    assert prop(sym, "Reference") == "R1"
    assert prop(sym, "Value") == "1k"
    assert prop(sym, "Absent") == ""

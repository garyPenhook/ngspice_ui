"""Tests for the versioned ProjectDocument model and serializer.

Pure-data tests — no libngspice, no Qt — so they always run in CI.
"""
from __future__ import annotations

import json

import pytest

from ngspice_ui.models.project import (
    CURRENT_VERSION,
    ProjectDocument,
    ProjectError,
)


def _full_doc() -> ProjectDocument:
    return ProjectDocument(
        netlist="* title\nR1 1 0 1k\n.end",
        analysis={"key": "tran", "params": {"tstep": "1us", "tstop": "1ms"}},
        measurements=[{"name": "vout", "expr": "v(out)"}],
        notes="some notes",
        script={"text": "print all"},
        cosim={"enabled": True},
    )


def test_round_trip_preserves_all_fields():
    doc = _full_doc()
    restored = ProjectDocument.loads(doc.dumps())
    assert restored == doc


def test_dumps_is_valid_json_with_version():
    doc = _full_doc()
    raw = json.loads(doc.dumps())
    assert raw["version"] == CURRENT_VERSION
    assert raw["netlist"] == doc.netlist


def test_defaults_for_missing_fields():
    doc = ProjectDocument.from_dict({"version": 2})
    assert doc.netlist == ""
    assert doc.analysis == {}
    assert doc.measurements == []
    assert doc.notes == ""
    assert doc.script == {}
    assert doc.cosim == {}


def test_empty_object_yields_all_defaults():
    doc = ProjectDocument.from_dict({})
    assert doc == ProjectDocument()


def test_wrong_typed_fields_fall_back_to_defaults():
    raw = {
        "netlist": 123,            # should be str
        "analysis": ["nope"],      # should be dict
        "measurements": {"x": 1},  # should be list
        "notes": None,             # should be str
        "script": 5,               # should be dict
        "cosim": "x",              # should be dict
    }
    doc = ProjectDocument.from_dict(raw)
    assert doc.netlist == ""
    assert doc.analysis == {}
    assert doc.measurements == []
    assert doc.notes == ""
    assert doc.script == {}
    assert doc.cosim == {}


def test_non_object_root_is_fatal():
    with pytest.raises(ProjectError):
        ProjectDocument.from_dict([1, 2, 3])
    with pytest.raises(ProjectError):
        ProjectDocument.from_dict("a string")


def test_bad_json_raises_project_error():
    with pytest.raises(ProjectError):
        ProjectDocument.loads("{ not valid json ]")


def test_non_int_version_falls_back():
    doc = ProjectDocument.from_dict({"version": "two"})
    assert doc.version == CURRENT_VERSION


def test_unknown_keys_are_ignored():
    doc = ProjectDocument.from_dict({"netlist": "x", "future_field": 42})
    assert doc.netlist == "x"
    assert not hasattr(doc, "future_field")

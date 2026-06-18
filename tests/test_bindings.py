"""Tests for library discovery and raw ctypes bindings."""

import pytest

from ngspice_ui.engine.bindings import NgSpiceNotFoundError, _find_lib_path, get_lib


def test_find_lib_path_returns_string():
    """find_lib_path must return a non-empty string on a system with ngspice."""
    path = _find_lib_path()
    assert isinstance(path, str) and len(path) > 0


def test_find_lib_path_env_override(monkeypatch, tmp_path):
    """NGSPICE_LIB env var is returned verbatim (path need not exist for resolution)."""
    fake = str(tmp_path / "libngspice.so")
    monkeypatch.setenv("NGSPICE_LIB", fake)
    assert _find_lib_path() == fake


def test_find_lib_missing_raises(monkeypatch):
    """NgSpiceNotFoundError is raised with an actionable message when the lib is absent."""
    monkeypatch.setenv("NGSPICE_LIB", "")
    import ctypes.util
    monkeypatch.setattr(ctypes.util, "find_library", lambda _: None)
    import glob as _glob
    monkeypatch.setattr(_glob, "glob", lambda *a, **kw: [])
    with pytest.raises(NgSpiceNotFoundError, match="NGSPICE_LIB"):
        _find_lib_path()


def test_get_lib_loads():
    """get_lib() returns a loaded CDLL with key symbols present."""
    lib = get_lib()
    assert hasattr(lib, "ngSpice_Init")
    assert hasattr(lib, "ngSpice_Command")
    assert hasattr(lib, "ngGet_Vec_Info")

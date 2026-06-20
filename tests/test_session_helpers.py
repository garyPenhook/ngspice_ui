"""Unit tests for pure NgSpiceSession helpers (no libngspice required).

These exercise the relative-include rewriting that fixes ``.include local.lib``
failing when the application's working directory differs from the deck's.
"""

from __future__ import annotations

from ngspice_ui.engine.session import _rewrite_includes


def test_relative_include_rewritten_to_absolute(tmp_path):
    (tmp_path / "local.lib").write_text("* a model", encoding="utf-8")
    out = _rewrite_includes([".include local.lib"], tmp_path)
    assert out == [f'.include "{(tmp_path / "local.lib").resolve()}"']


def test_relative_lib_with_section_keeps_trailing_token(tmp_path):
    (tmp_path / "models.lib").write_text("* models", encoding="utf-8")
    out = _rewrite_includes([".lib models.lib typical"], tmp_path)
    assert out == [f'.lib "{(tmp_path / "models.lib").resolve()}" typical']


def test_quoted_relative_include_rewritten(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "m.lib").write_text("* m", encoding="utf-8")
    out = _rewrite_includes(['.include "sub/m.lib"'], tmp_path)
    assert out == [f'.include "{(sub / "m.lib").resolve()}"']


def test_absolute_paths_left_untouched(tmp_path):
    line = '.include "/opt/spice/lib/std.lib"'
    assert _rewrite_includes([line], tmp_path) == [line]


def test_nonexistent_relative_path_left_untouched(tmp_path):
    # Never invent a path: an in-deck ``.lib`` section reference (not a file) and
    # any missing relative path are preserved verbatim so behaviour is unchanged
    # where there is nothing to resolve.
    lines = [".include missing.inc", ".lib typical"]
    assert _rewrite_includes(lines, tmp_path) == lines


def test_ordinary_lines_unchanged(tmp_path):
    lines = ["* title", "R1 in out 1k", ".tran 1u 1m", ".end"]
    assert _rewrite_includes(lines, tmp_path) == lines

"""Tests for co-simulation widget lifecycle and robustness.

Covers the runtime-callback leak fix (set_config must unregister previously
applied callbacks) and defensive parsing of malformed nested project data.
Requires PySide6 but not libngspice (a fake session records init_sync calls).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ngspice_ui.gui.widgets.cosim_widget import CoSimWidget, _compile_expr  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeSession:
    def __init__(self):
        self.sync_calls: list[tuple] = []

    def init_sync(self, vsrc_fn, isrc_fn, sync_fn):
        self.sync_calls.append((vsrc_fn, isrc_fn, sync_fn))


def test_set_config_unregisters_previous_callbacks(qapp):
    w = CoSimWidget()
    session = FakeSession()
    w.set_session(session)
    # Loading a new project's config must clear any callbacks a prior project
    # left registered with the engine.
    w.set_config({"rows": [], "sync": ""})
    assert session.sync_calls == [(None, None, None)]


def test_set_config_tolerates_malformed_data(qapp):
    w = CoSimWidget()
    w.set_session(FakeSession())
    # None config, non-list rows, and non-dict row entries must not raise.
    w.set_config(None)
    w.set_config({"rows": "not a list", "sync": 123})
    w.set_config({"rows": [None, 42, {"name": 5, "expr": None, "type": None}]})


def test_invalid_expression_is_rejected(qapp):
    w = CoSimWidget()
    session = FakeSession()
    w.set_session(session)
    w.set_config(
        {"rows": [{"enabled": True, "name": "vext", "type": "V", "expr": "__import__('os')"}]}
    )
    calls_before = len(session.sync_calls)  # set_config issues one clear
    w._apply()
    # A disallowed expression must not register any callback dispatch.
    assert "row 1" in w._status.text() or "not allowed" in w._status.text()
    assert len(session.sync_calls) == calls_before  # _apply bailed before init_sync


def test_compiled_source_enforces_runtime_guards(qapp):
    # A valid co-sim source compiles and evaluates per-timestep.
    fn = _compile_expr("np.sin(t)", "row 1 (vext)")
    assert fn(0.0, "vext") == pytest.approx(0.0)

    # The source path now routes through the same ** / * guards as safe_eval, so
    # an expression that validates but would build a giant sequence at call time
    # is refused inside the callback instead of hanging the simulation.
    blowup = _compile_expr("[t] * 1000000000", "row 1 (vext)")
    with pytest.raises(ValueError, match="oversized sequence"):
        blowup(1.0, "vext")

"""Tests for SimController orchestration (param sweep + Monte Carlo).

The coordinator's *sequencing* is separable from libngspice: we inject a fake
session that records loaded netlists, and drive completion by emitting
sim_finished manually. Requires PySide6 (a hard GUI dependency) but not the
ngspice shared library.
"""
from __future__ import annotations

import queue

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402

from ngspice_ui.gui.controllers.sim_controller import SimController  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


class FakeSession:
    """Minimal stand-in for NgSpiceSession used by the controller."""

    def __init__(self):
        self.event_queue: queue.Queue = queue.Queue()
        self.loaded: list[list[str]] = []
        self.run_count = 0

    def load_netlist(self, lines):
        self.loaded.append(list(lines))

    def bg_run(self):
        self.run_count += 1

    def bg_halt(self):
        pass

    def bg_resume(self):
        pass


def _make_controller():
    session = FakeSession()
    ctrl = SimController(session=session)
    ctrl._drain_timer.stop()  # no event loop in tests; avoid stray ticks
    return ctrl, session


def test_run_param_sweep_prepends_step_lines(qapp):
    ctrl, session = _make_controller()
    ctrl.run_param_sweep("R1 1 0 1k", [".step param r 1k 10k 1k"], "tran 1u 1m")
    assert session.run_count == 1
    loaded = session.loaded[0]
    assert loaded[0] == ".step param r 1k 10k 1k"
    assert "R1 1 0 1k" in loaded
    assert loaded[-1] == "tran 1u 1m"


def test_run_param_sweep_without_step_lines(qapp):
    ctrl, session = _make_controller()
    ctrl.run_param_sweep("R1 1 0 1k", [], "tran 1u 1m")
    assert session.run_count == 1
    assert not session.loaded[0][0].startswith(".step")


def test_monte_carlo_runs_in_order(qapp):
    ctrl, session = _make_controller()
    progress: list[tuple[int, int]] = []
    finished: list[int] = []
    ctrl.mc_progress.connect(lambda i, t: progress.append((i, t)))
    ctrl.mc_finished.connect(lambda t: finished.append(t))

    netlists = ["* run A\nRA 1 0 1", "* run B\nRB 1 0 2", "* run C\nRC 1 0 3"]
    ctrl.run_monte_carlo(netlists, "op")

    # First run starts immediately.
    assert session.run_count == 1
    assert progress == [(1, 3)]

    # Each sim_finished advances the queue.
    ctrl.sim_finished.emit()
    assert session.run_count == 2
    ctrl.sim_finished.emit()
    assert session.run_count == 3

    # Final completion emits mc_finished and stops advancing.
    ctrl.sim_finished.emit()
    assert finished == [3]
    assert progress == [(1, 3), (2, 3), (3, 3)]

    # The marker text from each netlist appears in load order.
    titles = [lines[0] for lines in session.loaded]
    assert titles == ["* run A", "* run B", "* run C"]


def test_monte_carlo_disconnects_after_completion(qapp):
    ctrl, session = _make_controller()
    finished: list[int] = []
    ctrl.mc_finished.connect(lambda t: finished.append(t))

    ctrl.run_monte_carlo(["* only\nR 1 0 1"], "op")
    ctrl.sim_finished.emit()      # completes the single run
    assert finished == [1]

    # A stray sim_finished after completion must not re-trigger anything.
    runs_after = session.run_count
    ctrl.sim_finished.emit()
    assert session.run_count == runs_after
    assert finished == [1]


def test_monte_carlo_empty_list_is_noop(qapp):
    ctrl, session = _make_controller()
    ctrl.run_monte_carlo([], "op")
    assert session.run_count == 0

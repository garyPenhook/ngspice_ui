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


def test_run_param_sweep_inserts_step_lines_after_title(qapp):
    ctrl, session = _make_controller()
    ctrl.run_param_sweep(
        "* RC low-pass\nR1 1 2 1k\nC1 2 0 1n",
        [".step param r 1k 10k 1k"],
        "tran 1u 1m",
    )
    assert session.run_count == 1
    loaded = session.loaded[0]
    # SPICE title (line 0) must be preserved as-is
    assert loaded[0] == "* RC low-pass"
    # Step directive must follow the title, not precede it
    assert loaded[1] == ".step param r 1k 10k 1k"
    # Analysis line at the end, netlist body in between
    assert "R1 1 2 1k" in loaded
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


# ------------------------------------------------------------------
# BGThreadEvent lifecycle
#
# Manual §15.3.3.6: the raw NG_BOOL from ngspice is *false* while the
# background thread is running and *true* once it has stopped.
# callbacks._bg_thread_running normalises this so that:
#   BGThreadEvent(running=True)  → sim_started  (thread just started)
#   BGThreadEvent(running=False) → sim_finished (thread just stopped)
# These tests inject already-normalised BGThreadEvents (bypassing the
# callback) and verify the controller's signal dispatch.
# ------------------------------------------------------------------

from ngspice_ui.engine.callbacks import BGThreadEvent  # noqa: E402


def test_bg_thread_event_running_true_emits_sim_started(qapp):
    ctrl, session = _make_controller()
    started: list[int] = []
    finished: list[int] = []
    ctrl.sim_started.connect(lambda: started.append(1))
    ctrl.sim_finished.connect(lambda: finished.append(1))

    session.event_queue.put_nowait(BGThreadEvent(running=True))
    ctrl._drain_queue()

    assert started == [1]
    assert finished == []


def test_bg_thread_event_running_false_emits_sim_finished(qapp):
    ctrl, session = _make_controller()
    started: list[int] = []
    finished: list[int] = []
    ctrl.sim_started.connect(lambda: started.append(1))
    ctrl.sim_finished.connect(lambda: finished.append(1))

    session.event_queue.put_nowait(BGThreadEvent(running=False))
    ctrl._drain_queue()

    assert finished == [1]
    assert started == []

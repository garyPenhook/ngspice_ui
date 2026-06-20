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


def test_run_param_sweep_runs_each_netlist_sequentially(qapp):
    ctrl, session = _make_controller()
    progress: list[tuple[int, int, str]] = []
    ctrl.sequence_progress.connect(lambda i, t, k: progress.append((i, t, k)))

    netlists = [
        "* sweep\nR1 1 0 1k\n.param r=100",
        "* sweep\nR1 1 0 1k\n.param r=1k",
    ]
    ctrl.run_param_sweep(netlists, "op")

    # First pass starts immediately; .step must never reach the engine.
    assert session.run_count == 1
    assert progress == [(1, 2, "Sweep")]
    assert not any(ln.strip().lower().startswith(".step") for ln in session.loaded[0])

    ctrl.sim_finished.emit()
    assert session.run_count == 2
    # The per-pass .param override survives into the loaded netlist.
    assert ".param r=1k" in session.loaded[1]


def test_monte_carlo_runs_in_order(qapp):
    ctrl, session = _make_controller()
    progress: list[tuple[int, int, str]] = []
    finished: list[tuple[int, str]] = []
    ctrl.sequence_progress.connect(lambda i, t, k: progress.append((i, t, k)))
    ctrl.sequence_finished.connect(lambda t, k: finished.append((t, k)))

    netlists = ["* run A\nRA 1 0 1", "* run B\nRB 1 0 2", "* run C\nRC 1 0 3"]
    ctrl.run_monte_carlo(netlists, "op")

    # First run starts immediately.
    assert session.run_count == 1
    assert progress == [(1, 3, "Monte Carlo")]

    # Each sim_finished advances the queue.
    ctrl.sim_finished.emit()
    assert session.run_count == 2
    ctrl.sim_finished.emit()
    assert session.run_count == 3

    # Final completion emits sequence_finished and stops advancing.
    ctrl.sim_finished.emit()
    assert finished == [(3, "Monte Carlo")]
    assert progress == [
        (1, 3, "Monte Carlo"),
        (2, 3, "Monte Carlo"),
        (3, 3, "Monte Carlo"),
    ]

    # The marker text from each netlist appears in load order.
    titles = [lines[0] for lines in session.loaded]
    assert titles == ["* run A", "* run B", "* run C"]


def test_sequence_disconnects_after_completion(qapp):
    ctrl, session = _make_controller()
    finished: list[tuple[int, str]] = []
    ctrl.sequence_finished.connect(lambda t, k: finished.append((t, k)))

    ctrl.run_monte_carlo(["* only\nR 1 0 1"], "op")
    ctrl.sim_finished.emit()  # completes the single run
    assert finished == [(1, "Monte Carlo")]

    # A stray sim_finished after completion must not re-trigger anything.
    runs_after = session.run_count
    ctrl.sim_finished.emit()
    assert session.run_count == runs_after
    assert finished == [(1, "Monte Carlo")]


def test_sequence_empty_list_is_noop(qapp):
    ctrl, session = _make_controller()
    ctrl.run_monte_carlo([], "op")
    ctrl.run_param_sweep([], "op")
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

from ngspice_ui.engine.callbacks import (  # noqa: E402
    BGThreadEvent,
    CharEvent,
    DataPointEvent,
)


def test_error_line_marks_run_failed(qapp):
    ctrl, session = _make_controller()
    ctrl.run()  # resets the error flag
    assert ctrl.last_run_had_errors is False
    # An error without a parseable line number must still flag the run.
    session.event_queue.put_nowait(CharEvent(line="Error: incomplete or empty netlist"))
    ctrl._drain_queue()
    assert ctrl.last_run_had_errors is True


def test_completion_not_starved_by_data_backlog(qapp):
    ctrl, session = _make_controller()
    finished: list[int] = []
    ctrl.sim_finished.connect(lambda: finished.append(1))
    # Far more data points than the per-drain forward cap, then completion.
    for i in range(ctrl._MAX_DATA_EVENTS_PER_DRAIN * 3):
        session.event_queue.put_nowait(
            DataPointEvent(vec_index=i, values={"v(out)": 0j}, scale_name="time")
        )
    session.event_queue.put_nowait(BGThreadEvent(running=False))
    ctrl._drain_queue()
    # A single drain must reach the completion event despite the backlog.
    assert finished == [1]


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

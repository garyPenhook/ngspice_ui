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

    def load_netlist(self, lines, base_dir=None):
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


def test_sequence_does_not_hang_when_a_run_fails_to_start(qapp):
    """A load/bg_run error emits no sim_finished, so the sequence must advance
    itself rather than stalling forever on a completion signal that never comes.
    """

    class FailingLoadSession(FakeSession):
        def __init__(self, fail_on):
            super().__init__()
            self._fail_on = fail_on

        def load_netlist(self, lines, base_dir=None):
            super().load_netlist(lines, base_dir)
            # Second netlist fails to load (e.g. ngSpice_Circ error).
            if len(self.loaded) == self._fail_on:
                raise RuntimeError("synthetic load failure")

    session = FailingLoadSession(fail_on=2)
    ctrl = SimController(session=session)
    ctrl._drain_timer.stop()
    finished: list[tuple[int, str]] = []
    ctrl.sequence_finished.connect(lambda t, k: finished.append((t, k)))

    ctrl.run_monte_carlo(["* A\nRA 1 0 1", "* B\nRB 1 0 2", "* C\nRC 1 0 3"], "op")
    # Run 1 started.
    assert session.run_count == 1
    # Completion of run 1 advances; run 2 fails to start, so the controller must
    # skip straight to run 3 inline (no sim_finished arrives for the failed run).
    ctrl.sim_finished.emit()
    assert session.run_count == 2  # run 3 actually started (run 2 never ran)
    # Completion of run 3 finalises the whole sequence.
    ctrl.sim_finished.emit()
    assert finished == [(3, "Monte Carlo")]


def test_sequence_completes_when_every_run_fails_to_start(qapp):
    class AlwaysFailSession(FakeSession):
        def load_netlist(self, lines, base_dir=None):
            super().load_netlist(lines, base_dir)
            raise RuntimeError("synthetic load failure")

    session = AlwaysFailSession()
    ctrl = SimController(session=session)
    ctrl._drain_timer.stop()
    finished: list[tuple[int, str]] = []
    ctrl.sequence_finished.connect(lambda t, k: finished.append((t, k)))

    ctrl.run_param_sweep(["* A\nRA 1 0 1", "* B\nRB 1 0 2"], "op")
    # No run ever started, but the sequence must still finalise instead of hang.
    assert session.run_count == 0
    assert finished == [(2, "Sweep")]


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


def test_title_containing_error_does_not_fail_run(qapp):
    # Regression: a valid circuit titled "Error amplifier test" is echoed by
    # libngspice as "Circuit: error amplifier test" (here behind a stdout tag).
    # The word "error" mid-line must no longer flag the run as failed.
    ctrl, session = _make_controller()
    ctrl.run()
    session.event_queue.put_nowait(CharEvent(line="stdout Circuit: error amplifier test"))
    ctrl._drain_queue()
    assert ctrl.last_run_had_errors is False


def test_real_stderr_error_still_marks_run_failed(qapp):
    ctrl, session = _make_controller()
    ctrl.run()
    session.event_queue.put_nowait(CharEvent(line="stderr Error on line 2 or its substitute:"))
    ctrl._drain_queue()
    assert ctrl.last_run_had_errors is True


def test_simulation_interrupted_phrase_marks_run_failed(qapp):
    ctrl, session = _make_controller()
    ctrl.run()
    session.event_queue.put_nowait(CharEvent(line="stderr Simulation interrupted due to error!"))
    ctrl._drain_queue()
    assert ctrl.last_run_had_errors is True


def test_warning_line_does_not_fail_run(qapp):
    # ngspice warnings go to stderr too but are not errors.
    ctrl, session = _make_controller()
    ctrl.run()
    session.event_queue.put_nowait(CharEvent(line="stderr warning, can't find model 'x' from line"))
    ctrl._drain_queue()
    assert ctrl.last_run_had_errors is False


def test_real_simulation_aborted_line_marks_run_failed(qapp):
    # ngspice prints "run simulation(s) aborted" (note the "(s)"); the old
    # pattern matched only "simulation aborted", so real aborts slipped through.
    ctrl, session = _make_controller()
    ctrl.run()
    session.event_queue.put_nowait(CharEvent(line="stderr run simulation(s) aborted"))
    ctrl._drain_queue()
    assert ctrl.last_run_had_errors is True


def test_timestep_too_small_with_nonzero_step_fails_run(qapp):
    # A genuine mid-run convergence failure: tiny but NONZERO step, time well
    # below tstop, named cause.
    ctrl, session = _make_controller()
    ctrl.run()
    session.event_queue.put_nowait(
        CharEvent(
            line="doAnalyses: TRAN:  Timestep too small; time = 3e-05, timestep = 1.25e-16: "
            'trouble with node "p"'
        )
    )
    session.event_queue.put_nowait(CharEvent(line="stderr run simulation(s) aborted"))
    ctrl._drain_queue()
    assert ctrl.last_run_had_errors is True


def test_zero_timestep_with_named_cause_is_clean_not_failure(qapp):
    # Regression: the end-of-run zero-step (timestep = 0 at tstop) is a clean
    # completion even when ngspice attributes a node ("trouble with node ...") —
    # keying off the cause text alone wrongly discarded valid data (e.g. the
    # half-wave rectifier example, which reached tstop with full data).
    ctrl, session = _make_controller()
    ctrl.run()
    session.event_queue.put_nowait(
        CharEvent(
            line="doAnalyses: TRAN:  Timestep too small; time = 0.003, timestep = 0: "
            'trouble with node "v1#branch"'
        )
    )
    session.event_queue.put_nowait(CharEvent(line="stderr run simulation(s) aborted"))
    ctrl._drain_queue()
    assert ctrl.last_run_had_errors is False


def test_benign_end_of_run_timestep_is_clean_not_failure(qapp):
    # "cause unrecorded" zero-step at the stop time is a clean completion.
    ctrl, session = _make_controller()
    ctrl.run()
    session.event_queue.put_nowait(
        CharEvent(
            line="doAnalyses: TRAN:  Timestep too small; time = 0.003, timestep = 0: "
            "cause unrecorded."
        )
    )
    session.event_queue.put_nowait(CharEvent(line="stderr run simulation(s) aborted"))
    ctrl._drain_queue()
    assert ctrl.last_run_had_errors is False


def test_source_stepping_failure_marks_run_failed(qapp):
    # Terminal operating-point non-convergence (singular matrix circuits).
    ctrl, session = _make_controller()
    ctrl.run()
    session.event_queue.put_nowait(CharEvent(line="Warning: source stepping failed"))
    ctrl._drain_queue()
    assert ctrl.last_run_had_errors is True


def test_recoverable_gmin_stepping_does_not_fail_run(qapp):
    # Dynamic-gmin stepping can fail and the run still converge via another
    # method, so that line alone must not flag the run.
    ctrl, session = _make_controller()
    ctrl.run()
    session.event_queue.put_nowait(CharEvent(line="Warning: Dynamic gmin stepping failed"))
    ctrl._drain_queue()
    assert ctrl.last_run_had_errors is False


def test_user_halt_abort_line_is_not_a_failure(qapp):
    # After an explicit halt, ngspice's "aborted" line must not read as a failure.
    ctrl, session = _make_controller()
    ctrl.run()
    ctrl.halt()
    session.event_queue.put_nowait(CharEvent(line="stderr run simulation(s) aborted"))
    ctrl._drain_queue()
    assert ctrl.last_run_had_errors is False


def test_benign_timestep_then_hard_error_still_fails(qapp):
    # A benign zero-step followed by a real error in a later analysis must fail.
    ctrl, session = _make_controller()
    ctrl.run()
    session.event_queue.put_nowait(
        CharEvent(
            line="doAnalyses: TRAN:  Timestep too small; time = 0.003, timestep = 0: "
            "cause unrecorded."
        )
    )
    session.event_queue.put_nowait(CharEvent(line="stderr Error: something fatal"))
    ctrl._drain_queue()
    assert ctrl.last_run_had_errors is True


def test_benign_state_is_cleared_so_next_runs_real_abort_is_caught(qapp):
    # The benign-abort suppression is per-run: a bare abort in a later run (no
    # preceding benign zero-step) must still fail.
    ctrl, session = _make_controller()
    ctrl.run()
    session.event_queue.put_nowait(
        CharEvent(
            line="doAnalyses: TRAN:  Timestep too small; time = 0.003, timestep = 0: "
            "cause unrecorded."
        )
    )
    ctrl._drain_queue()
    assert ctrl.last_run_had_errors is False
    ctrl.run()  # _begin_run resets per-run state
    session.event_queue.put_nowait(CharEvent(line="stderr run simulation(s) aborted"))
    ctrl._drain_queue()
    assert ctrl.last_run_had_errors is True


def test_halt_flag_is_cleared_so_next_runs_real_abort_is_caught(qapp):
    # Guards the stale-flag risk: a halt must not suppress a *later* run's abort.
    ctrl, session = _make_controller()
    ctrl.run()
    ctrl.halt()
    ctrl.run()  # _begin_run must clear _run_halted
    session.event_queue.put_nowait(CharEvent(line="stderr run simulation(s) aborted"))
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

"""Focused tests for MainWindow's stale-completion guard (audit finding #7).

Constructing the full window pulls in every widget and a real libngspice
session, so these exercise the guard logic on a bare instance (``__new__`` with
only the attributes the methods touch stubbed). That keeps the test on the pure
decision — does a completion that arrives after the project changed get applied
to the new project? — without GUI or engine dependencies.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from ngspice_ui.gui.main_window import MainWindow  # noqa: E402


class _Action:
    def __init__(self) -> None:
        self.enabled: bool | None = None

    def setEnabled(self, value: bool) -> None:
        self.enabled = value


class _Progress:
    def setVisible(self, value: bool) -> None:
        self.visible = value


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def append_line(self, msg: str) -> None:
        self.lines.append(msg)


def _bare_window(*, run_epoch: int, project_epoch: int, sim_halted: bool = False) -> MainWindow:
    mw = MainWindow.__new__(MainWindow)
    mw._sim_start_time = 0.0
    mw._run_epoch = run_epoch
    mw._project_epoch = project_epoch
    mw._sim_halted = sim_halted
    mw._act_run = _Action()
    mw._act_stop = _Action()
    mw._act_resume = _Action()
    mw._progress = _Progress()
    mw._console = _Console()
    return mw


def test_stale_completion_is_discarded_not_applied():
    # Run started under epoch 4; a project load bumped the epoch to 5 before the
    # completion arrived. The old run's data must not be snapshotted/evaluated
    # against the new project — it is discarded and the toolbar returns to idle.
    mw = _bare_window(run_epoch=4, project_epoch=5)
    mw._on_sim_finished()
    assert any("discarded" in line for line in mw._console.lines)
    assert mw._act_run.enabled is True
    assert mw._act_stop.enabled is False
    assert mw._act_resume.enabled is False


def test_current_completion_is_not_discarded():
    # Same epoch → not stale: the guard must let processing continue. We stub a
    # failed run so it returns at the error branch (no snapshot deps needed) and
    # confirm it did NOT take the discard path.
    mw = _bare_window(run_epoch=5, project_epoch=5)
    statuses: list[str] = []
    mw._set_status = statuses.append  # type: ignore[method-assign]

    class _Ctrl:
        last_run_had_errors = True

    mw._controller = _Ctrl()  # type: ignore[assignment]
    mw._on_sim_finished()
    assert not any("discarded" in line for line in mw._console.lines)
    assert any("failed" in line.lower() for line in mw._console.lines)


def test_benign_warning_completion_is_flagged_but_not_failed():
    # A run that finished with a non-fatal warning (e.g. a benign end-of-run
    # "timestep too small") must surface the warning and a "with warnings"
    # status, yet still proceed to snapshot results (unlike the failed path).
    # We stub _snapshot_result to stop right after the warning branch so the
    # heavy plotting/measurement path is not needed.
    mw = _bare_window(run_epoch=5, project_epoch=5)
    statuses: list[str] = []
    mw._set_status = statuses.append  # type: ignore[method-assign]

    class _Ctrl:
        last_run_had_errors = False
        last_run_warning = "transient stopped at end of run (timestep too small)"

    mw._controller = _Ctrl()  # type: ignore[assignment]

    reached_snapshot = []

    def _stop_after_warning():
        reached_snapshot.append(True)
        raise RuntimeError("stop")  # sentinel: warning branch already ran

    mw._snapshot_result = _stop_after_warning  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="stop"):
        mw._on_sim_finished()

    # Warning surfaced, status reflects warnings, and it did NOT take the
    # failed early-return (it proceeded toward snapshotting results).
    assert any("warning" in line.lower() for line in mw._console.lines)
    assert not any("failed" in line.lower() for line in mw._console.lines)
    assert any("with warnings" in s.lower() for s in statuses)
    assert reached_snapshot == [True]


def test_invalidate_running_run_bumps_epoch_and_halts():
    mw = MainWindow.__new__(MainWindow)
    mw._project_epoch = 7
    mw._sim_halted = True
    halted: list[bool] = []

    class _Session:
        is_running = True

    class _Ctrl:
        session = _Session()

        def halt(self) -> None:
            halted.append(True)

    mw._controller = _Ctrl()  # type: ignore[assignment]
    mw._invalidate_running_run()
    assert mw._project_epoch == 8
    assert halted == [True]
    assert mw._sim_halted is False  # abandoned, not a user pause


def test_invalidate_running_run_idle_only_bumps_epoch():
    mw = MainWindow.__new__(MainWindow)
    mw._project_epoch = 0

    class _Session:
        is_running = False

    class _Ctrl:
        session = _Session()

        def halt(self) -> None:  # pragma: no cover - must not be called
            raise AssertionError("halt() called when no run is active")

    mw._controller = _Ctrl()  # type: ignore[assignment]
    mw._invalidate_running_run()
    assert mw._project_epoch == 1

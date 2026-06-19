"""Bridge between NgSpiceSession and the GUI.

Drains the engine's event_queue via a QTimer on the GUI thread and
re-emits events as Qt signals — no widget access from callback threads.
"""
from __future__ import annotations

import queue
import re

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ngspice_ui.engine.callbacks import (
    BGThreadEvent,
    CharEvent,
    DataPointEvent,
    ExitEvent,
    InitDataEvent,
    StatEvent,
)
from ngspice_ui.engine.session import NgSpiceSession

_ERR_MSG_RE = re.compile(r"\b(?:error|fatal)\b", re.IGNORECASE)
_ERR_LINE_RE = re.compile(r"\bline\s+(\d+)\b", re.IGNORECASE)

_ANALYSIS_KEYWORDS: frozenset[str] = frozenset(
    ("tran", "ac", "dc", "op", "noise", "tf", "sens", "pz", "disto")
)


class SimController(QObject):
    output_line = Signal(str)
    sim_started = Signal()
    sim_finished = Signal()
    progress = Signal(int)
    plot_init = Signal(object)    # InitDataEvent — emitted when a new sim begins
    plot_data = Signal(object)    # list[DataPointEvent] — batched per drain cycle
    errors_changed = Signal(list) # list[tuple[int, str]] — (1-based lineno, msg)
    mc_progress = Signal(int, int)  # (1-based run index, total runs)
    mc_finished = Signal(int)       # total runs completed

    def __init__(
        self,
        parent: QObject | None = None,
        session: NgSpiceSession | None = None,
    ) -> None:
        super().__init__(parent)
        # session is injectable for tests; production constructs the real one.
        self._session = session if session is not None else NgSpiceSession()
        self._pending_errors: list[tuple[int, str]] = []

        # Monte Carlo sequencing state (sequential bg_run driven by sim_finished)
        self._mc_queue: list[str] = []
        self._mc_total = 0
        self._mc_index = 0
        self._mc_analysis_line: str | None = None
        self._mc_connected = False

        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(50)
        self._drain_timer.timeout.connect(self._drain_queue)
        self._drain_timer.start()

    @property
    def session(self) -> NgSpiceSession:
        return self._session

    @Slot(str)
    def load_netlist(self, text: str) -> None:
        lines = text.splitlines()
        try:
            self._session.load_netlist(lines)
            self.output_line.emit("-- netlist loaded --")
        except RuntimeError as exc:
            self.output_line.emit(f"load error: {exc}")

    @Slot()
    def run(self) -> None:
        try:
            self._session.bg_run()
        except RuntimeError as exc:
            self.output_line.emit(f"run error: {exc}")

    def run_with_analysis(
        self,
        netlist: str,
        analysis_line: str | None,
        extra_lines: list[str] | None = None,
    ) -> None:
        """Load *netlist* text, optionally override its analysis command, then bg_run.

        extra_lines: prepended before the first non-title line (e.g. .temp, .step).
        """
        self._pending_errors.clear()
        lines = netlist.splitlines()
        if analysis_line is not None:
            filtered: list[str] = []
            for ln in lines:
                s = ln.strip().lower()
                if s == ".end":
                    continue
                # Match the exact dot-keyword (first token), not a prefix
                if s.startswith("."):
                    token = s[1:].split()[0] if s[1:].split() else ""
                    if token in _ANALYSIS_KEYWORDS:
                        continue
                filtered.append(ln)
            filtered.append(analysis_line)
            lines = filtered
        if extra_lines:
            # SPICE title is always line 0 regardless of content; insert after it
            insert_at = 1 if lines else 0
            lines = lines[:insert_at] + list(extra_lines) + lines[insert_at:]
        try:
            self._session.load_netlist(lines)
            self.output_line.emit("-- netlist loaded --")
            if analysis_line:
                self.output_line.emit(f"-- analysis: {analysis_line} --")
            if extra_lines:
                for el in extra_lines:
                    self.output_line.emit(f"-- extra: {el} --")
        except RuntimeError as exc:
            self.output_line.emit(f"load error: {exc}")
            return
        try:
            self._session.bg_run()
        except RuntimeError as exc:
            self.output_line.emit(f"run error: {exc}")

    @Slot()
    def halt(self) -> None:
        try:
            self._session.bg_halt()
        except RuntimeError as exc:
            self.output_line.emit(f"halt error: {exc}")

    @Slot()
    def resume(self) -> None:
        try:
            self._session.bg_resume()
        except RuntimeError as exc:
            self.output_line.emit(f"resume error: {exc}")

    # ------------------------------------------------------------------
    # Parametric sweep
    # ------------------------------------------------------------------

    def run_param_sweep(
        self,
        netlist: str,
        step_lines: list[str],
        analysis_line: str | None,
    ) -> None:
        """Run a single swept simulation with *step_lines* inserted after the SPICE title."""
        self.run_with_analysis(netlist, analysis_line, extra_lines=step_lines or None)

    # ------------------------------------------------------------------
    # Monte Carlo (sequential bg_run, advanced by sim_finished)
    # ------------------------------------------------------------------

    def run_monte_carlo(
        self,
        netlists: list[str],
        analysis_line: str | None,
    ) -> None:
        """Run *netlists* one after another, each as its own bg_run.

        Emits ``mc_progress(index, total)`` before each run and
        ``mc_finished(total)`` once the queue drains. A no-op for an empty list.
        """
        if not netlists:
            return
        self._mc_queue = list(netlists)
        self._mc_total = len(netlists)
        self._mc_index = 0
        self._mc_analysis_line = analysis_line
        if not self._mc_connected:
            self.sim_finished.connect(self._mc_on_finished)
            self._mc_connected = True
        self._mc_run_next()

    def _mc_run_next(self) -> None:
        if not self._mc_queue:
            return
        text = self._mc_queue.pop(0)
        self._mc_index += 1
        self.mc_progress.emit(self._mc_index, self._mc_total)
        self.run_with_analysis(text, self._mc_analysis_line)

    @Slot()
    def _mc_on_finished(self) -> None:
        # Only react while a Monte Carlo sequence is active.
        if not self._mc_connected:
            return
        if self._mc_queue:
            self._mc_run_next()
        else:
            self.sim_finished.disconnect(self._mc_on_finished)
            self._mc_connected = False
            self.mc_finished.emit(self._mc_total)

    _MAX_DATA_EVENTS_PER_DRAIN = 2_500

    @Slot()
    def _drain_queue(self) -> None:
        q = self._session.event_queue
        data_events: list = []
        data_cap = self._MAX_DATA_EVENTS_PER_DRAIN
        while True:
            try:
                event = q.get_nowait()
            except queue.Empty:
                break
            match event:
                case CharEvent(line=line):
                    self.output_line.emit(line)
                    if _ERR_MSG_RE.search(line):
                        m = _ERR_LINE_RE.search(line)
                        if m:
                            self._pending_errors.append((int(m.group(1)), line))
                            self.errors_changed.emit(list(self._pending_errors))
                case StatEvent(message=msg, percent=pct):
                    self.output_line.emit(f"[{pct:3d}%] {msg}")
                    self.progress.emit(pct)
                case ExitEvent(status=s):
                    self.output_line.emit(f"[ngspice exit: {s}]")
                case BGThreadEvent(running=True):
                    self.sim_started.emit()
                case BGThreadEvent(running=False):
                    self.sim_finished.emit()
                case InitDataEvent() as e:
                    self.output_line.emit(f"-- plot: {e.plot_name} ({e.plot_type}) --")
                    self.plot_init.emit(e)
                case DataPointEvent() as e:
                    data_events.append(e)
                    data_cap -= 1
                    if data_cap <= 0:
                        break   # yield to the GUI; next tick drains the rest
        if data_events:
            self.plot_data.emit(data_events)

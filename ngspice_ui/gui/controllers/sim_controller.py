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
    plot_init = Signal(object)  # InitDataEvent — emitted when a new sim begins
    plot_data = Signal(object)  # list[DataPointEvent] — batched per drain cycle
    errors_changed = Signal(list)  # list[tuple[int, str]] — (1-based lineno, msg)
    # Generic multi-run sequencing (Monte Carlo, parametric/temperature sweeps).
    # The trailing label distinguishes which sequence kind is running.
    sequence_progress = Signal(int, int, str)  # (1-based run index, total, kind)
    sequence_finished = Signal(int, str)  # (total runs completed, kind)

    def __init__(
        self,
        parent: QObject | None = None,
        session: NgSpiceSession | None = None,
    ) -> None:
        super().__init__(parent)
        # session is injectable for tests; production constructs the real one.
        self._session = session if session is not None else NgSpiceSession()
        self._pending_errors: list[tuple[int, str]] = []
        # True if the current/last run reported an ngspice error via callbacks.
        self._run_errored = False

        # Sequential bg_run state (driven by sim_finished) shared by Monte Carlo
        # and parametric/temperature sweeps — ngspice 46 has no working .step.
        self._seq_queue: list[str] = []
        self._seq_total = 0
        self._seq_index = 0
        self._seq_analysis_line: str | None = None
        self._seq_kind = ""
        self._seq_connected = False
        self._seq_active = False  # False while halted/cancelled so sim_finished won't advance

        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(50)
        self._drain_timer.timeout.connect(self._drain_queue)
        self._drain_timer.start()

    @property
    def session(self) -> NgSpiceSession:
        return self._session

    @property
    def last_run_had_errors(self) -> bool:
        """True if the most recent run emitted an ngspice error message.

        ngspice can report a fatal error through the send_char callback while
        the background run still 'finishes' normally, so completion alone does
        not imply the run produced valid data.
        """
        return self._run_errored

    def _begin_run(self) -> None:
        """Reset per-run error tracking before launching a (bg) simulation."""
        self._pending_errors.clear()
        self._run_errored = False

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
        self._begin_run()
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

        extra_lines: prepended before the first non-title line (e.g. .temp).
        """
        self._begin_run()
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
        if self._seq_active:
            self._seq_active = False  # cancel pending runs before the finished signal fires
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
    # Sequential multi-run sequencing (bg_run advanced by sim_finished)
    #
    # ngspice 46 rejects '.step' as unimplemented, so parametric and
    # temperature sweeps run as a sequence of independent bg_run passes —
    # the same mechanism Monte Carlo uses. Each pass produces its own plot,
    # which the read-only consumers snapshot after every sim_finished.
    # ------------------------------------------------------------------

    def run_sequence(
        self,
        netlists: list[str],
        analysis_line: str | None,
        kind: str = "Run",
    ) -> None:
        """Run *netlists* one after another, each as its own bg_run.

        Emits ``sequence_progress(index, total, kind)`` before each run and
        ``sequence_finished(total, kind)`` once the queue drains. A no-op for
        an empty list.
        """
        if not netlists:
            return
        self._seq_queue = list(netlists)
        self._seq_total = len(netlists)
        self._seq_index = 0
        self._seq_analysis_line = analysis_line
        self._seq_kind = kind
        self._seq_active = True
        if not self._seq_connected:
            self.sim_finished.connect(self._seq_on_finished)
            self._seq_connected = True
        self._seq_run_next()

    def run_monte_carlo(self, netlists: list[str], analysis_line: str | None) -> None:
        """Run Monte Carlo netlists sequentially. See :meth:`run_sequence`."""
        self.run_sequence(netlists, analysis_line, kind="Monte Carlo")

    def run_param_sweep(self, netlists: list[str], analysis_line: str | None) -> None:
        """Run one netlist per swept value sequentially. See :meth:`run_sequence`."""
        self.run_sequence(netlists, analysis_line, kind="Sweep")

    def _seq_run_next(self) -> None:
        if not self._seq_queue:
            return
        text = self._seq_queue.pop(0)
        self._seq_index += 1
        self.sequence_progress.emit(self._seq_index, self._seq_total, self._seq_kind)
        self.run_with_analysis(text, self._seq_analysis_line)

    @Slot()
    def _seq_on_finished(self) -> None:
        if not self._seq_connected:
            return
        if not self._seq_active:
            # Halted mid-sequence — clean up without starting the next run.
            self.sim_finished.disconnect(self._seq_on_finished)
            self._seq_connected = False
            self._seq_queue.clear()
            return
        if self._seq_queue:
            self._seq_run_next()
        else:
            self.sim_finished.disconnect(self._seq_on_finished)
            self._seq_connected = False
            self._seq_active = False
            self.sequence_finished.emit(self._seq_total, self._seq_kind)

    _MAX_DATA_EVENTS_PER_DRAIN = 250

    @Slot()
    def _drain_queue(self) -> None:
        q = self._session.event_queue
        data_events: list = []
        # Control events (completion, errors, init) must never wait behind a
        # large live-data backlog or sequenced runs (Monte Carlo / sweeps)
        # stall between passes. Cap only how many data points we *forward* to
        # the plot per tick — excess live points are dropped (the post-run
        # snapshot has the full data) while control events keep draining.
        data_full = False
        while True:
            try:
                event = q.get_nowait()
            except queue.Empty:
                break
            if isinstance(event, DataPointEvent):
                if data_full:
                    continue  # drop excess live points; snapshot retains full data
                data_events.append(event)
                if len(data_events) >= self._MAX_DATA_EVENTS_PER_DRAIN:
                    data_full = True
                continue
            match event:
                case CharEvent(line=line):
                    self.output_line.emit(line)
                    if _ERR_MSG_RE.search(line):
                        # Any error line marks the run as failed, even when no
                        # line number is present (e.g. "Error: incomplete netlist").
                        self._run_errored = True
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
        if data_events:
            self.plot_data.emit(data_events)

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

# What counts as an ngspice error line. Matching the bare word "error" anywhere
# (the old behaviour) wrongly failed valid runs whose *title* contained it: the
# title is echoed as ``Circuit: <title>`` (optionally behind a ``stdout``/
# ``stderr`` stream tag from libngspice), so "Error amplifier test" looked like
# an error. Real ngspice diagnostics instead lead with ``error``/``fatal`` (e.g.
# ``Error on line 2``, ``Error: circuit not parsed``) or carry a distinctive
# abort phrase. Anchoring to the start of the message — after an optional stream
# tag — excludes the ``Circuit:``-prefixed title echo while still catching every
# real error, including the co-sim failures the engine reports as ``Error: ...``.
_ERR_MSG_RE = re.compile(
    r"^\s*(?:std(?:out|err)\s+)?(?:error|fatal)\b"
    r"|\b(?:fatal error|simulation (?:interrupted|aborted))\b",
    re.IGNORECASE,
)
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
        self._seq_base_dir: str | None = None
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
    def load_netlist(self, text: str, base_dir: str | None = None) -> None:
        lines = text.splitlines()
        try:
            self._session.load_netlist(lines, base_dir=base_dir)
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
        base_dir: str | None = None,
    ) -> bool:
        """Load *netlist* text, optionally override its analysis command, then bg_run.

        extra_lines: prepended before the first non-title line (e.g. .temp).
        base_dir: directory for resolving relative ``.include`` / ``.lib`` paths.

        Returns True if a background run was actually started. A False return
        means the load or bg_run failed synchronously and no ``sim_finished``
        will ever fire — callers driving a sequence must advance themselves
        instead of waiting for a completion signal that never comes.
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
            self._session.load_netlist(lines, base_dir=base_dir)
            self.output_line.emit("-- netlist loaded --")
            if analysis_line:
                self.output_line.emit(f"-- analysis: {analysis_line} --")
            if extra_lines:
                for el in extra_lines:
                    self.output_line.emit(f"-- extra: {el} --")
        except RuntimeError as exc:
            self.output_line.emit(f"load error: {exc}")
            return False
        try:
            self._session.bg_run()
        except RuntimeError as exc:
            self.output_line.emit(f"run error: {exc}")
            return False
        return True

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
        base_dir: str | None = None,
    ) -> None:
        """Run *netlists* one after another, each as its own bg_run.

        Emits ``sequence_progress(index, total, kind)`` before each run and
        ``sequence_finished(total, kind)`` once the queue drains. A no-op for
        an empty list. ``base_dir`` resolves relative includes for every run.
        """
        if not netlists:
            return
        self._seq_queue = list(netlists)
        self._seq_total = len(netlists)
        self._seq_index = 0
        self._seq_analysis_line = analysis_line
        self._seq_kind = kind
        self._seq_base_dir = base_dir
        self._seq_active = True
        if not self._seq_connected:
            self.sim_finished.connect(self._seq_on_finished)
            self._seq_connected = True
        self._seq_run_next()

    def run_monte_carlo(
        self, netlists: list[str], analysis_line: str | None, base_dir: str | None = None
    ) -> None:
        """Run Monte Carlo netlists sequentially. See :meth:`run_sequence`."""
        self.run_sequence(netlists, analysis_line, kind="Monte Carlo", base_dir=base_dir)

    def run_param_sweep(
        self, netlists: list[str], analysis_line: str | None, base_dir: str | None = None
    ) -> None:
        """Run one netlist per swept value sequentially. See :meth:`run_sequence`."""
        self.run_sequence(netlists, analysis_line, kind="Sweep", base_dir=base_dir)

    def _seq_run_next(self) -> None:
        # A run that fails to *start* (load/bg_run error) emits no sim_finished,
        # so we cannot wait for one — advance to the next queued netlist inline.
        # If every remaining run fails to start, the loop drains the queue and
        # finalises the sequence here rather than hanging forever.
        while self._seq_queue:
            text = self._seq_queue.pop(0)
            self._seq_index += 1
            self.sequence_progress.emit(self._seq_index, self._seq_total, self._seq_kind)
            if self.run_with_analysis(text, self._seq_analysis_line, base_dir=self._seq_base_dir):
                return  # started; sim_finished will drive the next step
            # else: synchronous failure — keep trying the rest of the queue
        self._seq_finalize()

    def _seq_finalize(self) -> None:
        """Tear down sequence state and announce completion (idempotent)."""
        if self._seq_connected:
            self.sim_finished.disconnect(self._seq_on_finished)
            self._seq_connected = False
        self._seq_active = False
        self.sequence_finished.emit(self._seq_total, self._seq_kind)

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
            self._seq_finalize()

    _MAX_DATA_EVENTS_PER_DRAIN = 250
    # Hard ceiling on how many events we pull from the queue in a single tick.
    # Without it, one drain can iterate the entire (unbounded) queue on the GUI
    # thread — a very large simulation backs up millions of points and freezes
    # the interface even though most are dropped. Anything left over is handled
    # on the next 50 ms tick, keeping each tick bounded. Sized well above the
    # data-forward cap so control events are never starved in practice.
    _MAX_EVENTS_PER_DRAIN = 20_000

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
        processed = 0
        while processed < self._MAX_EVENTS_PER_DRAIN:
            try:
                event = q.get_nowait()
            except queue.Empty:
                break
            processed += 1
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

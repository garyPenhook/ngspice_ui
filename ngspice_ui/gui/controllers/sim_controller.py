"""Bridge between NgSpiceSession and the GUI.

Drains the engine's event_queue via a QTimer on the GUI thread and
re-emits events as Qt signals — no widget access from callback threads.
"""
from __future__ import annotations

import queue

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

_ANALYSIS_PREFIXES: tuple[str, ...] = tuple(
    "." + k for k in ("tran", "ac", "dc", "op", "noise", "tf", "sens", "pz", "disto")
)


class SimController(QObject):
    output_line = Signal(str)
    sim_started = Signal()
    sim_finished = Signal()
    progress = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._session = NgSpiceSession()
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

    def run_with_analysis(self, netlist: str, analysis_line: str | None) -> None:
        """Load *netlist* text, optionally override its analysis command, then bg_run.

        When *analysis_line* is not None (e.g. '.tran 1us 1ms'), any existing
        analysis dot-commands are stripped from the netlist and *analysis_line*
        is appended in their place before loading.
        """
        lines = netlist.splitlines()
        if analysis_line is not None:
            filtered: list[str] = []
            for ln in lines:
                s = ln.strip().lower()
                if s == ".end":
                    continue
                if any(s.startswith(pfx) for pfx in _ANALYSIS_PREFIXES):
                    continue
                filtered.append(ln)
            filtered.append(analysis_line)
            lines = filtered
        try:
            self._session.load_netlist(lines)
            self.output_line.emit("-- netlist loaded --")
            if analysis_line:
                self.output_line.emit(f"-- analysis: {analysis_line} --")
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

    @Slot()
    def _drain_queue(self) -> None:
        q = self._session.event_queue
        while True:
            try:
                event = q.get_nowait()
            except queue.Empty:
                break
            match event:
                case CharEvent(line=line):
                    self.output_line.emit(line)
                case StatEvent(message=msg, percent=pct):
                    self.output_line.emit(f"[{pct:3d}%] {msg}")
                    self.progress.emit(pct)
                case ExitEvent(status=s):
                    self.output_line.emit(f"[ngspice exit: {s}]")
                case BGThreadEvent(running=True):
                    self.sim_started.emit()
                case BGThreadEvent(running=False):
                    self.sim_finished.emit()
                case InitDataEvent(plot_name=name, plot_type=typ):
                    self.output_line.emit(f"-- plot: {name} ({typ}) --")
                case DataPointEvent():
                    pass  # real-time streaming: phase 4

"""
Python scripting REPL dock widget.

The persistent namespace exposes:
  session  — NgSpiceSession (load_netlist, command, get_vector, …)
  ctrl     — SimController  (run_with_analysis, halt, resume, …)
  np       — numpy
  math     — math

Ctrl+Return executes the current input block.
Ctrl+Up / Ctrl+Down cycle through command history.
print() and repr() of returned expressions appear in the output pane.
"""

from __future__ import annotations

import contextlib
import io
import math
import traceback

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


class ScriptWidget(QWidget):
    """Interactive Python console wired to the simulation session."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session = None
        self._ctrl = None
        self._ns: dict = {}
        self._history: list[str] = []
        self._hist_idx: int = 0
        self._build_ui()

    def set_context(self, session, ctrl) -> None:
        """Call after both session and controller are ready."""
        self._session = session
        self._ctrl = ctrl
        self._ns = {
            "__builtins__": __builtins__,
            "math": math,
            "np": np,
            "session": session,
            "ctrl": ctrl,
        }
        self._out.setPlainText(
            "# Python scripting console\n"
            "# session, ctrl, np, math are pre-imported\n"
            "# Ctrl+Return to run  •  Ctrl+↑/↓ for history\n"
        )

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        mono = QFont("Monospace", 9)
        mono.setStyleHint(QFont.StyleHint.Monospace)

        self._out = QPlainTextEdit()
        self._out.setReadOnly(True)
        self._out.setFont(mono)
        self._out.setMaximumBlockCount(2000)
        self._out.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        self._inp = QPlainTextEdit()
        self._inp.setFont(mono)
        self._inp.setPlaceholderText("session.current_plot()")
        self._inp.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._inp.setMaximumHeight(120)
        self._inp.installEventFilter(self)
        self._inp.textChanged.connect(self.changed)

        btn_run = QPushButton("Run  Ctrl+↵")
        btn_run.setFixedWidth(110)
        btn_run.clicked.connect(self._run)

        btn_clear = QPushButton("Clear")
        btn_clear.setFixedWidth(60)
        btn_clear.clicked.connect(self._out.clear)

        btn_reset = QPushButton("Reset ns")
        btn_reset.setFixedWidth(70)
        btn_reset.setToolTip("Rebuild the execution namespace (keeps session/ctrl)")
        btn_reset.clicked.connect(self._reset_ns)

        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_run)
        btn_row.addWidget(btn_clear)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch()

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(2)
        bl.addLayout(btn_row)
        bl.addWidget(self._inp)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._out)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(splitter)

    # ------------------------------------------------------------------
    # Key handling
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent

        if obj is self._inp and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            mod = event.modifiers()
            ctrl = Qt.KeyboardModifier.ControlModifier

            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and mod & ctrl:
                self._run()
                return True
            if key == Qt.Key.Key_Up and mod & ctrl:
                self._hist_step(-1)
                return True
            if key == Qt.Key.Key_Down and mod & ctrl:
                self._hist_step(+1)
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _run(self) -> None:
        code = self._inp.toPlainText().strip()
        if not code:
            return
        if not self._ns:
            self._emit("# (no session yet)")
            return

        self._history.append(code)
        self._hist_idx = len(self._history)
        self._inp.clear()

        display = code if "\n" not in code else code.splitlines()[0] + " …"
        self._emit(f">>> {display}")

        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                # Try expression eval to show repr
                try:
                    result = eval(code, self._ns)  # noqa: S307
                    captured = buf.getvalue()
                    if captured:
                        self._emit(captured.rstrip())
                    if result is not None:
                        self._emit(repr(result))
                except SyntaxError:
                    exec(code, self._ns)  # noqa: S102
                    captured = buf.getvalue()
                    if captured:
                        self._emit(captured.rstrip())
        except Exception:
            self._emit(traceback.format_exc().rstrip())

    def _emit(self, text: str) -> None:
        for line in text.splitlines():
            self._out.appendPlainText(line)
        self._out.moveCursor(QTextCursor.MoveOperation.End)

    def _hist_step(self, direction: int) -> None:
        if not self._history:
            return
        self._hist_idx = max(0, min(len(self._history) - 1,
                                    self._hist_idx + direction))
        self._inp.setPlainText(self._history[self._hist_idx])
        cursor = self._inp.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._inp.setTextCursor(cursor)

    def _reset_ns(self) -> None:
        self.set_context(self._session, self._ctrl)
        self._emit("# namespace reset")

    # ------------------------------------------------------------------
    # Project serialisation
    # ------------------------------------------------------------------

    def get_config(self) -> dict:
        return {"input": self._inp.toPlainText()}

    def set_config(self, cfg: dict) -> None:
        self._inp.setPlainText(cfg.get("input", ""))

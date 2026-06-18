from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QSizePolicy


class ConsoleWidget(QPlainTextEdit):
    MAX_LINES = 5000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(self.MAX_LINES)
        font = QFont("Monospace", 9)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    @Slot(str)
    def append_line(self, line: str) -> None:
        self.appendPlainText(line)
        self.moveCursor(QTextCursor.MoveOperation.End)

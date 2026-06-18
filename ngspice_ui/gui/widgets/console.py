from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class ConsoleWidget(QWidget):
    MAX_LINES = 5000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all_lines: list[str] = []
        self._build_ui()

    def _build_ui(self) -> None:
        font = QFont("Monospace", 9)
        font.setStyleHint(QFont.StyleHint.Monospace)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(self.MAX_LINES)
        self._text.setFont(font)
        self._text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Filter bar
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)

        btn_clear = QPushButton("Clear")
        btn_clear.setFixedWidth(50)
        btn_clear.clicked.connect(self._clear)

        btn_save = QPushButton("Save…")
        btn_save.setFixedWidth(50)
        btn_save.clicked.connect(self._save)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 2, 0, 2)
        bar.setSpacing(4)
        bar.addWidget(self._filter)
        bar.addWidget(btn_clear)
        bar.addWidget(btn_save)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(bar)
        layout.addWidget(self._text)

    @Slot(str)
    def append_line(self, line: str) -> None:
        self._all_lines.append(line)
        if len(self._all_lines) > self.MAX_LINES:
            self._all_lines = self._all_lines[-self.MAX_LINES:]
        f = self._filter.text()
        if not f or f.lower() in line.lower():
            self._text.appendPlainText(line)
            self._text.moveCursor(QTextCursor.MoveOperation.End)

    def _clear(self) -> None:
        self._all_lines.clear()
        self._text.clear()

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Console Log", str(Path.home() / "console.txt"),
            "Text files (*.txt);;All Files (*)",
        )
        if path:
            Path(path).write_text("\n".join(self._all_lines), encoding="utf-8")

    def _apply_filter(self, text: str) -> None:
        self._text.setUpdatesEnabled(False)
        self._text.clear()
        low = text.lower()
        for line in self._all_lines:
            if not low or low in line.lower():
                self._text.appendPlainText(line)
        self._text.moveCursor(QTextCursor.MoveOperation.End)
        self._text.setUpdatesEnabled(True)

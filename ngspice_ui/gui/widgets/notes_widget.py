from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QPlainTextEdit, QSizePolicy, QVBoxLayout, QWidget


class NotesWidget(QWidget):
    """Free-form project notes saved in the .ngsui project file."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        font = QFont("Monospace", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._edit = QPlainTextEdit()
        self._edit.setFont(font)
        self._edit.setPlaceholderText("Project notes…")
        self._edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self._edit)

    def get_config(self) -> str:
        return self._edit.toPlainText()

    def set_config(self, text: str) -> None:
        self._edit.setPlainText(text)

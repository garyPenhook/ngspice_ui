from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, QStringListModel, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCompleter,
    QInputDialog,
    QPlainTextEdit,
    QSizePolicy,
    QTextEdit,
)

from .netlist_highlighter import SpiceHighlighter

_DOT_COMPLETIONS: list[str] = sorted([
    ".ac", ".dc", ".disto", ".else", ".elseif", ".end", ".endif", ".ends",
    ".global", ".if", ".include", ".lib", ".measure", ".model", ".noise",
    ".op", ".options", ".param", ".plot", ".print", ".probe", ".pz",
    ".save", ".sens", ".subckt", ".temp", ".tf", ".tran",
])

# Matches a SPICE value token like 1k, 100n, 2.2MEG, 1e-3, 47 — full string
_VALUE_RE = re.compile(
    r"^\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?:MEG|[kKmMuUnNpPfFgGtT])?$",
    re.IGNORECASE,
)

_ERR_FMT: QTextCharFormat | None = None  # built lazily after QApplication exists


def _error_fmt() -> QTextCharFormat:
    global _ERR_FMT
    if _ERR_FMT is None:
        _ERR_FMT = QTextCharFormat()
        _ERR_FMT.setUnderlineColor(QColor("#ff4444"))
        _ERR_FMT.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
        _ERR_FMT.setBackground(QColor(0x3D, 0x00, 0x00))
    return _ERR_FMT


class NetlistEditor(QPlainTextEdit):
    modification_changed = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_path: Path | None = None

        font = QFont("Monospace", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setPlaceholderText(
            "* Enter netlist here\n"
            ".title My Circuit\n"
            "...\n"
            ".end"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._highlighter = SpiceHighlighter(self.document())

        self._completer = QCompleter(_DOT_COMPLETIONS, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setWidget(self)
        self._completer.activated.connect(self._insert_completion)

        self.textChanged.connect(self._refresh_completions)
        self.document().modificationChanged.connect(self.modification_changed)

    # ------------------------------------------------------------------
    # Path / dirty tracking
    # ------------------------------------------------------------------

    @property
    def current_path(self) -> Path | None:
        return self._current_path

    @property
    def is_modified(self) -> bool:
        return self.document().isModified()

    def set_content(self, text: str, path: Path | None = None) -> None:
        """Replace editor content and clear the modified flag."""
        self._current_path = path
        self.setPlainText(text)
        self.document().setModified(False)

    def mark_saved(self, path: Path | None = None) -> None:
        """Clear the modified flag; optionally update the current path."""
        if path is not None:
            self._current_path = path
        self.document().setModified(False)

    # ------------------------------------------------------------------
    # Auto-complete
    # ------------------------------------------------------------------

    def _refresh_completions(self) -> None:
        """Rebuild the completer word list with dynamic .model/.subckt names."""
        extra: set[str] = set()
        for line in self.toPlainText().splitlines():
            s = line.strip()
            if s.lower().startswith((".model ", ".subckt ")):
                parts = s.split()
                if len(parts) >= 2:
                    extra.add(parts[1])
        words = _DOT_COMPLETIONS + sorted(extra)
        self._completer.setModel(QStringListModel(words, self._completer))

    def _word_prefix(self) -> tuple[str, int]:
        """Return (prefix, start_col) for the current non-whitespace token up to the cursor."""
        cursor = self.textCursor()
        col = cursor.positionInBlock()
        line = cursor.block().text()
        i = col
        while i > 0 and line[i - 1] not in " \t":
            i -= 1
        return line[i:col], i

    def keyPressEvent(self, event) -> None:
        popup = self._completer.popup()
        if popup.isVisible() and event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Tab,
            Qt.Key.Key_Escape,
        ):
            event.ignore()
            return
        super().keyPressEvent(event)
        self._trigger_completer()

    def _trigger_completer(self) -> None:
        prefix, _ = self._word_prefix()
        if not prefix.startswith(".") or len(prefix) < 2:
            self._completer.popup().hide()
            return
        self._completer.setCompletionPrefix(prefix)
        if self._completer.completionCount() == 0:
            self._completer.popup().hide()
            return
        scroll = self._completer.popup().verticalScrollBar()
        cr = self.cursorRect()
        cr.setWidth(
            self._completer.popup().sizeHintForColumn(0) + scroll.sizeHint().width()
        )
        self._completer.complete(cr)

    def _insert_completion(self, completion: str) -> None:
        prefix = self._completer.completionPrefix()
        cursor = self.textCursor()
        cursor.movePosition(
            QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, len(prefix)
        )
        cursor.insertText(completion)
        self.setTextCursor(cursor)

    # ------------------------------------------------------------------
    # Value quick-edit (Ctrl + double-click on a SPICE value token)
    # ------------------------------------------------------------------

    def mouseDoubleClickEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            cursor = self.cursorForPosition(event.position().toPoint())
            text = cursor.block().text()
            col = cursor.positionInBlock()
            # Expand to the full whitespace-delimited token under cursor
            start = col
            while start > 0 and text[start - 1] not in " \t":
                start -= 1
            end = col
            while end < len(text) and text[end] not in " \t":
                end += 1
            word = text[start:end]
            if _VALUE_RE.match(word):
                new_val, ok = QInputDialog.getText(
                    self, "Edit value", "New value:", text=word
                )
                if ok and new_val.strip():
                    cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                    cursor.movePosition(
                        QTextCursor.MoveOperation.Right,
                        QTextCursor.MoveMode.MoveAnchor,
                        start,
                    )
                    cursor.movePosition(
                        QTextCursor.MoveOperation.Right,
                        QTextCursor.MoveMode.KeepAnchor,
                        end - start,
                    )
                    cursor.insertText(new_val.strip())
                return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # Error markers
    # ------------------------------------------------------------------

    def mark_errors(self, errors: list[tuple[int, str]]) -> None:
        """Highlight lines that ngspice reported errors on.

        errors: [(1-based line number, message), ...]
        """
        doc = self.document()
        sels: list[QTextEdit.ExtraSelection] = []
        fmt = _error_fmt()
        for lineno, _ in errors:
            block = doc.findBlockByLineNumber(lineno - 1)
            if not block.isValid():
                continue
            sel = QTextEdit.ExtraSelection()
            sel.cursor = QTextCursor(block)
            sel.cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            sel.format = fmt
            sels.append(sel)
        self.setExtraSelections(sels)

    def clear_errors(self) -> None:
        self.setExtraSelections([])

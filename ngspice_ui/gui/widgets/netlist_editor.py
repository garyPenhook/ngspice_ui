from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QRect, QSize, QStringListModel, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeySequence,
    QPainter,
    QPalette,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .netlist_highlighter import SpiceHighlighter

_DOT_COMPLETIONS: list[str] = sorted([
    ".ac", ".dc", ".disto", ".else", ".elseif", ".end", ".endif", ".ends",
    ".global", ".if", ".include", ".lib", ".measure", ".model", ".noise",
    ".op", ".options", ".param", ".plot", ".print", ".probe", ".pz",
    ".save", ".sens", ".subckt", ".temp", ".tf", ".tran",
])

_VALUE_RE = re.compile(
    r"^\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?:MEG|[kKmMuUnNpPfFgGtT])?$",
    re.IGNORECASE,
)
_INCLUDE_RE = re.compile(r'^\s*\.(?:include|lib)\s+"?([^"\s]+)"?', re.IGNORECASE)

_ERR_FMT: QTextCharFormat | None = None


def _error_fmt() -> QTextCharFormat:
    global _ERR_FMT
    if _ERR_FMT is None:
        _ERR_FMT = QTextCharFormat()
        _ERR_FMT.setUnderlineColor(QColor("#ff4444"))
        _ERR_FMT.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
        _ERR_FMT.setBackground(QColor(0x3D, 0x00, 0x00))
    return _ERR_FMT


# ---------------------------------------------------------------------------
# Line-number gutter
# ---------------------------------------------------------------------------

class _LineNumberArea(QWidget):
    def __init__(self, editor: "_EditorCore") -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor._line_number_width(), 0)

    def paintEvent(self, event) -> None:
        self._editor._paint_line_numbers(event)


# ---------------------------------------------------------------------------
# Core QPlainTextEdit with line numbers + code folding + .include resolver
# ---------------------------------------------------------------------------

class _EditorCore(QPlainTextEdit):
    include_opened = Signal(str)   # emitted when user Ctrl+clicks an .include path

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._fold_state: dict[int, bool] = {}   # block_number → folded

        self._gutter = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._gutter.update)
        self._update_gutter_width()

        font = QFont("Monospace", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # -- gutter --

    def _line_number_width(self) -> int:
        digits = max(1, len(str(self.blockCount())))
        return 6 + self.fontMetrics().horizontalAdvance("9") * (digits + 1)

    def _update_gutter_width(self) -> None:
        self.setViewportMargins(self._line_number_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), self._line_number_width(), cr.height()))

    def _paint_line_numbers(self, event) -> None:
        p = QPainter(self._gutter)
        bg = self.palette().color(QPalette.ColorRole.Window).darker(110)
        p.fillRect(event.rect(), bg)

        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        fg = self.palette().color(QPalette.ColorRole.Mid)
        cur_fg = self.palette().color(QPalette.ColorRole.Highlight)
        cur_block = self.textCursor().blockNumber()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                p.setPen(cur_fg if num == cur_block else fg)
                p.setFont(self.font())
                p.drawText(
                    0, top, self._gutter.width() - 4,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    str(num + 1),
                )
                # Fold indicator for .subckt start
                text = block.text().strip().lower()
                if text.startswith(".subckt ") or text.startswith(".ends"):
                    folded = self._fold_state.get(num, False)
                    indicator = "▶" if folded else "▼"
                    p.setPen(QColor("#888888"))
                    p.drawText(
                        0, top, self._line_number_width() - 2,
                        self.fontMetrics().height(),
                        Qt.AlignmentFlag.AlignLeft,
                        indicator,
                    )

            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            num += 1

    # -- code folding --

    def _toggle_fold_at(self, line_num: int) -> None:
        doc = self.document()
        block = doc.findBlockByNumber(line_num)
        if not block.isValid():
            return
        text = block.text().strip().lower()
        if not text.startswith(".subckt "):
            return
        folded = self._fold_state.get(line_num, False)
        folded = not folded
        self._fold_state[line_num] = folded

        # Hide/show blocks until matching .ends
        depth = 0
        b = block.next()
        while b.isValid():
            bt = b.text().strip().lower()
            if bt.startswith(".subckt "):
                depth += 1
            b.setVisible(not folded)
            if bt.startswith(".ends"):
                if depth == 0:
                    b.setVisible(True)
                    break
                depth -= 1
            b = b.next()
        self.document().adjustSize()
        self.viewport().update()
        self._gutter.update()

    # -- mouse: fold toggle + .include resolver --

    def mousePressEvent(self, event) -> None:
        if event.x() < self._line_number_width():
            # Click in gutter — check fold toggle
            cursor = self.cursorForPosition(event.position().toPoint())
            line_num = cursor.blockNumber()
            text = self.document().findBlockByNumber(line_num).text().strip().lower()
            if text.startswith(".subckt ") or text.startswith(".ends"):
                self._toggle_fold_at(line_num)
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            cursor = self.cursorForPosition(event.position().toPoint())
            line = cursor.block().text()

            # .include resolver
            m = _INCLUDE_RE.match(line)
            if m:
                self.include_opened.emit(m.group(1))
                return

            # Value quick-edit
            col = cursor.positionInBlock()
            start = col
            while start > 0 and line[start - 1] not in " \t":
                start -= 1
            end = col
            while end < len(line) and line[end] not in " \t":
                end += 1
            word = line[start:end]
            if _VALUE_RE.match(word):
                new_val, ok = QInputDialog.getText(
                    self, "Edit value", "New value:", text=word
                )
                if ok and new_val.strip():
                    cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                    cursor.movePosition(QTextCursor.MoveOperation.Right,
                                        QTextCursor.MoveMode.MoveAnchor, start)
                    cursor.movePosition(QTextCursor.MoveOperation.Right,
                                        QTextCursor.MoveMode.KeepAnchor, end - start)
                    cursor.insertText(new_val.strip())
                return
        super().mouseDoubleClickEvent(event)


# ---------------------------------------------------------------------------
# Find/Replace bar
# ---------------------------------------------------------------------------

class _FindBar(QWidget):
    def __init__(self, editor: "_EditorCore", parent=None) -> None:
        super().__init__(parent)
        self._editor = editor
        self._build()
        self.hide()

    def _build(self) -> None:
        self._find_edit = QLineEdit()
        self._find_edit.setPlaceholderText("Find…")
        self._find_edit.returnPressed.connect(self._find_next)

        self._replace_edit = QLineEdit()
        self._replace_edit.setPlaceholderText("Replace…")

        self._case_cb = QCheckBox("Aa")
        self._case_cb.setToolTip("Case-sensitive")
        self._regex_cb = QCheckBox(".*")
        self._regex_cb.setToolTip("Regular expression")

        btn_prev = QPushButton("◀")
        btn_prev.setFixedWidth(28)
        btn_prev.setToolTip("Previous match")
        btn_prev.clicked.connect(self._find_prev)

        btn_next = QPushButton("▶")
        btn_next.setFixedWidth(28)
        btn_next.setToolTip("Next match")
        btn_next.clicked.connect(self._find_next)

        btn_replace = QPushButton("Replace")
        btn_replace.clicked.connect(self._replace_one)

        btn_all = QPushButton("All")
        btn_all.setToolTip("Replace all")
        btn_all.clicked.connect(self._replace_all)

        btn_close = QPushButton("✕")
        btn_close.setFixedWidth(24)
        btn_close.clicked.connect(self.hide)

        self._status = QLabel()
        self._status.setStyleSheet("color: #888; font-size: 9pt;")

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(4)
        for w in (
            QLabel("Find:"), self._find_edit, self._case_cb, self._regex_cb,
            btn_prev, btn_next,
            QLabel("Replace:"), self._replace_edit,
            btn_replace, btn_all,
            self._status, btn_close,
        ):
            row.addWidget(w)

    def show_and_focus(self) -> None:
        self.show()
        self._find_edit.setFocus()
        self._find_edit.selectAll()

    def _flags(self):
        from PySide6.QtGui import QTextDocument
        flags = QTextDocument.FindFlag(0)
        if self._case_cb.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        return flags

    def _pattern(self) -> str | re.Pattern:
        text = self._find_edit.text()
        if not text:
            return ""
        if self._regex_cb.isChecked():
            flags = 0 if self._case_cb.isChecked() else re.IGNORECASE
            try:
                return re.compile(text, flags)
            except re.error:
                return text
        return text

    def _find(self, backward: bool = False) -> bool:
        pat = self._pattern()
        if not pat:
            self._status.setText("")
            return False
        doc = self._editor.document()
        cursor = self._editor.textCursor()
        from PySide6.QtGui import QTextDocument
        flags = self._flags()
        if backward:
            flags |= QTextDocument.FindFlag.FindBackward
        if isinstance(pat, re.Pattern):
            case_flag = (
                QTextDocument.FindFlag.FindCaseSensitively
                if self._case_cb.isChecked()
                else QTextDocument.FindFlag(0)
            )
            new_cursor = doc.find(pat.pattern, cursor, case_flag)
        else:
            new_cursor = doc.find(pat, cursor, flags)
        if new_cursor.isNull():
            # wrap
            wrap = QTextCursor(doc)
            if backward:
                wrap.movePosition(QTextCursor.MoveOperation.End)
            if isinstance(pat, re.Pattern):
                new_cursor = doc.find(pat.pattern, wrap)
            else:
                new_cursor = doc.find(pat, wrap, flags)
        if not new_cursor.isNull():
            self._editor.setTextCursor(new_cursor)
            self._status.setText("")
            return True
        self._status.setText("Not found")
        return False

    def _find_next(self) -> None:
        self._find(backward=False)

    def _find_prev(self) -> None:
        self._find(backward=True)

    def _replace_one(self) -> None:
        cursor = self._editor.textCursor()
        if cursor.hasSelection():
            cursor.insertText(self._replace_edit.text())
        self._find(backward=False)

    def _replace_all(self) -> None:
        pat = self._pattern()
        if not pat:
            return
        doc = self._editor.document()
        repl = self._replace_edit.text()
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        count = 0
        flags = self._flags()
        while True:
            if isinstance(pat, re.Pattern):
                c = doc.find(pat.pattern, cursor)
            else:
                c = doc.find(pat, cursor, flags)
            if c.isNull():
                break
            c.insertText(repl)
            cursor = c
            count += 1
        cursor.endEditBlock()
        self._status.setText(f"{count} replaced" if count else "Not found")

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self._editor.setFocus()
        else:
            super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Public NetlistEditor wrapper
# ---------------------------------------------------------------------------

class NetlistEditor(QWidget):
    modification_changed = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_path: Path | None = None

        self._core = _EditorCore()
        self._highlighter = SpiceHighlighter(self._core.document())

        self._completer = QCompleter(_DOT_COMPLETIONS, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setWidget(self._core)
        self._completer.activated.connect(self._insert_completion)

        self._core.textChanged.connect(self._refresh_completions)
        self._core.document().modificationChanged.connect(self.modification_changed)
        self._core.include_opened.connect(self._open_include)
        self._core.installEventFilter(self)

        self._find_bar = _FindBar(self._core)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._core)
        layout.addWidget(self._find_bar)

        self._core.setPlaceholderText(
            "* Enter netlist here\n"
            ".title My Circuit\n"
            "...\n"
            ".end"
        )

    # ------------------------------------------------------------------
    # Delegate common QPlainTextEdit API
    # ------------------------------------------------------------------

    def toPlainText(self) -> str:
        return self._core.toPlainText()

    def setPlainText(self, text: str) -> None:
        self._core.setPlainText(text)

    def document(self):
        return self._core.document()

    def textCursor(self) -> QTextCursor:
        return self._core.textCursor()

    def setExtraSelections(self, sels) -> None:
        self._core.setExtraSelections(sels)

    # ------------------------------------------------------------------
    # Path / dirty tracking
    # ------------------------------------------------------------------

    @property
    def current_path(self) -> Path | None:
        return self._current_path

    @property
    def is_modified(self) -> bool:
        return self._core.document().isModified()

    def set_content(self, text: str, path: Path | None = None) -> None:
        self._current_path = path
        self._core.setPlainText(text)
        self._core.document().setModified(False)

    def mark_saved(self, path: Path | None = None) -> None:
        if path is not None:
            self._current_path = path
        self._core.document().setModified(False)

    # ------------------------------------------------------------------
    # Find & Replace (Ctrl+H / Ctrl+F)
    # ------------------------------------------------------------------

    def show_find_replace(self) -> None:
        self._find_bar.show_and_focus()

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent
        if obj is self._core and event.type() == QEvent.Type.KeyPress:
            if event.matches(QKeySequence.StandardKey.Find) or \
               (event.key() == Qt.Key.Key_H and
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self._find_bar.show_and_focus()
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Auto-complete
    # ------------------------------------------------------------------

    def _refresh_completions(self) -> None:
        extra: set[str] = set()
        for line in self._core.toPlainText().splitlines():
            s = line.strip()
            if s.lower().startswith((".model ", ".subckt ")):
                parts = s.split()
                if len(parts) >= 2:
                    extra.add(parts[1])
        words = _DOT_COMPLETIONS + sorted(extra)
        self._completer.setModel(QStringListModel(words, self._completer))

    def _word_prefix(self) -> tuple[str, int]:
        cursor = self._core.textCursor()
        col = cursor.positionInBlock()
        line = cursor.block().text()
        i = col
        while i > 0 and line[i - 1] not in " \t":
            i -= 1
        return line[i:col], i

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
        cr = self._core.cursorRect()
        cr.setWidth(
            self._completer.popup().sizeHintForColumn(0) + scroll.sizeHint().width()
        )
        self._completer.complete(cr)

    def _insert_completion(self, completion: str) -> None:
        prefix = self._completer.completionPrefix()
        cursor = self._core.textCursor()
        cursor.movePosition(
            QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, len(prefix)
        )
        cursor.insertText(completion)
        self._core.setTextCursor(cursor)

    def keyPressEvent(self, event) -> None:
        popup = self._completer.popup()
        if popup.isVisible() and event.key() in (
            Qt.Key.Key_Return, Qt.Key.Key_Enter,
            Qt.Key.Key_Tab, Qt.Key.Key_Escape,
        ):
            event.ignore()
            return
        super().keyPressEvent(event)
        self._trigger_completer()

    # ------------------------------------------------------------------
    # .include resolver
    # ------------------------------------------------------------------

    def _open_include(self, rel_path: str) -> None:
        base = self._current_path.parent if self._current_path else Path.cwd()
        p = (base / rel_path).resolve()
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                dlg = _IncludeViewer(str(p), text, self)
                dlg.exec()
            except OSError as exc:
                QMessageBox.warning(self, ".include", str(exc))
        else:
            QMessageBox.information(self, ".include", f"File not found:\n{p}")

    # ------------------------------------------------------------------
    # Error markers
    # ------------------------------------------------------------------

    def mark_errors(self, errors: list[tuple[int, str]]) -> None:
        doc = self._core.document()
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
        self._core.setExtraSelections(sels)

    def clear_errors(self) -> None:
        self._core.setExtraSelections([])


# ---------------------------------------------------------------------------
# Simple read-only viewer for .include files
# ---------------------------------------------------------------------------



class _IncludeViewer(QDialog):
    def __init__(self, title: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 500)
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        font = QFont("Monospace", 9)
        font.setStyleHint(QFont.StyleHint.Monospace)
        edit.setFont(font)
        edit.setPlainText(text)
        SpiceHighlighter(edit.document())
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(edit)
        lay.addWidget(btns)

"""SPICE model library browser — scan .lib/.mod files and display .model/.subckt defs."""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

_DEF_RE = re.compile(
    r'^\s*\.(model|subckt)\s+(\w+)(?:\s+(\w+))?', re.IGNORECASE | re.MULTILINE
)


def _scan_file(path: Path) -> list[tuple[str, str, str]]:
    """Return [(kind, name, type_hint), ...] from a lib file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    results = []
    for m in _DEF_RE.finditer(text):
        kind = m.group(1).upper()
        name = m.group(2)
        hint = m.group(3) or ""
        results.append((kind, name, hint))
    return results


class ModelBrowserWidget(QWidget):
    """Browse .lib directories, view model parameters, insert .include into netlist."""

    insert_requested = Signal(str)   # .include line to insert

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._dirs: list[Path] = []
        self._file_map: dict[str, Path] = {}  # model name → source file
        self._build()

    def _build(self) -> None:
        # Directory list
        self._dir_list = QListWidget()
        self._dir_list.setMaximumHeight(80)

        btn_add_dir = QPushButton("Add dir…")
        btn_add_dir.clicked.connect(self._add_dir)
        btn_rem_dir = QPushButton("Remove")
        btn_rem_dir.clicked.connect(self._rem_dir)
        dir_btns = QHBoxLayout()
        dir_btns.addWidget(btn_add_dir)
        dir_btns.addWidget(btn_rem_dir)
        dir_btns.addStretch()

        # Filter
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter models…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)

        # Model tree
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Name", "Kind", "Type"])
        self._tree.setColumnWidth(0, 140)
        self._tree.currentItemChanged.connect(self._on_select)

        # Preview pane
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)

        btn_insert = QPushButton("Insert .include into editor")
        btn_insert.clicked.connect(self._insert)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._tree)
        splitter.addWidget(self._preview)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        lay.addWidget(QLabel("<b>Model Library</b>"))
        lay.addWidget(self._dir_list)
        lay.addLayout(dir_btns)
        lay.addWidget(self._filter)
        lay.addWidget(splitter)
        lay.addWidget(btn_insert)

    def _add_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Add Model Library Directory")
        if d:
            p = Path(d)
            if p not in self._dirs:
                self._dirs.append(p)
                self._dir_list.addItem(str(p))
                self._scan_all()

    def _rem_dir(self) -> None:
        row = self._dir_list.currentRow()
        if row >= 0:
            self._dirs.pop(row)
            self._dir_list.takeItem(row)
            self._scan_all()

    def _scan_all(self) -> None:
        self._file_map.clear()
        self._all_entries: list[tuple[str, str, str, Path]] = []
        for d in self._dirs:
            for ext in ("*.lib", "*.mod", "*.spi", "*.sp", "*.cir"):
                for f in sorted(d.rglob(ext)):
                    for kind, name, hint in _scan_file(f):
                        self._all_entries.append((kind, name, hint, f))
                        self._file_map[name.lower()] = f
        self._apply_filter(self._filter.text())

    def _apply_filter(self, text: str) -> None:
        self._tree.clear()
        low = text.lower()
        for kind, name, hint, path in getattr(self, "_all_entries", []):
            if low and low not in name.lower():
                continue
            item = QTreeWidgetItem([name, kind, hint])
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            self._tree.addTopLevelItem(item)

    def _on_select(self, item: QTreeWidgetItem | None, _prev=None) -> None:
        if item is None:
            self._preview.clear()
            return
        path: Path | None = item.data(0, Qt.ItemDataRole.UserRole)
        name = item.text(0)
        if path is None:
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            # Extract the model block
            pat = re.compile(
                rf'(^\s*\.(?:model|subckt)\s+{re.escape(name)}\b.*?)(?=^\s*\.\w|\Z)',
                re.IGNORECASE | re.MULTILINE | re.DOTALL
            )
            m = pat.search(text)
            snippet = m.group(1).strip() if m else text[:2000]
            self._preview.setPlainText(snippet)
        except OSError as exc:
            self._preview.setPlainText(str(exc))

    def _insert(self) -> None:
        item = self._tree.currentItem()
        if item is None:
            return
        path: Path | None = item.data(0, Qt.ItemDataRole.UserRole)
        if path is None:
            return
        self.insert_requested.emit(f'.include "{path}"')

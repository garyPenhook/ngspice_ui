"""
Co-simulation source editor.

Lets users define Python expressions that drive external voltage / current
sources during an ngspice simulation via ngSpice_Init_Sync callbacks.

Each table row represents one external source:
  ✓ Enable | Source name (matches netlist element, e.g. 'vext') |
    Type (V/I) | Python expression f(t, name) → float

Expressions receive:
  t     — current simulation time (seconds, float)
  name  — element name string as ngspice passes it (e.g. 'vext')
and must evaluate to a float (volts or amps).

Available in expression scope: math, np (numpy).

Optional sync expression:
  sync(t, old_delta) → float | None
  Return a positive float to request a shorter time step, or None / omit
  to accept ngspice's own delta.

Netlist: declare any standard V/I source; ngspice will call the registered
callback for sources it identifies as externally controlled.
Example:
  Vext n_hi n_lo dc 0
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

_EXPR_SCOPE = {"__builtins__": __builtins__, "math": math, "np": np}


def _compile_expr(expr: str, label: str):
    """Compile a user expression into a lambda(t, name) → float."""
    src = f"lambda t, name, math=math, np=np: ({expr})"
    try:
        return eval(src, _EXPR_SCOPE)  # noqa: S307
    except SyntaxError as exc:
        raise ValueError(f"{label}: {exc}") from exc


class CoSimWidget(QWidget):
    """Table-driven co-simulation source editor."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session = None
        self._build_ui()

    def set_session(self, session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        mono = QFont("Monospace", 9)
        mono.setStyleHint(QFont.StyleHint.Monospace)

        info = QLabel(
            "Define Python expressions that drive external V/I sources.\n"
            "Expressions: f(t, name) → float  •  math and np are in scope."
        )
        info.setWordWrap(True)

        # Table
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["En", "Source name", "Type", "Expression  f(t, name)"])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(0, 28)
        self._table.setColumnWidth(2, 44)
        self._table.verticalHeader().setVisible(False)
        self._table.setFont(mono)

        # Row +/- buttons
        btn_add = QPushButton("+")
        btn_add.setFixedWidth(28)
        btn_add.setToolTip("Add source row")
        btn_add.clicked.connect(self._add_row)

        btn_del = QPushButton("−")
        btn_del.setFixedWidth(28)
        btn_del.setToolTip("Remove selected row")
        btn_del.clicked.connect(self._del_row)

        tbl_btns = QHBoxLayout()
        tbl_btns.addWidget(btn_add)
        tbl_btns.addWidget(btn_del)
        tbl_btns.addStretch()

        # Sync expression
        sync_row = QHBoxLayout()
        sync_row.addWidget(QLabel("Sync  δ(t, old_δ):"))
        self._sync_edit = QLineEdit()
        self._sync_edit.setFont(mono)
        self._sync_edit.setPlaceholderText("optional — e.g.  min(old_delta, 1e-6)")
        self._sync_edit.setToolTip(
            "Delta-time negotiation: return a new step length or leave blank to accept ngspice's."
        )
        sync_row.addWidget(self._sync_edit)

        # Apply + status
        self._btn_apply = QPushButton("Apply co-sim")
        self._btn_apply.clicked.connect(self._apply)

        self._btn_disable = QPushButton("Disable")
        self._btn_disable.setToolTip("Unregister callbacks (pass all None)")
        self._btn_disable.clicked.connect(self._disable)

        self._status = QLabel("Co-sim: not registered")
        self._status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        apply_row = QHBoxLayout()
        apply_row.addWidget(self._btn_apply)
        apply_row.addWidget(self._btn_disable)
        apply_row.addWidget(self._status)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.addWidget(info)
        layout.addWidget(self._table)
        layout.addLayout(tbl_btns)
        layout.addLayout(sync_row)
        layout.addLayout(apply_row)

        # Start with one empty row as a hint
        self._add_row()

    # ------------------------------------------------------------------
    # Row management
    # ------------------------------------------------------------------

    def _add_row(self) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)

        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        chk.setCheckState(Qt.CheckState.Checked)
        self._table.setItem(row, 0, chk)

        self._table.setItem(row, 1, QTableWidgetItem("vext"))

        cmb = QComboBox()
        cmb.addItems(["V", "I"])
        self._table.setCellWidget(row, 2, cmb)

        self._table.setItem(row, 3, QTableWidgetItem(""))

    def _del_row(self) -> None:
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        for r in sorted(rows, reverse=True):
            self._table.removeRow(r)

    # ------------------------------------------------------------------
    # Apply / disable
    # ------------------------------------------------------------------

    def _apply(self) -> None:
        if self._session is None:
            self._status.setText("No session available.")
            return

        vsrc_fns: dict[str, object] = {}
        isrc_fns: dict[str, object] = {}

        for row in range(self._table.rowCount()):
            chk_item = self._table.item(row, 0)
            if chk_item is None or chk_item.checkState() != Qt.CheckState.Checked:
                continue

            name_item = self._table.item(row, 1)
            expr_item = self._table.item(row, 3)
            name = (name_item.text().strip().lower() if name_item else "")
            expr = (expr_item.text().strip() if expr_item else "")
            typ = self._table.cellWidget(row, 2)
            src_type = typ.currentText() if typ else "V"

            if not name or not expr:
                continue

            try:
                fn = _compile_expr(expr, f"row {row + 1} ({name})")
            except ValueError as exc:
                self._status.setText(str(exc))
                return

            if src_type == "V":
                vsrc_fns[name] = fn
            else:
                isrc_fns[name] = fn

        # Build dispatch callables
        vsrc_fn = None
        if vsrc_fns:
            _v = dict(vsrc_fns)
            def vsrc_fn(t, name, _d=_v):
                fn = _d.get(name.lower())
                return float(fn(t, name)) if fn is not None else 0.0

        isrc_fn = None
        if isrc_fns:
            _i = dict(isrc_fns)
            def isrc_fn(t, name, _d=_i):
                fn = _d.get(name.lower())
                return float(fn(t, name)) if fn is not None else 0.0

        sync_fn = None
        sync_expr = self._sync_edit.text().strip()
        if sync_expr:
            try:
                _raw = _compile_expr(sync_expr.replace("old_delta", "name"), "sync")
                # sync receives (t, old_delta); reuse lambda(t, name) mapping name→old_delta
                def sync_fn(t, old_delta, _raw=_raw):
                    result = _raw(t, old_delta)
                    return float(result) if result is not None else None
            except ValueError as exc:
                # Retry with correct signature lambda(t, old_delta)
                try:
                    src = f"lambda t, old_delta, math=math, np=np: ({sync_expr})"
                    _sf = eval(src, _EXPR_SCOPE)  # noqa: S307
                    def sync_fn(t, old_delta, _sf=_sf):
                        result = _sf(t, old_delta)
                        return float(result) if result is not None else None
                except SyntaxError as exc2:
                    self._status.setText(f"sync: {exc2}")
                    return

        try:
            self._session.init_sync(vsrc_fn, isrc_fn, sync_fn)
            parts = []
            if vsrc_fns:
                parts.append(f"{len(vsrc_fns)}V: {', '.join(vsrc_fns)}")
            if isrc_fns:
                parts.append(f"{len(isrc_fns)}I: {', '.join(isrc_fns)}")
            if sync_expr:
                parts.append("sync ✓")
            self._status.setText("Co-sim active — " + ("  ".join(parts) or "no-op"))
        except Exception as exc:
            self._status.setText(f"init_sync failed: {exc}")

    def _disable(self) -> None:
        if self._session is None:
            return
        try:
            self._session.init_sync(None, None, None)
            self._status.setText("Co-sim: callbacks cleared (no-op installed)")
        except Exception as exc:
            self._status.setText(f"disable failed: {exc}")

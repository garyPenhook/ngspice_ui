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

Netlist: declare external sources with the ``external`` keyword so ngspice
triggers the registered callbacks.  Using ``dc 0`` instead produces no callbacks.
Example:
  Vext n_hi n_lo external
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...models.expr import SAFE_NUMPY as _SAFE_NUMPY
from ...models.expr import validate_expr as _validate_expr

_EXPR_SCOPE = {"__builtins__": {}, "math": math, "np": _SAFE_NUMPY, "abs": abs, "float": float}

# Names a *source* expression may reference: only the callback parameters bound
# by the compiled lambda (t, name) plus the math/numpy modules in _EXPR_SCOPE.
# 'old_delta' belongs solely to the sync callback — allowing it here passes
# validation but then NameErrors at call time (the source lambda never binds
# it), so a co-sim source would silently output zero.
_COSIM_NAMES = frozenset({"t", "name"})


def _compile_expr(expr: str, label: str):
    """Compile a user expression into a lambda(t, name) → float.

    The expression is first validated against the shared AST allowlist
    (numeric/numpy/math only). ``eval`` with empty builtins is *not* a security
    boundary on its own, so the validation pass is what blocks attribute
    escapes, imports, and arbitrary calls.
    """
    try:
        _validate_expr(expr, _COSIM_NAMES)
    except (ValueError, SyntaxError) as exc:
        raise ValueError(f"{label}: {exc}") from exc
    src = f"lambda t, name, math=math, np=np: ({expr})"
    try:
        return eval(src, _EXPR_SCOPE)  # noqa: S307
    except SyntaxError as exc:
        raise ValueError(f"{label}: {exc}") from exc


class CoSimWidget(QWidget):
    """Table-driven co-simulation source editor."""

    changed = Signal()

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
        self._table.setHorizontalHeaderLabels(
            ["En", "Source name", "Type", "Expression  f(t, name)"]
        )
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

        btn_csv = QPushButton("Load CSV…")
        btn_csv.setToolTip("Load a CSV (time, value) file as a PWL source for the selected row")
        btn_csv.clicked.connect(self._load_csv)

        tbl_btns = QHBoxLayout()
        tbl_btns.addWidget(btn_add)
        tbl_btns.addWidget(btn_del)
        tbl_btns.addWidget(btn_csv)
        tbl_btns.addStretch()

        # Sync expression
        self._table.itemChanged.connect(self.changed)

        sync_row = QHBoxLayout()
        sync_row.addWidget(QLabel("Sync  δ(t, old_δ):"))
        self._sync_edit = QLineEdit()
        self._sync_edit.setFont(mono)
        self._sync_edit.setPlaceholderText("optional — e.g.  min(old_delta, 1e-6)")
        self._sync_edit.setToolTip(
            "Delta-time negotiation: return a new step length or leave blank to accept ngspice's."
        )
        self._sync_edit.textChanged.connect(self.changed)
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
            name = name_item.text().strip().lower() if name_item else ""
            expr = expr_item.text().strip() if expr_item else ""
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
            # sync receives (t, old_delta); validate then compile with that
            # exact signature (no name-substitution tricks).
            try:
                _validate_expr(sync_expr, frozenset({"t", "old_delta"}))
                _sf = eval(  # noqa: S307
                    f"lambda t, old_delta, math=math, np=np: ({sync_expr})", _EXPR_SCOPE
                )
            except (ValueError, SyntaxError) as exc:
                self._status.setText(f"sync: {exc}")
                return

            def sync_fn(t, old_delta, _sf=_sf):
                result = _sf(t, old_delta)
                return float(result) if result is not None else None

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

    def _clear_live_callbacks(self) -> None:
        """Unregister any callbacks currently installed in the engine (quietly)."""
        if self._session is None:
            return
        try:
            self._session.init_sync(None, None, None)
        except Exception:
            pass

    def _disable(self) -> None:
        if self._session is None:
            return
        try:
            self._session.init_sync(None, None, None)
            self._status.setText("Co-sim: callbacks cleared (no-op installed)")
        except Exception as exc:
            self._status.setText(f"disable failed: {exc}")

    def _load_csv(self) -> None:
        """Load a (time, value) CSV and generate a PWL interpolation expression."""
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        row = next(iter(rows)) if rows else -1
        if row < 0:
            QMessageBox.information(self, "Load CSV", "Select a row first.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open CSV",
            str(Path.home()),
            "CSV files (*.csv *.txt);;All Files (*)",
        )
        if not path:
            return
        try:
            times: list[float] = []
            values: list[float] = []
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                for r in reader:
                    if len(r) < 2:
                        continue
                    try:
                        times.append(float(r[0]))
                        values.append(float(r[1]))
                    except ValueError:
                        continue
            if len(times) < 2:
                QMessageBox.warning(self, "Load CSV", "Need at least 2 data rows.")
                return
            # np.interp requires strictly increasing sample times; unsorted or
            # duplicated times silently produce wrong interpolation. Sort the
            # pairs by time and reject duplicates rather than feeding np.interp
            # invalid input.
            order = sorted(range(len(times)), key=lambda k: times[k])
            was_unsorted = order != list(range(len(times)))
            times = [times[k] for k in order]
            values = [values[k] for k in order]
            if any(times[k] == times[k + 1] for k in range(len(times) - 1)):
                QMessageBox.warning(
                    self,
                    "Load CSV",
                    "Time column has duplicate values; times must be strictly "
                    "increasing for interpolation.",
                )
                return
            # Build a numpy-interp expression referencing inline arrays
            t_str = repr(times)
            v_str = repr(values)
            expr = f"float(np.interp(t, {t_str}, {v_str}))"
            item = self._table.item(row, 3)
            if item is None:
                self._table.setItem(row, 3, QTableWidgetItem(expr))
            else:
                item.setText(expr)
            note = " (reordered by time)" if was_unsorted else ""
            self._status.setText(f"Loaded {len(times)} points from {Path(path).name}{note}")
        except Exception as exc:
            QMessageBox.critical(self, "Load CSV Error", str(exc))

    # ------------------------------------------------------------------
    # Project serialisation
    # ------------------------------------------------------------------

    def get_config(self) -> dict:
        rows: list[dict] = []
        for r in range(self._table.rowCount()):
            chk = self._table.item(r, 0)
            name_item = self._table.item(r, 1)
            expr_item = self._table.item(r, 3)
            cmb = self._table.cellWidget(r, 2)
            rows.append(
                {
                    "enabled": chk.checkState() == Qt.CheckState.Checked if chk else True,
                    "name": name_item.text() if name_item else "",
                    "type": cmb.currentText() if cmb else "V",
                    "expr": expr_item.text() if expr_item else "",
                }
            )
        return {
            "rows": rows,
            "sync": self._sync_edit.text(),
        }

    def set_config(self, cfg: dict) -> None:
        if not isinstance(cfg, dict):
            cfg = {}
        # Replacing the config invalidates any callbacks a previous project
        # registered with the engine. Unregister them so an old project's source
        # functions can't silently keep driving a newly loaded circuit.
        self._clear_live_callbacks()
        while self._table.rowCount() > 0:
            self._table.removeRow(0)

        def _str(v) -> str:
            return v if isinstance(v, str) else ""

        rows = cfg.get("rows", [])
        if not isinstance(rows, list):
            rows = []
        for rd in rows:
            if not isinstance(rd, dict):
                continue
            self._add_row()
            r = self._table.rowCount() - 1
            chk = self._table.item(r, 0)
            if chk:
                chk.setCheckState(
                    Qt.CheckState.Checked if rd.get("enabled", True) else Qt.CheckState.Unchecked
                )
            name_item = self._table.item(r, 1)
            if name_item:
                name_item.setText(_str(rd.get("name", "")))
            cmb = self._table.cellWidget(r, 2)
            if cmb:
                idx = cmb.findText(_str(rd.get("type", "V")) or "V")
                if idx >= 0:
                    cmb.setCurrentIndex(idx)
            expr_item = self._table.item(r, 3)
            if expr_item:
                expr_item.setText(_str(rd.get("expr", "")))
        self._sync_edit.setText(_str(cfg.get("sync", "")))
        self._status.setText("Co-sim: not registered")

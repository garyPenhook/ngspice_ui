from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class MeasurementsWidget(QWidget):
    """Table of named measurement expressions evaluated against the current simulation.

    Expression language: Python with ``np`` (numpy) and a ``vec(name)``
    helper that fetches a simulation vector as a real numpy array.

    Examples::

        np.max(vec("v(out)"))                          # peak voltage
        np.ptp(vec("v(out)"))                          # peak-to-peak
        np.sqrt(np.mean(vec("v(out)") ** 2))           # RMS
        np.max(vec("v(out)")) - np.max(vec("v(in)"))   # delta
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Name", "Expression", "Value"])
        self._table.itemChanged.connect(self.changed)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        outer.addWidget(self._table)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("Add")
        btn_add.setToolTip("Add a measurement row")
        btn_add.clicked.connect(self._add_row)
        btn_rem = QPushButton("Remove")
        btn_rem.setToolTip("Remove selected row(s)")
        btn_rem.clicked.connect(self._remove_selected)
        btn_eval = QPushButton("Evaluate")
        btn_eval.setToolTip("Evaluate all expressions against the current simulation result")
        btn_eval.clicked.connect(self.evaluate)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_rem)
        btn_row.addStretch()
        btn_row.addWidget(btn_eval)
        outer.addLayout(btn_row)

    def set_session(self, session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Table helpers
    # ------------------------------------------------------------------

    def _add_row(self) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(f"meas{row + 1}"))
        self._table.setItem(row, 1, QTableWidgetItem(""))
        val = QTableWidgetItem("")
        val.setFlags(val.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(row, 2, val)

    def _remove_selected(self) -> None:
        rows = sorted(
            {idx.row() for idx in self._table.selectedIndexes()}, reverse=True
        )
        for r in rows:
            self._table.removeRow(r)

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    @Slot()
    def evaluate(self) -> None:
        if self._session is None:
            return
        plot = self._session.current_plot()
        if not plot or plot == "const":
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 2)
                if item:
                    item.setText("(no data)")
            return

        def _vec(name: str) -> np.ndarray:
            for candidate in (name, f"{plot}.{name}"):
                try:
                    return self._session.get_vector(candidate).data
                except Exception:
                    pass
            raise KeyError(f"vector not found: {name!r}")

        ns: dict = {
            "__builtins__": {},   # no builtins — prevents exec/open/import in loaded projects
            "np": np,
            "math": math,
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "sum": sum,
            "len": len,
            "float": float,
            "int": int,
            "bool": bool,
            "vec": _vec,
        }

        for row in range(self._table.rowCount()):
            expr_item = self._table.item(row, 1)
            val_item = self._table.item(row, 2)
            if expr_item is None or val_item is None:
                continue
            expr = expr_item.text().strip()
            if not expr:
                val_item.setText("")
                continue
            try:
                result = eval(expr, ns)  # noqa: S307
                if isinstance(result, np.ndarray):
                    result = float(result.flat[-1])
                if isinstance(result, complex):
                    angle = math.degrees(math.atan2(result.imag, result.real))
                    val_item.setText(f"{abs(result):.6g} ∠{angle:.2f}°")
                else:
                    val_item.setText(f"{float(result):.6g}")
            except Exception as exc:
                val_item.setText(f"ERR: {exc}")

    # ------------------------------------------------------------------
    # Project serialisation
    # ------------------------------------------------------------------

    def get_config(self) -> list[dict]:
        rows: list[dict] = []
        for row in range(self._table.rowCount()):
            n = self._table.item(row, 0)
            e = self._table.item(row, 1)
            rows.append({
                "name": n.text() if n else "",
                "expr": e.text() if e else "",
            })
        return rows

    def set_config(self, rows: list[dict]) -> None:
        self._table.setRowCount(0)
        for r in rows:
            self._add_row()
            i = self._table.rowCount() - 1
            n = self._table.item(i, 0)
            e = self._table.item(i, 1)
            if n:
                n.setText(r.get("name", ""))
            if e:
                e.setText(r.get("expr", ""))

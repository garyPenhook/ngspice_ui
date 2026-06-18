"""Monte Carlo analysis widget — run N simulations with randomised component values."""
from __future__ import annotations

import re

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# Matches component value tokens we can randomise
_VAL_RE = re.compile(
    r'(\b\w+\b\s+\w+\s+\w+\s+)(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?:MEG|[kKmMuUnNpPfFgGtT])?)',
    re.IGNORECASE,
)


def _parse_spice_val(s: str) -> float:
    """Convert SPICE value string to float."""
    s = s.strip()
    suffix_map = {
        'T': 1e12, 'G': 1e9, 'MEG': 1e6, 'K': 1e3, 'k': 1e3,
        'm': 1e-3, 'u': 1e-6, 'U': 1e-6, 'n': 1e-9, 'N': 1e-9,
        'p': 1e-12, 'P': 1e-12, 'f': 1e-15, 'F': 1e-15,
    }
    for sfx in ('MEG', 'T', 'G', 'K', 'k', 'm', 'u', 'U', 'n', 'N', 'p', 'P', 'f', 'F'):
        if s.upper().endswith(sfx.upper()):
            return float(s[:-len(sfx)]) * suffix_map.get(sfx, 1.0)
    return float(s)


class MonteCarloWidget(QWidget):
    """Configure and trigger Monte Carlo runs.

    Emits ``run_mc`` with a list of modified netlists (one per run).
    """

    run_mc = Signal(list)   # list[str] — one netlist string per run

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._netlist: str = ""
        self._build()

    def set_netlist(self, text: str) -> None:
        self._netlist = text

    def _build(self) -> None:
        self._runs_edit = QLineEdit("20")
        self._runs_edit.setToolTip("Number of Monte Carlo runs")

        # Variation table: Reference | Nominal | Variation% | Distribution
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Component", "Nominal", "Variation %", "Distribution"])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(2, 80)

        btn_add = QPushButton("+")
        btn_add.setFixedWidth(28)
        btn_add.clicked.connect(self._add_row)
        btn_del = QPushButton("−")
        btn_del.setFixedWidth(28)
        btn_del.clicked.connect(self._del_row)
        btn_scan = QPushButton("Scan netlist")
        btn_scan.setToolTip("Auto-detect R/L/C values from the current netlist")
        btn_scan.clicked.connect(self._scan_netlist)

        row_btns = QHBoxLayout()
        row_btns.addWidget(btn_add)
        row_btns.addWidget(btn_del)
        row_btns.addWidget(btn_scan)
        row_btns.addStretch()

        self._status = QLabel()

        btn_run = QPushButton("Run Monte Carlo")
        btn_run.clicked.connect(self._emit_mc)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)
        lay.addWidget(QLabel("<b>Monte Carlo</b>"))
        f = QFormLayout()
        f.addRow("Runs:", self._runs_edit)
        lay.addLayout(f)
        lay.addWidget(QLabel("Variations:"))
        lay.addWidget(self._table)
        lay.addLayout(row_btns)
        lay.addWidget(self._status)
        lay.addWidget(btn_run)
        lay.addStretch()

    def _add_row(self, comp: str = "", nom: str = "", var: str = "5") -> None:
        r = self._table.rowCount()
        self._table.insertRow(r)
        self._table.setItem(r, 0, QTableWidgetItem(comp))
        self._table.setItem(r, 1, QTableWidgetItem(nom))
        self._table.setItem(r, 2, QTableWidgetItem(var))
        cmb = QComboBox()
        cmb.addItems(["Gaussian", "Uniform"])
        self._table.setCellWidget(r, 3, cmb)

    def _del_row(self) -> None:
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        for r in sorted(rows, reverse=True):
            self._table.removeRow(r)

    def _scan_netlist(self) -> None:
        if not self._netlist:
            self._status.setText("No netlist loaded.")
            return
        self._table.setRowCount(0)
        found = 0
        for line in self._netlist.splitlines():
            s = line.strip()
            if not s or s.startswith("*") or s.startswith("."):
                continue
            parts = s.split()
            if len(parts) >= 4 and parts[0][0].upper() in "RLC":
                ref = parts[0]
                val_str = parts[3] if len(parts) > 3 else ""
                try:
                    _parse_spice_val(val_str)
                    self._add_row(ref, val_str, "5")
                    found += 1
                except (ValueError, IndexError):
                    pass
        self._status.setText(f"Found {found} component(s)")

    def _vary(self, nominal_str: str, pct: float, dist: str) -> float:
        try:
            nom = _parse_spice_val(nominal_str)
        except ValueError:
            return 0.0
        rel = pct / 100.0
        if dist == "Gaussian":
            return nom * (1 + np.random.normal(0, rel / 3))
        else:
            return nom * (1 + np.random.uniform(-rel, rel))

    def _emit_mc(self) -> None:
        try:
            n_runs = int(self._runs_edit.text())
        except ValueError:
            n_runs = 20

        variations: list[tuple[str, str, float, str]] = []
        for r in range(self._table.rowCount()):
            comp_item = self._table.item(r, 0)
            nom_item = self._table.item(r, 1)
            var_item = self._table.item(r, 2)
            cmb = self._table.cellWidget(r, 3)
            comp = comp_item.text().strip() if comp_item else ""
            nom = nom_item.text().strip() if nom_item else ""
            dist = cmb.currentText() if cmb else "Gaussian"
            try:
                pct = float(var_item.text()) if var_item else 5.0
            except ValueError:
                pct = 5.0
            if comp and nom:
                variations.append((comp, nom, pct, dist))

        netlists = []
        base = self._netlist
        for _ in range(n_runs):
            text = base
            for comp, nom, pct, dist in variations:
                new_val = self._vary(nom, pct, dist)
                text = re.sub(
                    rf'(?m)^(\s*{re.escape(comp)}\s+\S+\s+\S+\s+)\S+',
                    lambda m, v=new_val: m.group(1) + f"{v:.6g}",
                    text, count=1,
                )
            netlists.append(text)

        self._status.setText(f"Running {n_runs} simulations…")
        self.run_mc.emit(netlists)

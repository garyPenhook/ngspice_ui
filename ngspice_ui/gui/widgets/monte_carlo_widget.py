"""Monte Carlo analysis widget — run N simulations with randomised component values."""

from __future__ import annotations

from PySide6.QtCore import Signal
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

from ...models.monte_carlo import generate_netlists
from ...models.monte_carlo import parse_spice_val as _parse_spice_val


class MonteCarloWidget(QWidget):
    """Configure and trigger Monte Carlo runs.

    Emits ``run_mc`` with a list of modified netlists (one per run).
    """

    run_mc = Signal(list)  # list[str] — one netlist string per run

    # Guard rails on the run count: at least one run, and a ceiling so a stray
    # huge value can't exhaust memory generating netlists or flood the run
    # queue. 10000 is far more than any practical Monte Carlo sweep needs.
    _MAX_RUNS = 10_000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._netlist: str = ""
        self._netlist_getter = None
        self._build()

    def set_netlist(self, text: str) -> None:
        self._netlist = text

    def set_netlist_getter(self, fn) -> None:
        """Register a callable that returns the current netlist text on demand."""
        self._netlist_getter = fn

    def _build(self) -> None:
        self._runs_edit = QLineEdit("20")
        self._runs_edit.setToolTip("Number of Monte Carlo runs")

        # Variation table: Reference | Nominal | Variation% | Distribution
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["Component", "Nominal", "Variation %", "Distribution"]
        )
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

    def _emit_mc(self) -> None:
        try:
            n_runs = int(self._runs_edit.text())
        except ValueError:
            self._status.setText("Runs must be a positive integer.")
            return
        if n_runs < 1:
            self._status.setText("Runs must be at least 1.")
            return
        if n_runs > self._MAX_RUNS:
            self._status.setText(f"Runs capped at {self._MAX_RUNS}.")
            n_runs = self._MAX_RUNS
            self._runs_edit.setText(str(n_runs))

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

        base = self._netlist_getter() if self._netlist_getter else self._netlist
        if not base:
            self._status.setText("No netlist — load or run a simulation first.")
            return

        netlists = generate_netlists(base, variations, n_runs)
        self._status.setText(f"Running {n_runs} simulations…")
        self.run_mc.emit(netlists)

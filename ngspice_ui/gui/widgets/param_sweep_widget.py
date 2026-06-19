"""Parametric sweep widget — inject .param + .step into netlist and run N times."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class ParamSweepWidget(QWidget):
    """Configure and trigger a parametric (.step param) sweep.

    Emits ``run_sweep`` with (param_name, values_list) so the caller can
    inject the .param + .step lines into the netlist and run.
    """

    run_sweep = Signal(str, list)  # (param_name, [value, ...])

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g.  Rload")

        # Mode: linear sweep or list
        self._radio_lin = QRadioButton("Linear")
        self._radio_lin.setChecked(True)
        self._radio_list = QRadioButton("List")
        self._radio_lin.toggled.connect(self._on_mode)

        mode_row = QHBoxLayout()
        mode_row.addWidget(self._radio_lin)
        mode_row.addWidget(self._radio_list)
        mode_row.addStretch()

        # Linear controls
        self._start_edit = QLineEdit("0")
        self._stop_edit = QLineEdit("1k")
        self._step_edit = QLineEdit("100")

        self._lin_box = QGroupBox()
        self._lin_box.setFlat(True)
        lf = QFormLayout(self._lin_box)
        lf.setContentsMargins(0, 0, 0, 0)
        lf.addRow("Start:", self._start_edit)
        lf.addRow("Stop:", self._stop_edit)
        lf.addRow("Step:", self._step_edit)

        # List controls
        self._list_edit = QLineEdit()
        self._list_edit.setPlaceholderText("space-separated: 100 1k 10k 100k")
        self._list_box = QGroupBox()
        self._list_box.setFlat(True)
        ll = QFormLayout(self._list_box)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addRow("Values:", self._list_edit)
        self._list_box.hide()

        # Preview
        self._preview = QLabel()
        self._preview.setWordWrap(True)
        self._preview.setStyleSheet("color:#555; font-family:monospace; font-size:9pt;")

        self._name_edit.textChanged.connect(self._update_preview)
        self._start_edit.textChanged.connect(self._update_preview)
        self._stop_edit.textChanged.connect(self._update_preview)
        self._step_edit.textChanged.connect(self._update_preview)
        self._list_edit.textChanged.connect(self._update_preview)

        btn_run = QPushButton("Run Sweep")
        btn_run.clicked.connect(self._emit_sweep)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)
        lay.addWidget(QLabel("<b>Parametric Sweep</b>"))
        f = QFormLayout()
        f.addRow("Parameter:", self._name_edit)
        lay.addLayout(f)
        lay.addLayout(mode_row)
        lay.addWidget(self._lin_box)
        lay.addWidget(self._list_box)
        lay.addWidget(self._preview)
        lay.addWidget(btn_run)
        lay.addStretch()

        self._update_preview()

    def _on_mode(self, lin: bool) -> None:
        self._lin_box.setVisible(lin)
        self._list_box.setVisible(not lin)
        self._update_preview()

    def _values(self) -> list[str]:
        if self._radio_lin.isChecked():
            try:
                start = float(self._start_edit.text())
                stop = float(self._stop_edit.text())
                step = float(self._step_edit.text())
                if step == 0:
                    return []
                vals = []
                v = start
                while v <= stop + abs(step) * 1e-9:
                    vals.append(str(v))
                    v += step
                return vals
            except ValueError:
                return []
        else:
            return self._list_edit.text().split()

    def _update_preview(self) -> None:
        name = self._name_edit.text().strip() or "param"
        vals = self._values()
        if not vals:
            self._preview.setText("")
            return
        if self._radio_lin.isChecked():
            start = self._start_edit.text().strip()
            stop = self._stop_edit.text().strip()
            step = self._step_edit.text().strip()
            line = f".step param {name} {start} {stop} {step}"
        else:
            line = f".step param {name} list {' '.join(vals)}"
        self._preview.setText(f".param {name}=0\n{line}")

    def _emit_sweep(self) -> None:
        name = self._name_edit.text().strip()
        vals = self._values()
        if not name or not vals:
            return
        self.run_sweep.emit(name, vals)

    def get_step_lines(self) -> list[str]:
        """Return the .param + .step lines to prepend to the netlist."""
        name = self._name_edit.text().strip()
        if not name:
            return []
        vals = self._values()
        if not vals:
            return []
        if self._radio_lin.isChecked():
            start = self._start_edit.text().strip()
            stop = self._stop_edit.text().strip()
            step = self._step_edit.text().strip()
            return [
                f".param {name}=0",
                f".step param {name} {start} {stop} {step}",
            ]
        return [
            f".param {name}=0",
            f".step param {name} list {' '.join(vals)}",
        ]

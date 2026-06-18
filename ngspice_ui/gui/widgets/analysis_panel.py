"""Analysis configuration panel — analysis type picker + parameter form."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ngspice_ui.models.analyses import ANALYSES, ANALYSIS_KEY_ORDER, AnalysisSpec, ParamSpec


class AnalysisPanel(QWidget):
    """Select an analysis type and fill in its parameters.

    Emits ``analysis_changed`` whenever the user edits anything so the
    caller can update a live preview or enable/disable Run.
    """

    analysis_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._param_widgets: list[dict[str, QWidget]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(8)

        outer.addWidget(QLabel("<b>Analysis</b>"))

        self._combo = QComboBox()
        self._combo.addItem("Use netlist as-is", userData=None)
        for key in ANALYSIS_KEY_ORDER:
            self._combo.addItem(ANALYSES[key].label, userData=key)
        outer.addWidget(self._combo)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        # Temperature sweep (optional, applies to any analysis)
        temp_box = QGroupBox("Temperature")
        temp_box.setCheckable(True)
        temp_box.setChecked(False)
        temp_box.setFlat(True)
        tl = QHBoxLayout(temp_box)
        tl.setContentsMargins(4, 4, 4, 4)
        self._temp_edit = QLineEdit()
        self._temp_edit.setPlaceholderText("27  or  0 50 100")
        self._temp_edit.setToolTip(
            "Single temperature (°C) or space-separated list for a .step temp list sweep"
        )
        self._temp_edit.textChanged.connect(self._update_preview)
        tl.addWidget(QLabel("°C:"))
        tl.addWidget(self._temp_edit)
        self._temp_box = temp_box
        temp_box.toggled.connect(self._update_preview)
        outer.addWidget(temp_box)

        # Preview label
        self._preview = QLabel()
        self._preview.setWordWrap(True)
        self._preview.setStyleSheet(
            "color: #555; font-family: monospace; font-size: 9pt;"
        )
        outer.addWidget(self._preview)

        outer.addStretch()

        # Page 0: "use netlist"
        lbl = QLabel("Run the analysis embedded\nin the netlist unchanged.")
        lbl.setWordWrap(True)
        self._stack.addWidget(lbl)
        self._param_widgets.append({})

        # Pages 1..N
        for key in ANALYSIS_KEY_ORDER:
            page, widgets = self._make_page(ANALYSES[key])
            self._stack.addWidget(page)
            self._param_widgets.append(widgets)

        self._combo.currentIndexChanged.connect(self._on_combo_changed)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._update_preview()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_page(self, spec: AnalysisSpec) -> tuple[QWidget, dict[str, QWidget]]:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 4, 0, 0)
        form.setSpacing(6)
        widgets: dict[str, QWidget] = {}

        if not spec.params:
            form.addRow(QLabel("No parameters needed."))
            return page, widgets

        for p in spec.params:
            w: QWidget
            if p.kind == "choice":
                cb = QComboBox()
                for ch in p.choices:
                    cb.addItem(ch)
                if p.default:
                    idx = cb.findText(p.default)
                    if idx >= 0:
                        cb.setCurrentIndex(idx)
                cb.currentIndexChanged.connect(self._update_preview)
                w = cb
            else:
                le = QLineEdit()
                if p.placeholder:
                    le.setPlaceholderText(p.placeholder)
                if p.default:
                    le.setText(p.default)
                le.textChanged.connect(self._update_preview)
                w = le
            widgets[p.name] = w
            label_text = p.label + (":" if p.required else " (opt):")
            form.addRow(label_text, w)

        return page, widgets

    def _on_combo_changed(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)
        self._update_preview()
        self.analysis_changed.emit()

    def _update_preview(self) -> None:
        lines = []
        temp_line = self._get_temp_line()
        if temp_line:
            lines.append(temp_line)
        main_line = self.get_netlist_line()
        if main_line:
            lines.append(main_line)
        self._preview.setText("\n".join(lines) if lines else "(from netlist)")
        self.analysis_changed.emit()

    def _get_temp_line(self) -> str:
        if not self._temp_box.isChecked():
            return ""
        val = self._temp_edit.text().strip()
        if not val:
            return ""
        temps = val.split()
        if len(temps) == 1:
            return f".temp {temps[0]}"
        return ".step temp list " + " ".join(temps)

    def _get_widgets(self, key: str) -> dict[str, QWidget]:
        idx = self._combo.currentIndex()
        return self._param_widgets[idx]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def selected_key(self) -> str | None:
        idx = self._combo.currentIndex()
        return None if idx == 0 else ANALYSIS_KEY_ORDER[idx - 1]

    def get_netlist_line(self) -> str | None:
        """Return a dot-command line like '.tran 1us 1ms', or None to leave netlist unchanged."""
        key = self.selected_key()
        if key is None:
            return None
        spec = ANALYSES[key]
        idx = self._combo.currentIndex()
        widgets = self._param_widgets[idx]

        def _val(name: str) -> str:
            w = widgets.get(name)
            if w is None:
                return ""
            if isinstance(w, QComboBox):
                return w.currentText()
            assert isinstance(w, QLineEdit)
            return w.text().strip()

        if key == "tran":
            tstep = _val("tstep")
            tstop = _val("tstop")
            tmax = _val("tmax")
            uic = _val("uic")
            cmd = f".tran {tstep} {tstop}"
            if tmax:
                cmd += f" {tmax}"
            if uic == "Yes":
                if not tmax:
                    cmd += " 0"
                cmd += " uic"
            return cmd

        if key == "dc":
            src = _val("src")
            vstart = _val("vstart")
            vstop = _val("vstop")
            vincr = _val("vincr")
            src2 = _val("src2")
            cmd = f".dc {src} {vstart} {vstop} {vincr}"
            if src2:
                vstart2 = _val("vstart2")
                vstop2 = _val("vstop2")
                vincr2 = _val("vincr2")
                cmd += f" {src2} {vstart2} {vstop2} {vincr2}"
            return cmd

        kwargs: dict[str, str] = {}
        for p in spec.params:
            w = widgets[p.name]
            if isinstance(w, QComboBox):
                kwargs[p.name] = w.currentText()
            else:
                assert isinstance(w, QLineEdit)
                kwargs[p.name] = w.text().strip()
        return "." + spec.command.format(**kwargs)

    def get_temperature_lines(self) -> list[str]:
        """Return .temp/.step lines to prepend, or empty list."""
        line = self._get_temp_line()
        return [line] if line else []

    def get_config(self) -> dict:
        """Return a JSON-serialisable dict describing the current analysis setup."""
        key = self.selected_key()
        cfg: dict = {"key": key}

        if key is not None:
            idx = self._combo.currentIndex()
            widgets = self._param_widgets[idx]
            params: dict[str, str] = {}
            for p in ANALYSES[key].params:
                w = widgets[p.name]
                if isinstance(w, QComboBox):
                    params[p.name] = w.currentText()
                else:
                    assert isinstance(w, QLineEdit)
                    params[p.name] = w.text().strip()
            cfg["params"] = params

        cfg["temp_enabled"] = self._temp_box.isChecked()
        cfg["temp_value"] = self._temp_edit.text().strip()
        return cfg

    def set_config(self, cfg: dict) -> None:
        """Restore analysis setup from a dict previously returned by get_config()."""
        key = cfg.get("key")
        if key is None:
            self._combo.setCurrentIndex(0)
        else:
            for i, k in enumerate(ANALYSIS_KEY_ORDER):
                if k == key:
                    self._combo.setCurrentIndex(i + 1)
                    break
            params = cfg.get("params", {})
            idx = self._combo.currentIndex()
            widgets = self._param_widgets[idx]
            for p in ANALYSES[key].params:
                w = widgets.get(p.name)
                if w is None:
                    continue
                val = params.get(p.name, "")
                if isinstance(w, QComboBox):
                    i2 = w.findText(val)
                    if i2 >= 0:
                        w.setCurrentIndex(i2)
                else:
                    assert isinstance(w, QLineEdit)
                    w.setText(val)

        self._temp_box.setChecked(cfg.get("temp_enabled", False))
        self._temp_edit.setText(cfg.get("temp_value", ""))

    def validate(self) -> list[str]:
        """Return a list of human-readable error messages for missing required fields."""
        key = self.selected_key()
        if key is None:
            return []
        spec = ANALYSES[key]
        idx = self._combo.currentIndex()
        widgets = self._param_widgets[idx]
        errors: list[str] = []
        for p in spec.params:
            if not p.required:
                continue
            if p.kind == "text":
                w = widgets[p.name]
                assert isinstance(w, QLineEdit)
                if not w.text().strip():
                    errors.append(f"{p.label} is required.")
        return errors

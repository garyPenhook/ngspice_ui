"""Analysis configuration panel — analysis type picker + parameter form."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
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
        # _param_widgets[combo_index] → {param_name: widget}
        # combo index 0 = "Use netlist as-is"
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

        # Preview label — shows the generated dot-command
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

        # Pages 1..N: one per analysis key
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
            form.addRow(p.label + ":", w)

        return page, widgets

    def _on_combo_changed(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)
        self._update_preview()
        self.analysis_changed.emit()

    def _update_preview(self) -> None:
        line = self.get_netlist_line()
        self._preview.setText(line if line else "(from netlist)")
        self.analysis_changed.emit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def selected_key(self) -> str | None:
        """Return the analysis key (e.g. 'tran') or None for 'use netlist'."""
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
        kwargs: dict[str, str] = {}
        for p in spec.params:
            w = widgets[p.name]
            if isinstance(w, QComboBox):
                kwargs[p.name] = w.currentText()
            else:
                assert isinstance(w, QLineEdit)
                kwargs[p.name] = w.text().strip()
        return "." + spec.command.format(**kwargs)

    def get_config(self) -> dict:
        """Return a JSON-serialisable dict describing the current analysis setup."""
        key = self.selected_key()
        if key is None:
            return {"key": None}
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
        return {"key": key, "params": params}

    def set_config(self, cfg: dict) -> None:
        """Restore analysis setup from a dict previously returned by get_config()."""
        key = cfg.get("key")
        if key is None:
            self._combo.setCurrentIndex(0)
            return
        for i, k in enumerate(ANALYSIS_KEY_ORDER):
            if k == key:
                self._combo.setCurrentIndex(i + 1)
                break
        else:
            return
        params = cfg.get("params", {})
        idx = self._combo.currentIndex()
        widgets = self._param_widgets[idx]
        for p in ANALYSES[key].params:
            w = widgets.get(p.name)
            if w is None:
                continue
            val = params.get(p.name, "")
            if isinstance(w, QComboBox):
                i = w.findText(val)
                if i >= 0:
                    w.setCurrentIndex(i)
            else:
                assert isinstance(w, QLineEdit)
                w.setText(val)

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
            if p.kind == "text":
                w = widgets[p.name]
                assert isinstance(w, QLineEdit)
                if not w.text().strip():
                    errors.append(f"{p.label} is required.")
        return errors

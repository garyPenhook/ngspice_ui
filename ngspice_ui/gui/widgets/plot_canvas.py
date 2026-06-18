from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.backends.backend_qt import NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

_SCALE_NAMES = frozenset({"time", "frequency", "v-sweep", "i-sweep", "sweep"})
_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


def _fmt(v: float) -> str:
    a = abs(v)
    if a == 0:
        return "0"
    if a >= 1e9:
        return f"{v / 1e9:.4g}G"
    if a >= 1e6:
        return f"{v / 1e6:.4g}M"
    if a >= 1e3:
        return f"{v / 1e3:.4g}k"
    if a >= 0.1:
        return f"{v:.4g}"
    if a >= 1e-3:
        return f"{v * 1e3:.4g}m"
    if a >= 1e-6:
        return f"{v * 1e6:.4g}µ"
    if a >= 1e-9:
        return f"{v * 1e9:.4g}n"
    return f"{v:.4g}"


# ---------------------------------------------------------------------------
# Single plot pane
# ---------------------------------------------------------------------------

class _PlotPane(QWidget):
    """One matplotlib figure with trace tree, cursors, and toolbar."""

    # plot mode constants
    MODE_AUTO   = "auto"
    MODE_BODE   = "bode"
    MODE_NYQUIST = "nyquist"
    MODE_FFT    = "fft"
    MODE_SMITH  = "smith"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._session = None
        self._fig = Figure(tight_layout=True)
        self._ax = self._fig.add_subplot(111)
        self._ax2 = None

        self._canvas = FigureCanvasQTAgg(self._fig)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)

        self._plots_vecs: dict[str, list[str]] = {}
        self._checked: set[tuple[str, str]] = set()
        self._derived: list[tuple[str, str]] = []   # (label, expr)

        self._live_x: list[float] = []
        self._live_y: dict[str, list[float]] = {}
        self._live_lines: dict = {}
        self._pending_redraw = False

        self._cursor_a: float | None = None
        self._cursor_b: float | None = None
        self._vline_a = None
        self._vline_b = None

        self._log_y = False
        self._mode = self.MODE_AUTO

        self._cursor_bar = QLabel()
        self._cursor_bar.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._cursor_bar.setWordWrap(True)

        self._build_ui()
        self._canvas.mpl_connect("button_press_event", self._on_canvas_click)

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setInterval(50)
        self._redraw_timer.timeout.connect(self._maybe_redraw)
        self._redraw_timer.start()

    def _build_ui(self) -> None:
        # Trace tree panel
        trace_panel = QWidget()
        tp = QVBoxLayout(trace_panel)
        tp.setContentsMargins(4, 4, 4, 4)
        tp.setSpacing(2)
        lbl = QLabel("Traces")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tp.addWidget(lbl)
        self._trace_tree = QTreeWidget()
        self._trace_tree.setHeaderHidden(True)
        self._trace_tree.itemChanged.connect(self._on_trace_toggled)
        tp.addWidget(self._trace_tree)

        # Math / derived trace button
        btn_math = QPushButton("Math…")
        btn_math.setToolTip("Add a derived trace from an expression (e.g. v(out)-v(in))")
        btn_math.clicked.connect(self._add_derived)
        tp.addWidget(btn_math)

        # Right side controls
        ctrl_bar = QHBoxLayout()
        ctrl_bar.setSpacing(4)
        ctrl_bar.setContentsMargins(0, 0, 0, 0)

        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["Auto", "Bode", "Nyquist", "FFT", "Smith"])
        self._mode_combo.setToolTip("Plot mode")
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        ctrl_bar.addWidget(QLabel("Mode:"))
        ctrl_bar.addWidget(self._mode_combo)

        self._btn_logy = QPushButton("Log Y")
        self._btn_logy.setCheckable(True)
        self._btn_logy.setToolTip("Toggle Y-axis log scale")
        self._btn_logy.toggled.connect(self._on_log_y_toggled)
        ctrl_bar.addWidget(self._btn_logy)

        self._btn_grpdelay = QPushButton("Grp Delay")
        self._btn_grpdelay.setCheckable(True)
        self._btn_grpdelay.setToolTip("Show group delay on secondary axis (AC only)")
        self._btn_grpdelay.toggled.connect(lambda _: self._replot())
        ctrl_bar.addWidget(self._btn_grpdelay)

        ctrl_bar.addStretch()

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self._toolbar)
        rl.addLayout(ctrl_bar)
        rl.addWidget(self._canvas)
        rl.addWidget(self._cursor_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(trace_panel)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([180, 600])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_session(self, session) -> None:
        self._session = session

    @Slot()
    def refresh_from_session(self) -> None:
        self._stop_live()
        if self._session is None:
            return
        self._populate_trace_tree()
        self._replot()

    @Slot(object)
    def on_init_data(self, event) -> None:
        self._stop_live()
        self._live_y = {
            name: []
            for name, is_real in zip(event.vector_names, event.real_flags)
            if name.lower() not in _SCALE_NAMES
        }
        self._live_x = []
        self._live_lines = {}
        self._fig.clear()
        self._ax = self._fig.add_subplot(111)
        self._ax2 = None
        self._vline_a = None
        self._vline_b = None
        for name in self._live_y:
            (line,) = self._ax.plot([], [], label=name)
            self._live_lines[name] = line
        self._ax.legend(fontsize="small")
        self._ax.grid(True, alpha=0.3)
        self._canvas.draw_idle()

    @Slot(object)
    def on_data_points(self, events: list) -> None:
        for event in events:
            scale_val = None
            for name, val in event.values.items():
                if name == event.scale_name or name.lower() in _SCALE_NAMES:
                    scale_val = val.real
                elif name in self._live_y:
                    self._live_y[name].append(val.real)
            if scale_val is not None:
                self._live_x.append(scale_val)
        self._pending_redraw = True

    def set_op_annotations(self, node_voltages: dict[str, float]) -> None:
        """Called after .op run; stores node voltages for display."""
        self._op_annotations = node_voltages

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_csv(self, path: str) -> None:
        traces = self._gather_traces()
        if not traces:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            header = []
            for plot_name, vec_name, x, y, _ in traces:
                if not header and x is not None:
                    header.append(f"x ({plot_name})")
                header.append(f"{plot_name}.{vec_name}")
            w.writerow(header)
            length = max(len(t[3]) for t in traces)
            for i in range(length):
                row = []
                first = True
                for _, _, x, y, _ in traces:
                    if first and x is not None:
                        row.append(x[i] if i < len(x) else "")
                        first = False
                    row.append(y.real[i] if i < len(y) else "")
                w.writerow(row)

    def export_figure(self, path: str) -> None:
        self._fig.savefig(path, bbox_inches="tight", dpi=150)

    # ------------------------------------------------------------------
    # Trace tree
    # ------------------------------------------------------------------

    def _populate_trace_tree(self) -> None:
        self._trace_tree.blockSignals(True)
        self._trace_tree.clear()
        plots = self._session.all_plots()
        current = self._session.current_plot()
        self._plots_vecs = {}

        for plot_name in reversed(plots):
            if plot_name == "const":
                continue
            vecs = self._session.all_vecs(plot_name)
            self._plots_vecs[plot_name] = vecs
            plot_item = QTreeWidgetItem([plot_name])
            plot_item.setData(0, Qt.ItemDataRole.UserRole, ("plot", plot_name))
            self._trace_tree.addTopLevelItem(plot_item)
            for v in vecs:
                if v.lower() in _SCALE_NAMES:
                    continue
                key = (plot_name, v)
                vec_item = QTreeWidgetItem([v])
                vec_item.setData(0, Qt.ItemDataRole.UserRole, ("vec", plot_name, v))
                is_checked = (plot_name == current) or (key in self._checked)
                vec_item.setCheckState(
                    0, Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked,
                )
                if is_checked:
                    self._checked.add(key)
                plot_item.addChild(vec_item)
            plot_item.setExpanded(plot_name == current)

        # Derived traces
        if self._derived:
            derived_item = QTreeWidgetItem(["[derived]"])
            self._trace_tree.addTopLevelItem(derived_item)
            for label, _ in self._derived:
                di = QTreeWidgetItem([label])
                di.setData(0, Qt.ItemDataRole.UserRole, ("derived", label))
                di.setCheckState(0, Qt.CheckState.Checked)
                derived_item.addChild(di)
            derived_item.setExpanded(True)

        self._trace_tree.blockSignals(False)

    def _on_trace_toggled(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        if data[0] == "vec":
            _, plot_name, vec_name = data
            key = (plot_name, vec_name)
            if item.checkState(0) == Qt.CheckState.Checked:
                self._checked.add(key)
            else:
                self._checked.discard(key)
        self._replot()

    # ------------------------------------------------------------------
    # Derived traces (waveform math)
    # ------------------------------------------------------------------

    def _add_derived(self) -> None:
        if self._session is None:
            return
        plot = self._session.current_plot()

        def _vec(name: str) -> np.ndarray:
            for cand in (name, f"{plot}.{name}"):
                try:
                    return self._session.get_vector(cand).data.real
                except Exception:
                    pass
            raise KeyError(name)

        expr, ok = QInputDialog.getText(
            self, "Add derived trace",
            "Expression (use vec('name') or numpy):\ne.g.  vec('v(out)') - vec('v(in)')",
        )
        if not ok or not expr.strip():
            return
        label, ok2 = QInputDialog.getText(self, "Trace label", "Label:", text=expr[:20])
        if not ok2 or not label.strip():
            return
        self._derived.append((label.strip(), expr.strip()))
        self._populate_trace_tree()
        self._replot()

    def _eval_derived(self) -> list[tuple[str, np.ndarray]]:
        if self._session is None or not self._derived:
            return []
        plot = self._session.current_plot()

        def _vec(name: str) -> np.ndarray:
            for cand in (name, f"{plot}.{name}"):
                try:
                    return self._session.get_vector(cand).data.real
                except Exception:
                    pass
            raise KeyError(name)

        results = []
        for label, expr in self._derived:
            try:
                y = eval(expr, {"__builtins__": {}, "np": np, "vec": _vec})  # noqa: S307
                results.append((label, np.asarray(y, dtype=float)))
            except Exception as exc:
                results.append((label, np.array([])))
        return results

    # ------------------------------------------------------------------
    # Mode & log-Y
    # ------------------------------------------------------------------

    def _on_mode_changed(self, idx: int) -> None:
        modes = [self.MODE_AUTO, self.MODE_BODE, self.MODE_NYQUIST, self.MODE_FFT, self.MODE_SMITH]
        self._mode = modes[idx]
        self._replot()

    def _on_log_y_toggled(self, checked: bool) -> None:
        self._log_y = checked
        self._replot()

    # ------------------------------------------------------------------
    # Plotting
    # ------------------------------------------------------------------

    def _gather_traces(self) -> list:
        traces = []
        for plot_name, vec_name in sorted(self._checked):
            try:
                vecs = self._plots_vecs.get(plot_name, [])
                scale_name = next((v for v in vecs if v.lower() in _SCALE_NAMES), None)
                x = None
                if scale_name:
                    sd = self._session.get_vector(f"{plot_name}.{scale_name}")
                    x = sd.data.real
                vd = self._session.get_vector(f"{plot_name}.{vec_name}")
                traces.append((plot_name, vec_name, x, vd.data, vd.is_complex))
            except Exception:
                pass
        return traces

    def _replot(self) -> None:
        self._fig.clear()
        self._ax = self._fig.add_subplot(111)
        self._ax2 = None
        self._vline_a = None
        self._vline_b = None

        traces = self._gather_traces()
        derived = self._eval_derived()

        if not traces and not derived:
            self._canvas.draw_idle()
            return

        mode = self._mode
        if mode == self.MODE_AUTO:
            if any(t[4] for t in traces):
                mode = self.MODE_BODE
            else:
                mode = "real"

        if mode == self.MODE_BODE:
            self._plot_bode(traces)
        elif mode == self.MODE_NYQUIST:
            self._plot_nyquist(traces)
        elif mode == self.MODE_FFT:
            self._plot_fft(traces)
        elif mode == self.MODE_SMITH:
            self._plot_smith(traces)
        else:
            self._plot_real(traces)

        # Derived traces always plotted as real on _ax
        for i, (label, y) in enumerate(derived):
            if len(y) == 0:
                continue
            color = _COLORS[(len(traces) + i) % len(_COLORS)]
            self._ax.plot(y, label=f"[{label}]", color=color, linestyle=":")

        if self._log_y and self._ax:
            try:
                self._ax.set_yscale("log")
            except Exception:
                pass

        if derived:
            self._ax.legend(fontsize="small")

        self._draw_cursors()
        self._canvas.draw_idle()

    def _plot_real(self, traces: list) -> None:
        multi_plot = len({t[0] for t in traces}) > 1
        for i, (plot_name, vec_name, x, y, _) in enumerate(traces):
            color = _COLORS[i % len(_COLORS)]
            label = f"{plot_name}.{vec_name}" if multi_plot else vec_name
            if x is not None and len(x) == len(y):
                self._ax.plot(x, y.real, label=label, color=color)
            else:
                self._ax.plot(y.real, label=label, color=color)
        if traces and traces[0][2] is not None:
            scale = next(
                (v for v in self._plots_vecs.get(traces[0][0], []) if v.lower() in _SCALE_NAMES), "",
            )
            self._ax.set_xlabel(scale)
        self._ax.legend(fontsize="small")
        self._ax.grid(True, alpha=0.3)

    def _plot_bode(self, traces: list) -> None:
        ax2 = self._ax.twinx()
        self._ax2 = ax2
        multi_plot = len({t[0] for t in traces}) > 1
        self._ax.set_xscale("log")
        show_grpdelay = self._btn_grpdelay.isChecked()

        for i, (plot_name, vec_name, x, y, is_complex) in enumerate(traces):
            color = _COLORS[i % len(_COLORS)]
            label = f"{plot_name}.{vec_name}" if multi_plot else vec_name
            if x is None or len(x) != len(y):
                continue
            if is_complex:
                mag_db = 20.0 * np.log10(np.abs(y) + 1e-300)
                phase_deg = np.unwrap(np.angle(y, deg=False)) * 180.0 / np.pi
                self._ax.plot(x, mag_db, label=f"|{label}| dB", color=color)
                if show_grpdelay:
                    phase_rad = np.unwrap(np.angle(y))
                    omega = 2 * np.pi * x
                    grp = -np.gradient(phase_rad, omega)
                    ax2.plot(x, grp, label=f"τ {label} s", color=color,
                             linestyle=":", alpha=0.7)
                    ax2.set_ylabel("Group delay (s)")
                else:
                    ax2.plot(x, phase_deg, label=f"∠{label} °", color=color,
                             linestyle="--", alpha=0.6)
                    ax2.set_ylabel("Phase (°)")
            else:
                self._ax.plot(x, y.real, label=label, color=color)

        self._ax.set_xlabel("frequency")
        self._ax.set_ylabel("Magnitude (dB)")
        self._ax.grid(True, alpha=0.3)
        lines1, labels1 = self._ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        self._ax.legend(lines1 + lines2, labels1 + labels2, fontsize="small")

    def _plot_nyquist(self, traces: list) -> None:
        self._ax.set_title("Nyquist")
        self._ax.axhline(0, color="#888", linewidth=0.5)
        self._ax.axvline(0, color="#888", linewidth=0.5)
        for i, (plot_name, vec_name, x, y, is_complex) in enumerate(traces):
            if not is_complex:
                continue
            color = _COLORS[i % len(_COLORS)]
            self._ax.plot(y.real, y.imag, label=vec_name, color=color)
            self._ax.plot(y.real, -y.imag, color=color, linestyle="--", alpha=0.4)
        self._ax.set_xlabel("Re(H)")
        self._ax.set_ylabel("Im(H)")
        self._ax.legend(fontsize="small")
        self._ax.grid(True, alpha=0.3)
        self._ax.set_aspect("equal", adjustable="datalim")

    def _plot_fft(self, traces: list) -> None:
        self._ax.set_title("FFT Spectrum")
        for i, (plot_name, vec_name, x, y, _) in enumerate(traces):
            color = _COLORS[i % len(_COLORS)]
            data = y.real
            n = len(data)
            if n < 4:
                continue
            dt = (x[-1] - x[0]) / (n - 1) if x is not None and len(x) == n else 1.0
            freqs = np.fft.rfftfreq(n, d=dt)
            spectrum = np.fft.rfft(data)
            mag_db = 20 * np.log10(np.abs(spectrum) / n + 1e-300)
            self._ax.plot(freqs[1:], mag_db[1:], label=vec_name, color=color)
        self._ax.set_xscale("log")
        self._ax.set_xlabel("Frequency (Hz)")
        self._ax.set_ylabel("Magnitude (dB)")
        self._ax.legend(fontsize="small")
        self._ax.grid(True, alpha=0.3)

    def _plot_smith(self, traces: list) -> None:
        self._ax.set_title("Smith Chart")
        self._ax.set_aspect("equal")
        # Draw smith chart grid (constant-R and constant-X circles)
        theta = np.linspace(0, 2 * np.pi, 200)
        self._ax.plot(np.cos(theta), np.sin(theta), "k-", linewidth=0.8, alpha=0.3)
        for r in (0.2, 0.5, 1.0, 2.0, 5.0):
            cx = r / (1 + r)
            cr = 1 / (1 + r)
            self._ax.plot(cx + cr * np.cos(theta), cr * np.sin(theta),
                          color="#888", linewidth=0.5, alpha=0.4)
        for x_val in (0.2, 0.5, 1.0, 2.0, 5.0):
            for sign in (1, -1):
                cy = sign / x_val
                cr = 1 / x_val
                arc_t = np.linspace(0, 2 * np.pi, 200)
                xp = 1 + cr * np.cos(arc_t)
                yp = cy + cr * np.sin(arc_t)
                mask = (xp ** 2 + yp ** 2 <= 1.01)
                self._ax.plot(xp[mask], yp[mask], color="#888", linewidth=0.5, alpha=0.4)

        self._ax.axhline(0, color="#888", linewidth=0.5)
        self._ax.set_xlim(-1.1, 1.1)
        self._ax.set_ylim(-1.1, 1.1)
        self._ax.set_xlabel("Re(Γ)")
        self._ax.set_ylabel("Im(Γ)")

        for i, (plot_name, vec_name, x, y, is_complex) in enumerate(traces):
            if not is_complex:
                continue
            color = _COLORS[i % len(_COLORS)]
            # Treat complex data as reflection coefficient directly
            self._ax.plot(y.real, y.imag, label=vec_name, color=color)
        self._ax.legend(fontsize="small")

    # ------------------------------------------------------------------
    # Cursors
    # ------------------------------------------------------------------

    def _on_canvas_click(self, event) -> None:
        if event.inaxes is None or event.xdata is None:
            return
        if self._toolbar.mode:
            return
        if event.button == 1:
            self._cursor_a = event.xdata
        elif event.button == 3:
            self._cursor_b = event.xdata
        else:
            return
        self._redraw_cursors()

    def _draw_cursors(self) -> None:
        if self._cursor_a is not None:
            self._vline_a = self._ax.axvline(
                self._cursor_a, color="red", linewidth=1, linestyle="--"
            )
        if self._cursor_b is not None:
            self._vline_b = self._ax.axvline(
                self._cursor_b, color="green", linewidth=1, linestyle="--"
            )
        self._update_cursor_bar()

    def _redraw_cursors(self) -> None:
        for attr in ("_vline_a", "_vline_b"):
            line = getattr(self, attr)
            if line is not None:
                try:
                    line.remove()
                except Exception:
                    pass
                setattr(self, attr, None)
        self._draw_cursors()
        self._canvas.draw_idle()

    def _interp_y_at(self, x: float) -> list[tuple[str, float]]:
        results = []
        for line in self._ax.get_lines():
            label = line.get_label()
            if label.startswith("_"):
                continue
            xd = line.get_xdata()
            yd = line.get_ydata()
            if len(xd) < 2:
                continue
            try:
                results.append((label, float(np.interp(x, xd, yd))))
            except Exception:
                pass
        return results

    def _update_cursor_bar(self) -> None:
        parts: list[str] = []
        ya_vals: dict[str, float] = {}
        yb_vals: dict[str, float] = {}
        for x, tag, store in (
            (self._cursor_a, "A", ya_vals),
            (self._cursor_b, "B", yb_vals),
        ):
            if x is None:
                continue
            y_vals = self._interp_y_at(x)
            store.update(dict(y_vals))
            y_str = ", ".join(f"{lbl}={_fmt(y)}" for lbl, y in y_vals)
            parts.append(f"{tag}: x={_fmt(x)}" + (f"  [{y_str}]" if y_str else ""))

        if self._cursor_a is not None and self._cursor_b is not None:
            dx = self._cursor_b - self._cursor_a
            parts.append(f"ΔX={_fmt(dx)}")
            dy_parts = []
            for lbl in ya_vals:
                if lbl in yb_vals:
                    dy_parts.append(f"{lbl}:{_fmt(yb_vals[lbl] - ya_vals[lbl])}")
            if dy_parts:
                parts.append("ΔY=" + ", ".join(dy_parts))

        self._cursor_bar.setText("    ".join(parts))

    # ------------------------------------------------------------------
    # Live streaming
    # ------------------------------------------------------------------

    def _stop_live(self) -> None:
        self._live_x = []
        self._live_y = {}
        self._live_lines = {}
        self._pending_redraw = False

    @Slot()
    def _maybe_redraw(self) -> None:
        if not self._pending_redraw or not self._live_lines:
            return
        self._pending_redraw = False
        x = np.asarray(self._live_x)
        for name, line in self._live_lines.items():
            y = np.asarray(self._live_y.get(name, []))
            n = min(len(x), len(y))
            if n > 0:
                line.set_xdata(x[:n])
                line.set_ydata(y[:n])
        if len(x) > 1:
            self._ax.relim()
            self._ax.autoscale_view()
        self._canvas.draw_idle()


# ---------------------------------------------------------------------------
# PlotLab — tabbed wrapper with Live + pinned results
# ---------------------------------------------------------------------------

class PlotLab(QWidget):
    """Phase-4+ plot widget: trace selector, modes, cursors, live streaming, compare tabs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session = None
        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.tabCloseRequested.connect(self._close_tab)

        self._live_pane = _PlotPane()
        self._tabs.addTab(self._live_pane, "Live")
        # Prevent closing the live tab
        self._tabs.tabBar().setTabButton(0, self._tabs.tabBar().ButtonPosition.RightSide, None)

        # Toolbar buttons
        btn_pin = QPushButton("Pin result")
        btn_pin.setToolTip("Snapshot the current result to a new comparison tab")
        btn_pin.clicked.connect(self._pin_result)

        btn_csv = QPushButton("Export CSV…")
        btn_csv.clicked.connect(self._export_csv)

        btn_img = QPushButton("Export image…")
        btn_img.clicked.connect(self._export_image)

        bar = QHBoxLayout()
        bar.setContentsMargins(4, 2, 4, 2)
        bar.setSpacing(6)
        bar.addWidget(btn_pin)
        bar.addWidget(btn_csv)
        bar.addWidget(btn_img)
        bar.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(bar)
        layout.addWidget(self._tabs)

    def _current_pane(self) -> _PlotPane:
        w = self._tabs.currentWidget()
        return w if isinstance(w, _PlotPane) else self._live_pane

    def _close_tab(self, idx: int) -> None:
        if idx == 0:
            return
        self._tabs.removeTab(idx)

    def _pin_result(self) -> None:
        if self._session is None:
            return
        pane = _PlotPane()
        pane.set_session(self._session)
        pane.refresh_from_session()
        plot = self._session.current_plot()
        label = f"Pin:{plot}" if plot else "Pinned"
        idx = self._tabs.addTab(pane, label)
        self._tabs.setCurrentIndex(idx)

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", str(Path.home() / "waveforms.csv"),
            "CSV (*.csv);;All Files (*)",
        )
        if path:
            try:
                self._current_pane().export_csv(path)
            except Exception as exc:
                QMessageBox.critical(self, "Export CSV", str(exc))

    def _export_image(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Image", str(Path.home() / "plot.png"),
            "PNG (*.png);;SVG (*.svg);;PDF (*.pdf);;All Files (*)",
        )
        if path:
            try:
                self._current_pane().export_figure(path)
            except Exception as exc:
                QMessageBox.critical(self, "Export Image", str(exc))

    # ------------------------------------------------------------------
    # Public API (delegated to live pane)
    # ------------------------------------------------------------------

    def set_session(self, session) -> None:
        self._session = session
        self._live_pane.set_session(session)

    @Slot()
    def refresh_from_session(self) -> None:
        self._live_pane.refresh_from_session()

    @Slot(object)
    def on_init_data(self, event) -> None:
        self._live_pane.on_init_data(event)

    @Slot(object)
    def on_data_points(self, events: list) -> None:
        self._live_pane.on_data_points(events)

    def set_op_annotations(self, node_voltages: dict[str, float]) -> None:
        self._live_pane.set_op_annotations(node_voltages)

    def add_probe(self, net_name: str) -> None:
        """Add a net from schematic probe to the live pane's checked set."""
        plot = self._session.current_plot() if self._session else None
        if not plot:
            return
        key = (plot, f"v({net_name})")
        self._live_pane._checked.add(key)
        self._live_pane._populate_trace_tree()
        self._live_pane._replot()

from __future__ import annotations

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.backends.backend_qt import NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QLabel,
    QSizePolicy,
    QSplitter,
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


class PlotLab(QWidget):
    """Phase-4 plot widget: trace selector, dual-axis AC, cursors, live streaming."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session = None

        self._fig = Figure(tight_layout=True)
        self._ax = self._fig.add_subplot(111)
        self._ax2 = None  # twinx for AC magnitude/phase

        self._canvas = FigureCanvasQTAgg(self._fig)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)

        self._plots_vecs: dict[str, list[str]] = {}
        self._checked: set[tuple[str, str]] = set()

        # Live streaming buffers (populated by on_init_data / on_data_points)
        self._live_x: list[float] = []
        self._live_y: dict[str, list[float]] = {}
        self._live_lines: dict = {}
        self._pending_redraw = False

        # Cursors
        self._cursor_a: float | None = None
        self._cursor_b: float | None = None
        self._vline_a = None
        self._vline_b = None

        self._cursor_bar = QLabel()
        self._cursor_bar.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self._build_ui()
        self._canvas.mpl_connect("button_press_event", self._on_canvas_click)

        # Rate-limit live redraws to ~20 fps
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setInterval(50)
        self._redraw_timer.timeout.connect(self._maybe_redraw)
        self._redraw_timer.start()

    def _build_ui(self) -> None:
        trace_panel = QWidget()
        tp = QVBoxLayout(trace_panel)
        tp.setContentsMargins(4, 4, 4, 4)
        lbl = QLabel("Traces")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tp.addWidget(lbl)
        self._trace_tree = QTreeWidget()
        self._trace_tree.setHeaderHidden(True)
        self._trace_tree.itemChanged.connect(self._on_trace_toggled)
        tp.addWidget(self._trace_tree)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self._toolbar)
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
        """Called after sim completes: rebuild trace tree, replot from session data."""
        self._stop_live()
        if self._session is None:
            return
        self._populate_trace_tree()
        self._replot()

    @Slot(object)
    def on_init_data(self, event) -> None:
        """Called with InitDataEvent when a simulation starts streaming."""
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
        """Called with a batch of DataPointEvents accumulated since last drain."""
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
                    0,
                    Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked,
                )
                if is_checked:
                    self._checked.add(key)
                plot_item.addChild(vec_item)

            plot_item.setExpanded(plot_name == current)

        self._trace_tree.blockSignals(False)

    def _on_trace_toggled(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data[0] != "vec":
            return
        _, plot_name, vec_name = data
        key = (plot_name, vec_name)
        if item.checkState(0) == Qt.CheckState.Checked:
            self._checked.add(key)
        else:
            self._checked.discard(key)
        self._replot()

    # ------------------------------------------------------------------
    # Plotting
    # ------------------------------------------------------------------

    def _replot(self) -> None:
        self._fig.clear()
        self._ax = self._fig.add_subplot(111)
        self._ax2 = None
        self._vline_a = None
        self._vline_b = None

        traces = self._gather_traces()
        if not traces:
            self._canvas.draw_idle()
            return

        if any(t[4] for t in traces):
            self._plot_ac(traces)
        else:
            self._plot_real(traces)

        self._draw_cursors()
        self._canvas.draw_idle()

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

    def _plot_real(self, traces: list) -> None:
        multi_plot = len({t[0] for t in traces}) > 1
        for i, (plot_name, vec_name, x, y, _) in enumerate(traces):
            color = _COLORS[i % len(_COLORS)]
            label = f"{plot_name}.{vec_name}" if multi_plot else vec_name
            if x is not None and len(x) == len(y):
                self._ax.plot(x, y.real, label=label, color=color)
            else:
                self._ax.plot(y.real, label=label, color=color)
        if traces[0][2] is not None:
            scale = next(
                (v for v in self._plots_vecs.get(traces[0][0], []) if v.lower() in _SCALE_NAMES),
                "",
            )
            self._ax.set_xlabel(scale)
        self._ax.legend(fontsize="small")
        self._ax.grid(True, alpha=0.3)

    def _plot_ac(self, traces: list) -> None:
        ax2 = self._ax.twinx()
        self._ax2 = ax2
        multi_plot = len({t[0] for t in traces}) > 1
        self._ax.set_xscale("log")

        for i, (plot_name, vec_name, x, y, is_complex) in enumerate(traces):
            color = _COLORS[i % len(_COLORS)]
            label = f"{plot_name}.{vec_name}" if multi_plot else vec_name
            if x is None or len(x) != len(y):
                continue
            if is_complex:
                mag_db = 20.0 * np.log10(np.abs(y) + 1e-300)
                phase_deg = np.angle(y, deg=True)
                self._ax.plot(x, mag_db, label=f"|{label}| dB", color=color)
                ax2.plot(x, phase_deg, label=f"∠{label} °", color=color, linestyle="--", alpha=0.6)
            else:
                self._ax.plot(x, y.real, label=label, color=color)

        self._ax.set_xlabel("frequency")
        self._ax.set_ylabel("Magnitude (dB)")
        ax2.set_ylabel("Phase (°)")
        self._ax.grid(True, alpha=0.3)

        lines1, labels1 = self._ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        self._ax.legend(lines1 + lines2, labels1 + labels2, fontsize="small")

    # ------------------------------------------------------------------
    # Cursors
    # ------------------------------------------------------------------

    def _on_canvas_click(self, event) -> None:
        if event.inaxes is None or event.xdata is None:
            return
        if self._toolbar.mode:  # pan / zoom mode active — don't steal clicks
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
        for x, tag in ((self._cursor_a, "A"), (self._cursor_b, "B")):
            if x is None:
                continue
            y_vals = self._interp_y_at(x)
            y_str = ", ".join(f"{lbl}={_fmt(y)}" for lbl, y in y_vals[:3])
            parts.append(f"{tag}: x={_fmt(x)}" + (f"  [{y_str}]" if y_str else ""))
        if self._cursor_a is not None and self._cursor_b is not None:
            parts.append(f"ΔX={_fmt(self._cursor_b - self._cursor_a)}")
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

from __future__ import annotations

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

# ngspice uses these names for the x-axis / scale vector by analysis type
_SCALE_NAMES = frozenset({"time", "frequency", "v-sweep", "i-sweep", "sweep"})


class PlotCanvas(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fig = Figure(tight_layout=True)
        self._ax = self._fig.add_subplot(111)
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas)

    def plot(self, session, plot_name: str) -> None:
        self._ax.clear()
        vecs = session.all_vecs(plot_name)
        if not vecs:
            self._canvas.draw()
            return

        # Identify the scale (x-axis) vector
        scale_name = next((v for v in vecs if v.lower() in _SCALE_NAMES), vecs[0])
        try:
            scale_data = session.get_vector(f"{plot_name}.{scale_name}")
            x = scale_data.data.real
        except Exception:
            scale_name = None
            x = None

        for v in vecs:
            if v == scale_name:
                continue
            try:
                vdata = session.get_vector(f"{plot_name}.{v}")
                y = vdata.data.real
                if x is not None and len(x) == len(y):
                    self._ax.plot(x, y, label=v)
                else:
                    self._ax.plot(y, label=v)
            except Exception:
                pass

        self._ax.legend(fontsize="small")
        self._ax.grid(True, alpha=0.3)
        if scale_name:
            self._ax.set_xlabel(scale_name)
        self._canvas.draw()

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QToolBar,
)

from .controllers.sim_controller import SimController
from .widgets.analysis_panel import AnalysisPanel
from .widgets.console import ConsoleWidget
from .widgets.netlist_editor import NetlistEditor
from .widgets.plot_canvas import PlotCanvas


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ngspice-ui")
        self.resize(1200, 800)
        self._sim_halted = False

        self._controller = SimController(parent=self)
        self._build_toolbar()
        self._build_central()
        self._build_analysis_dock()
        self._build_console_dock()
        self._connect_signals()
        self._set_status("Ready")

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main", self)
        tb.setMovable(False)
        self.addToolBar(tb)

        self._act_open = tb.addAction("Open…")
        self._act_open.setShortcut("Ctrl+O")
        tb.addSeparator()

        self._act_run = tb.addAction("Run ▶")
        self._act_run.setShortcut("F5")
        self._act_run.setToolTip("Load and run the current netlist (F5)")

        self._act_stop = tb.addAction("Stop ■")
        self._act_stop.setEnabled(False)

        self._act_resume = tb.addAction("Resume ▷")
        self._act_resume.setEnabled(False)

        tb.addSeparator()

        self._act_plot = tb.addAction("Plot")
        self._act_plot.setToolTip("Re-plot the most recent simulation result")

        tb.addSeparator()

        self._progress = QProgressBar()
        self._progress.setFixedWidth(150)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        tb.addWidget(self._progress)

    def _build_central(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._editor = NetlistEditor()
        self._plot = PlotCanvas()
        splitter.addWidget(self._editor)
        splitter.addWidget(self._plot)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        self.setCentralWidget(splitter)

    def _build_analysis_dock(self) -> None:
        self._analysis_panel = AnalysisPanel()
        dock = QDockWidget("Analysis", self)
        dock.setWidget(self._analysis_panel)
        dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.resizeDocks([dock], [220], Qt.Orientation.Horizontal)

    def _build_console_dock(self) -> None:
        self._console = ConsoleWidget()
        dock = QDockWidget("Console", self)
        dock.setWidget(self._console)
        dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        self.resizeDocks([dock], [200], Qt.Orientation.Vertical)

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        ctrl = self._controller

        self._act_open.triggered.connect(self._open_file)
        self._act_run.triggered.connect(self._run)
        self._act_stop.triggered.connect(self._stop)
        self._act_resume.triggered.connect(self._resume)
        self._act_plot.triggered.connect(self._do_plot)

        ctrl.output_line.connect(self._console.append_line)
        ctrl.progress.connect(self._progress.setValue)
        ctrl.sim_started.connect(self._on_sim_started)
        ctrl.sim_finished.connect(self._on_sim_finished)

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    @Slot()
    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Netlist",
            str(Path.home()),
            "SPICE Netlists (*.cir *.net *.sp *.spi);;All Files (*)",
        )
        if path:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            self._editor.setPlainText(text)
            self.setWindowTitle(f"ngspice-ui — {Path(path).name}")

    @Slot()
    def _run(self) -> None:
        text = self._editor.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "No Netlist", "Enter or load a netlist first.")
            return
        errors = self._analysis_panel.validate()
        if errors:
            QMessageBox.warning(self, "Analysis Setup", "\n".join(errors))
            return
        self._sim_halted = False
        analysis_line = self._analysis_panel.get_netlist_line()
        self._controller.run_with_analysis(text, analysis_line)

    @Slot()
    def _stop(self) -> None:
        self._sim_halted = True
        self._controller.halt()

    @Slot()
    def _resume(self) -> None:
        self._sim_halted = False
        self._controller.resume()

    @Slot()
    def _do_plot(self) -> None:
        plot_name = self._controller.session.current_plot()
        if not plot_name or plot_name == "const":
            self._console.append_line("-- no simulation data to plot --")
            return
        self._plot.plot(self._controller.session, plot_name)

    # ------------------------------------------------------------------
    # Simulation state
    # ------------------------------------------------------------------

    @Slot()
    def _on_sim_started(self) -> None:
        self._act_run.setEnabled(False)
        self._act_stop.setEnabled(True)
        self._act_resume.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._set_status("Running…")

    @Slot()
    def _on_sim_finished(self) -> None:
        self._act_run.setEnabled(True)
        self._act_stop.setEnabled(False)
        self._progress.setVisible(False)
        if self._sim_halted:
            self._act_resume.setEnabled(True)
            self._set_status("Halted")
            self._sim_halted = False
        else:
            self._act_resume.setEnabled(False)
            self._set_status("Done")
        self._do_plot()

    def _set_status(self, msg: str) -> None:
        self.statusBar().showMessage(msg)

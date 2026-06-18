from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QSettings, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QTabWidget,
    QToolBar,
)

from .controllers.sim_controller import SimController
from .widgets.analysis_panel import AnalysisPanel
from .widgets.console import ConsoleWidget
from .widgets.cosim_widget import CoSimWidget
from .widgets.measurements_widget import MeasurementsWidget
from .widgets.netlist_editor import NetlistEditor
from .widgets.plot_canvas import PlotLab
from .widgets.schematic_view import SchematicView
from .widgets.script_widget import ScriptWidget

_ORG = "ngspice-ui"
_APP = "ngspice-ui"
_MAX_RECENT = 10


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ngspice-ui")
        self.resize(1200, 800)
        self._sim_halted = False
        self._current_project_path: Path | None = None

        self._controller = SimController(parent=self)
        self._create_actions()
        self._build_menubar()
        self._build_toolbar()
        self._build_central()
        self._build_analysis_dock()
        self._build_console_dock()
        self._build_measurements_dock()
        self._build_script_dock()
        self._build_schematic_dock()
        self._connect_signals()
        self._restore_geometry()
        self._set_status("Ready")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _create_actions(self) -> None:
        self._act_new = QAction("New", self)
        self._act_new.setShortcut("Ctrl+N")
        self._act_new.setToolTip("New empty netlist (Ctrl+N)")

        self._act_open = QAction("Open Netlist…", self)
        self._act_open.setShortcut("Ctrl+O")

        self._act_save = QAction("Save Netlist", self)
        self._act_save.setShortcut("Ctrl+S")

        self._act_save_as = QAction("Save Netlist As…", self)
        self._act_save_as.setShortcut("Ctrl+Shift+S")

        self._act_save_project = QAction("Save Project…", self)
        self._act_save_project.setToolTip(
            "Save netlist + analysis settings + measurements as a .ngsui project"
        )
        self._act_load_project = QAction("Load Project…", self)

        self._act_run = QAction("Run ▶", self)
        self._act_run.setShortcut("F5")
        self._act_run.setToolTip("Load and run the current netlist (F5)")

        self._act_stop = QAction("Stop ■", self)
        self._act_stop.setEnabled(False)

        self._act_resume = QAction("Resume ▷", self)
        self._act_resume.setEnabled(False)

        self._act_plot = QAction("Plot", self)
        self._act_plot.setToolTip("Re-plot the most recent simulation result")

        self._act_about = QAction("About…", self)

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def _build_menubar(self) -> None:
        mb = self.menuBar()

        file_menu = mb.addMenu("File")
        file_menu.addAction(self._act_new)
        file_menu.addAction(self._act_open)

        self._recent_menu = QMenu("Open Recent", self)
        file_menu.addMenu(self._recent_menu)
        self._rebuild_recent_menu()

        file_menu.addSeparator()
        file_menu.addAction(self._act_save)
        file_menu.addAction(self._act_save_as)
        file_menu.addSeparator()
        file_menu.addAction(self._act_save_project)
        file_menu.addAction(self._act_load_project)
        file_menu.addSeparator()
        quit_act = QAction("Exit", self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        help_menu = mb.addMenu("Help")
        help_menu.addAction(self._act_about)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main", self)
        tb.setMovable(False)
        self.addToolBar(tb)

        tb.addAction(self._act_open)
        tb.addSeparator()
        tb.addAction(self._act_run)
        tb.addAction(self._act_stop)
        tb.addAction(self._act_resume)
        tb.addSeparator()
        tb.addAction(self._act_plot)
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
        self._plot = PlotLab()
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
        self._console_dock = QDockWidget("Console", self)
        self._console_dock.setWidget(self._console)
        self._console_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._console_dock)
        self.resizeDocks([self._console_dock], [200], Qt.Orientation.Vertical)

    def _build_measurements_dock(self) -> None:
        self._measurements = MeasurementsWidget()
        self._measurements_dock = QDockWidget("Measurements", self)
        self._measurements_dock.setWidget(self._measurements)
        self._measurements_dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._measurements_dock)
        self.tabifyDockWidget(self._console_dock, self._measurements_dock)

    def _build_schematic_dock(self) -> None:
        self._schematic_view = SchematicView()
        self._schematic_dock = QDockWidget("Schematic", self)
        self._schematic_dock.setWidget(self._schematic_view)
        self._schematic_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._schematic_dock)
        self.tabifyDockWidget(self._console_dock, self._schematic_dock)
        self._schematic_dock.hide()

    def _build_script_dock(self) -> None:
        self._script = ScriptWidget()
        self._cosim = CoSimWidget()

        tabs = QTabWidget()
        tabs.addTab(self._script, "Script")
        tabs.addTab(self._cosim, "Co-Sim")

        dock = QDockWidget("Script / Co-Sim", self)
        dock.setWidget(tabs)
        dock.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.resizeDocks([dock], [340], Qt.Orientation.Horizontal)

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        ctrl = self._controller

        self._plot.set_session(ctrl.session)
        self._script.set_context(ctrl.session, ctrl)
        self._cosim.set_session(ctrl.session)
        self._measurements.set_session(ctrl.session)

        self._act_new.triggered.connect(self._new_file)
        self._act_open.triggered.connect(self._open_file)
        self._act_save.triggered.connect(self._save_netlist)
        self._act_save_as.triggered.connect(self._save_netlist_as)
        self._act_save_project.triggered.connect(self._save_project)
        self._act_load_project.triggered.connect(self._load_project)
        self._act_run.triggered.connect(self._run)
        self._act_stop.triggered.connect(self._stop)
        self._act_resume.triggered.connect(self._resume)
        self._act_plot.triggered.connect(self._do_plot)
        self._act_about.triggered.connect(self._about)

        ctrl.output_line.connect(self._console.append_line)
        ctrl.progress.connect(self._progress.setValue)
        ctrl.sim_started.connect(self._on_sim_started)
        ctrl.sim_finished.connect(self._on_sim_finished)
        ctrl.plot_init.connect(self._plot.on_init_data)
        ctrl.plot_data.connect(self._plot.on_data_points)
        ctrl.errors_changed.connect(self._editor.mark_errors)

        self._editor.modification_changed.connect(self._on_editor_modified)

    # ------------------------------------------------------------------
    # File: new / open
    # ------------------------------------------------------------------

    @Slot()
    def _new_file(self) -> None:
        if not self._confirm_discard():
            return
        self._editor.set_content("", path=None)
        self._current_project_path = None
        self._update_title()

    @Slot()
    def _open_file(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open File",
            str(Path.home()),
            "All Supported (*.cir *.net *.sp *.spi *.kicad_sch *.sch);;"
            "SPICE Netlists (*.cir *.net *.sp *.spi);;"
            "KiCad Schematic (*.kicad_sch);;"
            "Eagle Schematic (*.sch);;"
            "All Files (*)",
        )
        if not path:
            return
        self._open_path(Path(path))

    def _open_path(self, p: Path) -> None:
        if p.suffix.lower() in (".kicad_sch", ".sch"):
            try:
                from ..schematic import import_schematic
                lines = import_schematic(p)
                self._editor.set_content("\n".join(lines), path=None)
                self._current_project_path = None
                self._set_status(f"Imported {len(lines)} element(s) from {p.name}")
            except Exception as exc:
                QMessageBox.critical(self, "Schematic Import Error", str(exc))
                return
            # Load graphical viewer for KiCad files
            if p.suffix.lower() == ".kicad_sch":
                self._schematic_view.load(p)
                self._schematic_dock.show()
                self._schematic_dock.raise_()
        else:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                QMessageBox.critical(self, "Open Error", str(exc))
                return
            self._editor.set_content(text, path=p)
            self._current_project_path = None
        self._add_to_recent(str(p))
        self._update_title()

    # ------------------------------------------------------------------
    # File: save
    # ------------------------------------------------------------------

    @Slot()
    def _save_netlist(self) -> None:
        path = self._editor.current_path
        if path is None:
            self._save_netlist_as()
            return
        self._write_netlist(path)

    @Slot()
    def _save_netlist_as(self) -> None:
        default = str(self._editor.current_path or Path.home() / "circuit.cir")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Netlist As",
            default,
            "SPICE Netlists (*.cir *.net *.sp *.spi);;All Files (*)",
        )
        if path:
            self._write_netlist(Path(path))

    def _write_netlist(self, p: Path) -> None:
        try:
            p.write_text(self._editor.toPlainText(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Save Error", str(exc))
            return
        self._editor.mark_saved(path=p)
        self._add_to_recent(str(p))
        self._update_title()
        self._set_status(f"Saved {p.name}")

    # ------------------------------------------------------------------
    # Project: save / load
    # ------------------------------------------------------------------

    @Slot()
    def _save_project(self) -> None:
        default = str(self._current_project_path or Path.home() / "project.ngsui")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project",
            default,
            "ngspice-ui Project (*.ngsui);;All Files (*)",
        )
        if not path:
            return
        self._write_project(Path(path))

    def _write_project(self, p: Path) -> None:
        data = {
            "version": 1,
            "netlist": self._editor.toPlainText(),
            "analysis": self._analysis_panel.get_config(),
            "measurements": self._measurements.get_config(),
        }
        try:
            p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Save Project Error", str(exc))
            return
        self._current_project_path = p
        self._editor.mark_saved(path=None)
        self._add_to_recent(str(p))
        self._update_title()
        self._set_status(f"Project saved: {p.name}")

    @Slot()
    def _load_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Project",
            str(Path.home()),
            "ngspice-ui Project (*.ngsui);;All Files (*)",
        )
        if not path:
            return
        self._load_project_from(Path(path))

    def _load_project_from(self, p: Path) -> None:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "Load Project Error", str(exc))
            return
        self._editor.set_content(data.get("netlist", ""), path=None)
        if "analysis" in data:
            self._analysis_panel.set_config(data["analysis"])
        if "measurements" in data:
            self._measurements.set_config(data["measurements"])
        self._current_project_path = p
        self._add_to_recent(str(p))
        self._update_title()
        self._set_status(f"Project loaded: {p.name}")

    # ------------------------------------------------------------------
    # Recent files
    # ------------------------------------------------------------------

    def _add_to_recent(self, path: str) -> None:
        s = QSettings(_ORG, _APP)
        recent: list[str] = s.value("recent_files", [], type=list)
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        s.setValue("recent_files", recent[:_MAX_RECENT])
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        recent: list[str] = QSettings(_ORG, _APP).value("recent_files", [], type=list)
        if not recent:
            self._recent_menu.addAction("(none)").setEnabled(False)
            return
        for p in recent:
            act = self._recent_menu.addAction(Path(p).name)
            act.setToolTip(p)
            act.triggered.connect(lambda checked=False, _p=p: self._open_recent(_p))
        self._recent_menu.addSeparator()
        clear_act = self._recent_menu.addAction("Clear Recent")
        clear_act.triggered.connect(self._clear_recent)

    def _open_recent(self, path: str) -> None:
        p = Path(path)
        if p.suffix.lower() == ".ngsui":
            if not self._confirm_discard():
                return
            self._load_project_from(p)
        else:
            if not self._confirm_discard():
                return
            self._open_path(p)

    def _clear_recent(self) -> None:
        QSettings(_ORG, _APP).remove("recent_files")
        self._rebuild_recent_menu()

    # ------------------------------------------------------------------
    # Simulation actions
    # ------------------------------------------------------------------

    @Slot()
    def _run(self) -> None:
        self._editor.clear_errors()
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
        self._plot.refresh_from_session()

    # ------------------------------------------------------------------
    # Title / modification tracking
    # ------------------------------------------------------------------

    @Slot(bool)
    def _on_editor_modified(self, modified: bool) -> None:
        self._update_title()

    def _update_title(self) -> None:
        if self._current_project_path:
            name = self._current_project_path.name
        elif self._editor.current_path:
            name = self._editor.current_path.name
        else:
            name = None
        dirty = self._editor.is_modified
        if name:
            title = f"ngspice-ui — {name}" + (" *" if dirty else "")
        else:
            title = "ngspice-ui" + (" *" if dirty else "")
        self.setWindowTitle(title)

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
        self._measurements.evaluate()

    def _set_status(self, msg: str) -> None:
        self.statusBar().showMessage(msg)

    # ------------------------------------------------------------------
    # About dialog
    # ------------------------------------------------------------------

    @Slot()
    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About ngspice-ui",
            "<b>ngspice-ui</b><br>"
            "PySide6 front-end for ngspice (libngspice · ctypes).<br><br>"
            "Phases: 0 scaffold · 1 engine · 2 MVP · 3 analyses · "
            "4 PlotLab · 5 netlist intelligence · 5b schematic import · "
            "6 scripting/co-sim · 7 project/measurements/polish<br><br>"
            "Engine: ngspice 46 · libngspice.so",
        )

    # ------------------------------------------------------------------
    # Geometry persistence
    # ------------------------------------------------------------------

    def _restore_geometry(self) -> None:
        s = QSettings(_ORG, _APP)
        geom = s.value("geometry")
        state = s.value("windowState")
        if geom:
            self.restoreGeometry(geom)
        if state:
            self.restoreState(state)

    def _save_geometry(self) -> None:
        s = QSettings(_ORG, _APP)
        s.setValue("geometry", self.saveGeometry())
        s.setValue("windowState", self.saveState())

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self._editor.is_modified:
            ret = QMessageBox.question(
                self,
                "Unsaved Changes",
                "The netlist has unsaved changes. Save before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if ret == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if ret == QMessageBox.StandardButton.Save:
                self._save_netlist()
                if self._editor.is_modified:
                    event.ignore()
                    return
        self._save_geometry()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _confirm_discard(self) -> bool:
        """Return True if it is safe to discard the current netlist."""
        if not self._editor.is_modified:
            return True
        ret = QMessageBox.question(
            self,
            "Unsaved Changes",
            "The netlist has unsaved changes. Discard them?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        return ret == QMessageBox.StandardButton.Discard

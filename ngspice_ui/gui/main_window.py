from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, Slot
from PySide6.QtGui import QAction, QColor, QDragEnterEvent, QDropEvent, QPalette
from PySide6.QtWidgets import (
    QApplication,
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
from .widgets.eagle_view import EagleView
from .widgets.help_dialog import HelpDialog
from .widgets.linter import lint
from .widgets.measurements_widget import MeasurementsWidget
from .widgets.model_browser_widget import ModelBrowserWidget
from .widgets.monte_carlo_widget import MonteCarloWidget
from .widgets.netlist_editor import NetlistEditor
from .widgets.notes_widget import NotesWidget
from .widgets.param_sweep_widget import ParamSweepWidget
from .widgets.plot_canvas import PlotLab
from .widgets.schematic_view import SchematicView
from .widgets.script_widget import ScriptWidget
from .widgets.snippet_widget import SnippetWidget

_ORG = "ngspice-ui"
_APP = "ngspice-ui"
_MAX_RECENT = 10


def _make_dark_palette() -> QPalette:
    p = QPalette()
    dark = QColor(30, 30, 35)
    mid = QColor(50, 50, 55)
    text = QColor(220, 220, 220)
    highlight = QColor(0, 120, 215)
    p.setColor(QPalette.ColorRole.Window, dark)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, QColor(20, 20, 25))
    p.setColor(QPalette.ColorRole.AlternateBase, mid)
    p.setColor(QPalette.ColorRole.ToolTipBase, dark)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, mid)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
    p.setColor(QPalette.ColorRole.Highlight, highlight)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.Mid, QColor(80, 80, 85))
    return p


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ngspice-ui")
        self.resize(1400, 900)
        self._sim_halted = False
        self._current_project_path: Path | None = None
        self._sim_start_time: float = 0.0

        self._controller = SimController(parent=self)
        self._create_actions()
        self._build_menubar()
        self._build_toolbar()
        self._build_central()
        self._build_analysis_dock()
        self._build_console_dock()
        self._build_measurements_dock()
        self._build_notes_dock()
        self._build_script_dock()
        self._build_schematic_docks()
        self._build_advanced_docks()
        self._connect_signals()
        self._restore_geometry()
        self._set_status("Ready")
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _create_actions(self) -> None:
        self._act_new = QAction("New", self)
        self._act_new.setShortcut("Ctrl+N")

        self._act_open = QAction("Open Netlist…", self)
        self._act_open.setShortcut("Ctrl+O")

        self._act_save = QAction("Save Netlist", self)
        self._act_save.setShortcut("Ctrl+S")

        self._act_save_as = QAction("Save Netlist As…", self)
        self._act_save_as.setShortcut("Ctrl+Shift+S")

        self._act_save_project = QAction("Save Project…", self)
        self._act_load_project = QAction("Load Project…", self)

        self._act_run = QAction("Run ▶", self)
        self._act_run.setShortcut("F5")

        self._act_stop = QAction("Stop ■", self)
        self._act_stop.setEnabled(False)

        self._act_resume = QAction("Resume ▷", self)
        self._act_resume.setEnabled(False)

        self._act_plot = QAction("Plot", self)

        self._act_help = QAction("User Guide…", self)
        self._act_help.setShortcut("F1")

        self._act_about = QAction("About…", self)

        self._act_lint = QAction("Lint Netlist", self)
        self._act_lint.setShortcut("F4")
        self._act_lint.setToolTip("Run static checks on the current netlist (F4)")

        self._act_find = QAction("Find / Replace…", self)
        self._act_find.setShortcut("Ctrl+H")

        self._act_theme_dark = QAction("Dark Theme", self)
        self._act_theme_dark.setCheckable(True)
        self._act_theme_light = QAction("Light Theme", self)
        self._act_theme_light.setCheckable(True)

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def _build_menubar(self) -> None:
        mb = self.menuBar()

        # File
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

        # Edit
        edit_menu = mb.addMenu("Edit")
        edit_menu.addAction(self._act_find)
        edit_menu.addAction(self._act_lint)

        # View
        self._view_menu = mb.addMenu("View")
        theme_menu = self._view_menu.addMenu("Theme")
        theme_menu.addAction(self._act_theme_dark)
        theme_menu.addAction(self._act_theme_light)
        self._view_menu.addSeparator()
        # Dock toggle actions added after docks are created

        # Help
        help_menu = mb.addMenu("Help")
        help_menu.addAction(self._act_help)
        help_menu.addSeparator()
        help_menu.addAction(self._act_about)

    def _add_dock_toggle(self, dock: QDockWidget, shortcut: str | None = None) -> None:
        act = dock.toggleViewAction()
        if shortcut:
            act.setShortcut(shortcut)
        self._view_menu.addAction(act)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main", self)
        tb.setObjectName("toolbar_main")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addAction(self._act_open)
        tb.addSeparator()
        tb.addAction(self._act_run)
        tb.addAction(self._act_stop)
        tb.addAction(self._act_resume)
        tb.addSeparator()
        tb.addAction(self._act_plot)
        tb.addAction(self._act_lint)
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
        dock.setObjectName("dock_analysis")
        dock.setWidget(self._analysis_panel)
        dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.resizeDocks([dock], [240], Qt.Orientation.Horizontal)
        self._analysis_dock = dock

    def _build_console_dock(self) -> None:
        self._console = ConsoleWidget()
        self._console_dock = QDockWidget("Console", self)
        self._console_dock.setObjectName("dock_console")
        self._console_dock.setWidget(self._console)
        self._console_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._console_dock)
        self.resizeDocks([self._console_dock], [180], Qt.Orientation.Vertical)

    def _build_measurements_dock(self) -> None:
        self._measurements = MeasurementsWidget()
        self._measurements_dock = QDockWidget("Measurements", self)
        self._measurements_dock.setObjectName("dock_measurements")
        self._measurements_dock.setWidget(self._measurements)
        self._measurements_dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._measurements_dock)
        self.tabifyDockWidget(self._console_dock, self._measurements_dock)

    def _build_notes_dock(self) -> None:
        self._notes = NotesWidget()
        self._notes_dock = QDockWidget("Notes", self)
        self._notes_dock.setObjectName("dock_notes")
        self._notes_dock.setWidget(self._notes)
        self._notes_dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._notes_dock)
        self.tabifyDockWidget(self._console_dock, self._notes_dock)

    def _build_script_dock(self) -> None:
        self._script = ScriptWidget()
        self._cosim = CoSimWidget()
        tabs = QTabWidget()
        tabs.addTab(self._script, "Script")
        tabs.addTab(self._cosim, "Co-Sim")
        dock = QDockWidget("Script / Co-Sim", self)
        dock.setObjectName("dock_script")
        dock.setWidget(tabs)
        dock.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.resizeDocks([dock], [340], Qt.Orientation.Horizontal)
        self._script_dock = dock

    def _build_schematic_docks(self) -> None:
        # KiCad viewer
        self._schematic_view = SchematicView()
        self._schematic_dock = QDockWidget("Schematic (KiCad)", self)
        self._schematic_dock.setObjectName("dock_schematic_kicad")
        self._schematic_dock.setWidget(self._schematic_view)
        self._schematic_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._schematic_dock)
        self.tabifyDockWidget(self._console_dock, self._schematic_dock)
        self._schematic_dock.hide()

        # Eagle viewer
        self._eagle_view = EagleView()
        self._eagle_dock = QDockWidget("Schematic (Eagle)", self)
        self._eagle_dock.setObjectName("dock_schematic_eagle")
        self._eagle_dock.setWidget(self._eagle_view)
        self._eagle_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._eagle_dock)
        self.tabifyDockWidget(self._console_dock, self._eagle_dock)
        self._eagle_dock.hide()

    def _build_advanced_docks(self) -> None:
        # Snippet library
        self._snippets = SnippetWidget()
        self._snippets.insert_requested.connect(self._insert_snippet)
        snip_dock = QDockWidget("Snippets", self)
        snip_dock.setObjectName("dock_snippets")
        snip_dock.setWidget(self._snippets)
        snip_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, snip_dock)
        self.tabifyDockWidget(self._analysis_dock, snip_dock)
        self._snip_dock = snip_dock

        # Model browser
        self._model_browser = ModelBrowserWidget()
        self._model_browser.insert_requested.connect(self._insert_snippet)
        mb_dock = QDockWidget("Model Library", self)
        mb_dock.setObjectName("dock_model_library")
        mb_dock.setWidget(self._model_browser)
        mb_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, mb_dock)
        self.tabifyDockWidget(self._analysis_dock, mb_dock)
        self._mb_dock = mb_dock

        # Parametric sweep
        self._param_sweep = ParamSweepWidget()
        self._param_sweep.run_sweep.connect(self._run_param_sweep)
        ps_dock = QDockWidget("Param Sweep", self)
        ps_dock.setObjectName("dock_param_sweep")
        ps_dock.setWidget(self._param_sweep)
        ps_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, ps_dock)
        self.tabifyDockWidget(self._analysis_dock, ps_dock)
        self._ps_dock = ps_dock

        # Monte Carlo
        self._monte_carlo = MonteCarloWidget()
        self._monte_carlo.set_netlist_getter(lambda: self._editor.toPlainText().strip())
        self._monte_carlo.run_mc.connect(self._run_monte_carlo)
        mc_dock = QDockWidget("Monte Carlo", self)
        mc_dock.setObjectName("dock_monte_carlo")
        mc_dock.setWidget(self._monte_carlo)
        mc_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, mc_dock)
        self.tabifyDockWidget(self._analysis_dock, mc_dock)
        self._mc_dock = mc_dock

        # Raise Analysis tab by default
        self._analysis_dock.raise_()

        # View menu dock toggles
        self._view_menu.addAction(self._analysis_dock.toggleViewAction())
        self._view_menu.addAction(self._console_dock.toggleViewAction())
        self._view_menu.addAction(self._measurements_dock.toggleViewAction())
        self._view_menu.addAction(self._notes_dock.toggleViewAction())
        self._view_menu.addAction(self._script_dock.toggleViewAction())
        self._view_menu.addAction(self._schematic_dock.toggleViewAction())
        self._view_menu.addAction(self._eagle_dock.toggleViewAction())
        self._view_menu.addAction(self._snip_dock.toggleViewAction())
        self._view_menu.addAction(self._mb_dock.toggleViewAction())
        self._view_menu.addAction(self._ps_dock.toggleViewAction())
        self._view_menu.addAction(self._mc_dock.toggleViewAction())

        # Shortcuts Alt+1…Alt+5 for core docks
        for shortcut, dock in (
            ("Alt+1", self._analysis_dock),
            ("Alt+2", self._console_dock),
            ("Alt+3", self._measurements_dock),
            ("Alt+4", self._script_dock),
            ("Alt+5", self._schematic_dock),
        ):
            act = dock.toggleViewAction()
            act.setShortcut(shortcut)

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
        self._act_help.triggered.connect(self._show_help)
        self._act_about.triggered.connect(self._about)
        self._act_lint.triggered.connect(self._lint_netlist)
        self._act_find.triggered.connect(self._editor.show_find_replace)
        self._act_theme_dark.triggered.connect(lambda: self._apply_theme("dark"))
        self._act_theme_light.triggered.connect(lambda: self._apply_theme("light"))

        ctrl.output_line.connect(self._console.append_line)
        ctrl.progress.connect(self._progress.setValue)
        ctrl.sim_started.connect(self._on_sim_started)
        ctrl.sim_finished.connect(self._on_sim_finished)
        ctrl.plot_init.connect(self._plot.on_init_data)
        ctrl.plot_data.connect(self._plot.on_data_points)
        ctrl.errors_changed.connect(self._editor.mark_errors)

        self._editor.modification_changed.connect(self._on_editor_modified)

        # Project dirty tracking — changes to any project widget mark the project modified
        self._project_dirty = False
        self._analysis_panel.analysis_changed.connect(self._mark_project_dirty)
        self._measurements.changed.connect(self._mark_project_dirty)
        self._notes.changed.connect(self._mark_project_dirty)
        self._script.changed.connect(self._mark_project_dirty)
        self._cosim.changed.connect(self._mark_project_dirty)

        # Schematic probe → PlotLab
        self._schematic_view.net_probed.connect(self._on_net_probed)
        self._eagle_view.net_probed.connect(self._on_net_probed)

        # Restore saved theme
        theme = QSettings(_ORG, _APP).value("theme", "light")
        if theme == "dark":
            self._apply_theme("dark", save=False)

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
            self, "Open File", str(Path.home()),
            "All Supported (*.cir *.net *.sp *.spi *.kicad_sch *.sch *.ngsui);;"
            "SPICE Netlists (*.cir *.net *.sp *.spi);;"
            "KiCad Schematic (*.kicad_sch);;"
            "Eagle Schematic (*.sch);;"
            "ngspice-ui Project (*.ngsui);;"
            "All Files (*)",
        )
        if path:
            p = Path(path)
            if p.suffix.lower() == ".ngsui":
                self._load_project_from(p)
            else:
                self._open_path(p)

    def _open_path(self, p: Path) -> None:
        suffix = p.suffix.lower()
        if suffix == ".kicad_sch":
            try:
                from ..schematic import import_schematic
                lines = import_schematic(p)
                self._editor.set_content("\n".join(lines), path=None)
                self._current_project_path = None
                self._project_dirty = True   # imported netlist has never been saved
                self._update_title()
                self._set_status(f"Imported {len(lines)} element(s) from {p.name}")
            except Exception as exc:
                QMessageBox.critical(self, "Schematic Import Error", str(exc))
                return
            self._schematic_view.load(p)
            self._schematic_dock.show()
            self._schematic_dock.raise_()
        elif suffix == ".sch":
            try:
                from ..schematic import import_schematic
                lines = import_schematic(p)
                self._editor.set_content("\n".join(lines), path=None)
                self._current_project_path = None
                self._project_dirty = True   # imported netlist has never been saved
                self._update_title()
                self._set_status(f"Imported {len(lines)} element(s) from {p.name}")
            except Exception as exc:
                QMessageBox.critical(self, "Schematic Import Error", str(exc))
                return
            self._eagle_view.load(p)
            self._eagle_dock.show()
            self._eagle_dock.raise_()
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
            self, "Save Netlist As", default,
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
            self, "Save Project", default,
            "ngspice-ui Project (*.ngsui);;All Files (*)",
        )
        if path:
            self._write_project(Path(path))

    def _write_project(self, p: Path) -> None:
        data = {
            "version": 2,
            "netlist": self._editor.toPlainText(),
            "analysis": self._analysis_panel.get_config(),
            "measurements": self._measurements.get_config(),
            "notes": self._notes.get_config(),
            "script": self._script.get_config(),
            "cosim": self._cosim.get_config(),
        }
        try:
            p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Save Project Error", str(exc))
            return
        self._current_project_path = p
        self._editor.mark_saved(path=None)
        self._project_dirty = False
        self._add_to_recent(str(p))
        self._update_title()
        self._set_status(f"Project saved: {p.name}")

    @Slot()
    def _load_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Project", str(Path.home()),
            "ngspice-ui Project (*.ngsui);;All Files (*)",
        )
        if path:
            self._load_project_from(Path(path))

    def _load_project_from(self, p: Path) -> None:
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "Load Project Error", str(exc))
            return
        if not isinstance(raw, dict):
            QMessageBox.critical(self, "Load Project Error",
                                 "Invalid project file: root must be a JSON object.")
            return
        data: dict = raw
        self._editor.set_content(data.get("netlist", ""), path=None)
        if isinstance(data.get("analysis"), dict):
            self._analysis_panel.set_config(data["analysis"])
        if isinstance(data.get("measurements"), list):
            self._measurements.set_config(data["measurements"])
        if isinstance(data.get("notes"), str):
            self._notes.set_config(data["notes"])
        if isinstance(data.get("script"), dict):
            self._script.set_config(data["script"])
        if isinstance(data.get("cosim"), dict):
            self._cosim.set_config(data["cosim"])
        self._current_project_path = p
        self._project_dirty = False
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
        for path in recent:
            act = self._recent_menu.addAction(Path(path).name)
            act.setToolTip(path)
            act.triggered.connect(lambda checked=False, _p=path: self._open_recent(_p))
        self._recent_menu.addSeparator()
        clear_act = self._recent_menu.addAction("Clear Recent")
        clear_act.triggered.connect(self._clear_recent)

    def _open_recent(self, path: str) -> None:
        p = Path(path)
        if p.suffix.lower() == ".ngsui":
            if self._confirm_discard():
                self._load_project_from(p)
        else:
            if self._confirm_discard():
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
        temp_lines = self._analysis_panel.get_temperature_lines()
        self._monte_carlo.set_netlist(text)
        self._controller.run_with_analysis(text, analysis_line, extra_lines=temp_lines)

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
    # Lint
    # ------------------------------------------------------------------

    @Slot()
    def _lint_netlist(self) -> None:
        text = self._editor.toPlainText()
        path = self._editor.current_path
        issues = lint(text, path)
        if not issues:
            self._set_status("Lint: no issues found")
            self._editor.mark_errors([])
        else:
            self._editor.mark_errors(issues)
            msgs = [f"Line {ln}: {msg}" for ln, msg in issues]
            self._set_status(f"Lint: {len(issues)} issue(s)")
            self._console.append_line("=== Lint results ===")
            for m in msgs:
                self._console.append_line(m)
            self._console_dock.raise_()

    # ------------------------------------------------------------------
    # Snippet insert
    # ------------------------------------------------------------------

    def _insert_snippet(self, text: str) -> None:
        cursor = self._editor.textCursor()
        cursor.insertText(text)
        self._editor.document().setModified(True)

    # ------------------------------------------------------------------
    # Schematic probe → PlotLab
    # ------------------------------------------------------------------

    @Slot(str)
    def _on_net_probed(self, net_name: str) -> None:
        self._plot.add_probe(net_name)
        self._set_status(f"Probing net: {net_name}")

    # ------------------------------------------------------------------
    # Parametric sweep
    # ------------------------------------------------------------------

    @Slot(str, list)
    def _run_param_sweep(self, param_name: str, values: list) -> None:
        text = self._editor.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Param Sweep", "No netlist loaded.")
            return
        step_lines = self._param_sweep.get_step_lines()
        prefix = "\n".join(step_lines)
        combined = prefix + "\n" + text if prefix else text
        analysis_line = self._analysis_panel.get_netlist_line()
        self._controller.run_with_analysis(combined, analysis_line)

    # ------------------------------------------------------------------
    # Monte Carlo (sequential bg_run via queued timer)
    # ------------------------------------------------------------------

    @Slot(list)
    def _run_monte_carlo(self, netlists: list) -> None:
        if not netlists:
            return
        self._mc_queue = list(netlists)
        self._mc_total = len(netlists)
        self._mc_index = 0
        analysis_line = self._analysis_panel.get_netlist_line()
        self._mc_analysis_line = analysis_line
        self._console.append_line(f"Monte Carlo: {len(netlists)} runs queued")
        self._controller.sim_finished.connect(self._mc_next_run)
        self._mc_run_next()

    def _mc_run_next(self) -> None:
        if not hasattr(self, "_mc_queue") or not self._mc_queue:
            return
        text = self._mc_queue.pop(0)
        self._mc_index += 1
        self._console.append_line(f"  MC run {self._mc_index}/{self._mc_total}")
        self._controller.run_with_analysis(text, self._mc_analysis_line)

    @Slot()
    def _mc_next_run(self) -> None:
        if not hasattr(self, "_mc_queue"):
            return
        if self._mc_queue:
            self._mc_run_next()
        else:
            self._controller.sim_finished.disconnect(self._mc_next_run)
            self._console.append_line(f"Monte Carlo complete ({self._mc_total} runs)")
            self._mc_queue = []

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _apply_theme(self, theme: str, save: bool = True) -> None:
        app = QApplication.instance()
        if theme == "dark":
            app.setPalette(_make_dark_palette())
            app.setStyle("Fusion")
            self._act_theme_dark.setChecked(True)
            self._act_theme_light.setChecked(False)
        else:
            app.setPalette(app.style().standardPalette())
            self._act_theme_dark.setChecked(False)
            self._act_theme_light.setChecked(True)
        if save:
            QSettings(_ORG, _APP).setValue("theme", theme)

    # ------------------------------------------------------------------
    # Drag and drop
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            suffix = p.suffix.lower()
            if suffix == ".ngsui":
                if self._confirm_discard():
                    self._load_project_from(p)
            elif suffix in (".cir", ".net", ".sp", ".spi", ".kicad_sch", ".sch"):
                if self._confirm_discard():
                    self._open_path(p)
            break

    # ------------------------------------------------------------------
    # Title / modification
    # ------------------------------------------------------------------

    @Slot(bool)
    def _on_editor_modified(self, modified: bool) -> None:
        self._update_title()

    @Slot()
    def _mark_project_dirty(self) -> None:
        self._project_dirty = True
        self._update_title()

    def _update_title(self) -> None:
        if self._current_project_path:
            name = self._current_project_path.name
        elif self._editor.current_path:
            name = self._editor.current_path.name
        else:
            name = None
        dirty = self._editor.is_modified or self._project_dirty
        title = "ngspice-ui"
        if name:
            title += f" — {name}"
        if dirty:
            title += " *"
        self.setWindowTitle(title)

    # ------------------------------------------------------------------
    # Simulation state + elapsed time
    # ------------------------------------------------------------------

    @Slot()
    def _on_sim_started(self) -> None:
        self._sim_start_time = time.monotonic()
        self._act_run.setEnabled(False)
        self._act_stop.setEnabled(True)
        self._act_resume.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._set_status("Running…")

        # Live elapsed-time update
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(200)
        self._elapsed_timer.timeout.connect(self._update_elapsed)
        self._elapsed_timer.start()

    @Slot()
    def _update_elapsed(self) -> None:
        elapsed = time.monotonic() - self._sim_start_time
        self._set_status(f"Running… {elapsed:.1f} s")

    @Slot()
    def _on_sim_finished(self) -> None:
        elapsed = time.monotonic() - self._sim_start_time
        if hasattr(self, "_elapsed_timer"):
            self._elapsed_timer.stop()
            self._elapsed_timer = None

        self._act_run.setEnabled(True)
        self._act_stop.setEnabled(False)
        self._progress.setVisible(False)
        if self._sim_halted:
            self._act_resume.setEnabled(True)
            self._set_status(f"Halted  ({elapsed:.2f} s)")
            self._sim_halted = False
        else:
            self._act_resume.setEnabled(False)
            self._set_status(f"Done  ({elapsed:.2f} s)")

        self._do_plot()
        self._measurements.evaluate()

        # OP annotation
        try:
            session = self._controller.session
            plot = session.current_plot()
            if plot:
                vecs = session.all_vecs(plot)
                voltages: dict[str, float] = {}
                for v in vecs:
                    try:
                        vd = session.get_vector(f"{plot}.{v}")
                        if len(vd.data) == 1:
                            voltages[v] = float(vd.data.real[0])
                    except Exception:
                        pass
                if voltages:
                    self._schematic_view.set_op_voltages(voltages)
        except Exception:
            pass

    def _set_status(self, msg: str) -> None:
        self.statusBar().showMessage(msg)

    # ------------------------------------------------------------------
    # Help / About
    # ------------------------------------------------------------------

    @Slot()
    def _show_help(self) -> None:
        dlg = HelpDialog(self)
        dlg.exec()

    @Slot()
    def _about(self) -> None:
        QMessageBox.about(
            self, "About ngspice-ui",
            "<b>ngspice-ui</b><br>"
            "PySide6 front-end for ngspice (libngspice · ctypes).<br><br>"
            "Phases complete: 0–8 + full feature set.<br><br>"
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
        if self._editor.is_modified or self._project_dirty:
            ret = QMessageBox.question(
                self, "Unsaved Changes",
                "The project has unsaved changes. Save before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if ret == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if ret == QMessageBox.StandardButton.Save:
                self._save_netlist()
                if self._editor.is_modified or self._project_dirty:
                    event.ignore()
                    return
        self._save_geometry()
        super().closeEvent(event)

    def _confirm_discard(self) -> bool:
        if not self._editor.is_modified and not self._project_dirty:
            return True
        ret = QMessageBox.question(
            self, "Unsaved Changes",
            "The project has unsaved changes. Discard them?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        return ret == QMessageBox.StandardButton.Discard

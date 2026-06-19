"""Declarative UI construction for :class:`MainWindow`.

Split out as a mixin so ``main_window.py`` stays focused on behavior and
coordination. These methods only build widgets, menus, the toolbar, and docks,
assigning them onto ``self``; the runtime logic (signal handlers, file/project
I/O, simulation actions) lives in :class:`MainWindow`. The mixin assumes it is
combined with a ``QMainWindow`` and that ``MainWindow`` provides the slot
methods referenced here (``_insert_snippet``, ``_run_param_sweep``,
``_run_monte_carlo``, ``_rebuild_recent_menu``, ``close``).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDockWidget,
    QMenu,
    QProgressBar,
    QSplitter,
    QTabWidget,
    QToolBar,
)

from .widgets.analysis_panel import AnalysisPanel
from .widgets.console import ConsoleWidget
from .widgets.cosim_widget import CoSimWidget
from .widgets.eagle_view import EagleView
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


class MainWindowUI:
    """Mixin holding MainWindow's widget/menu/dock construction."""

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

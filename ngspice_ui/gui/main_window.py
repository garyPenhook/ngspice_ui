from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer, Slot
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMessageBox,
)

from ..models.project import ProjectDocument, ProjectError
from ..models.result import SimulationResult
from .controllers.sim_controller import SimController
from .main_window_ui import MainWindowUI
from .widgets.help_dialog import HelpDialog
from .widgets.linter import lint

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


class MainWindow(MainWindowUI, QMainWindow):
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
    # Signal wiring
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        ctrl = self._controller
        # Plot + measurements read an immutable SimulationResult snapshot built
        # after each run; script + co-sim need the live session for interaction.
        self._script.set_context(ctrl.session, ctrl)
        self._cosim.set_session(ctrl.session)

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
        ctrl.mc_progress.connect(self._on_mc_progress)
        ctrl.mc_finished.connect(self._on_mc_finished)

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
        doc = ProjectDocument(
            netlist=self._editor.toPlainText(),
            analysis=self._analysis_panel.get_config(),
            measurements=self._measurements.get_config(),
            notes=self._notes.get_config(),
            script=self._script.get_config(),
            cosim=self._cosim.get_config(),
        )
        try:
            p.write_text(doc.dumps(), encoding="utf-8")
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
            doc = ProjectDocument.loads(p.read_text(encoding="utf-8"))
        except OSError as exc:
            QMessageBox.critical(self, "Load Project Error", str(exc))
            return
        except ProjectError as exc:
            QMessageBox.critical(self, "Load Project Error", str(exc))
            return
        self._editor.set_content(doc.netlist, path=None)
        self._analysis_panel.set_config(doc.analysis)
        self._measurements.set_config(doc.measurements)
        self._notes.set_config(doc.notes)
        self._script.set_config(doc.script)
        self._cosim.set_config(doc.cosim)
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

    def _snapshot_result(self) -> SimulationResult:
        """Snapshot the live session and hand it to the read-only consumers.

        Built fresh on demand so the manual Plot action reflects the current
        session even when a run was triggered outside the controller (e.g. a
        foreground command from the Script console, which emits no
        sim_finished).
        """
        result = SimulationResult.from_session(self._controller.session)
        self._plot.set_result(result)
        self._measurements.set_result(result)
        return result

    @Slot()
    def _do_plot(self) -> None:
        plot_name = self._controller.session.current_plot()
        if not plot_name or plot_name == "const":
            self._console.append_line("-- no simulation data to plot --")
            return
        self._snapshot_result()
        self._plot.refresh()

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
        analysis_line = self._analysis_panel.get_netlist_line()
        self._controller.run_param_sweep(text, step_lines, analysis_line)

    # ------------------------------------------------------------------
    # Monte Carlo — sequencing lives in the coordinator; we only echo status
    # ------------------------------------------------------------------

    @Slot(list)
    def _run_monte_carlo(self, netlists: list) -> None:
        if not netlists:
            return
        analysis_line = self._analysis_panel.get_netlist_line()
        self._console.append_line(f"Monte Carlo: {len(netlists)} runs queued")
        self._controller.run_monte_carlo(netlists, analysis_line)

    @Slot(int, int)
    def _on_mc_progress(self, index: int, total: int) -> None:
        self._console.append_line(f"  MC run {index}/{total}")

    @Slot(int)
    def _on_mc_finished(self, total: int) -> None:
        self._console.append_line(f"Monte Carlo complete ({total} runs)")

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

        # Build an immutable snapshot of this run's vectors and hand it to the
        # read-only consumers (plotting, measurements, OP annotation).
        result = self._snapshot_result()

        plot_name = self._controller.session.current_plot()
        if not plot_name or plot_name == "const":
            self._console.append_line("-- no simulation data to plot --")
        else:
            self._plot.refresh()
        self._measurements.evaluate()

        # OP annotation — scalar (single-point) vectors are node voltages.
        try:
            plot = result.current_plot()
            if plot:
                voltages: dict[str, float] = {}
                for v in result.all_vecs(plot):
                    try:
                        vd = result.get_vector(f"{plot}.{v}")
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

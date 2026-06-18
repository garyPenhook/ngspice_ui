"""Entry point for ``python -m ngspice_ui`` and PyInstaller frozen builds."""
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from ngspice_ui.gui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("ngspice-ui")
    app.setOrganizationName("ngspice-ui")

    try:
        win = MainWindow()
    except Exception as exc:
        QMessageBox.critical(None, "Startup Error", str(exc))
        sys.exit(1)

    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

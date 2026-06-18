# PyInstaller spec for ngspice-ui
# Build: pyinstaller ngspice_ui_dist.spec
#
# Prerequisites:
#   pip install pyinstaller
#   apt install ngspice libngspice0-dev   (or equivalent for your OS)
#
# The spec auto-discovers libngspice.so; set NGSPICE_LIB env var if it's
# in a non-standard location before running pyinstaller.

import os
import sys
from pathlib import Path

block_cipher = None

# Try to locate libngspice
_ngspice_lib = os.environ.get("NGSPICE_LIB", "")
if not _ngspice_lib:
    for candidate in (
        "/usr/lib/libngspice.so",
        "/usr/lib/libngspice.so.0",
        "/usr/local/lib/libngspice.so",
        "/usr/lib/x86_64-linux-gnu/libngspice.so",
        "/usr/lib/x86_64-linux-gnu/libngspice.so.0",
    ):
        if Path(candidate).exists():
            _ngspice_lib = candidate
            break

_binaries = [(_ngspice_lib, ".")] if _ngspice_lib else []

a = Analysis(
    ["ngspice_ui/gui/app.py"],
    pathex=["."],
    binaries=_binaries,
    datas=[
        ("examples", "examples"),
        ("ngspice_ui/gui/widgets/snippet_widget.py", "."),  # snippets are inline
    ],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "matplotlib.backends.backend_qtagg",
        "matplotlib.backends.backend_qt",
        "numpy",
        "ngspice_ui.engine",
        "ngspice_ui.gui",
        "ngspice_ui.gui.widgets",
        "ngspice_ui.schematic",
        "ngspice_ui.models",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ngspice-ui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ngspice-ui",
)

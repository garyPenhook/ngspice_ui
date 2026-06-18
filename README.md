# ngspice-ui

A PySide6 desktop front-end for [ngspice](https://ngspice.sourceforge.io/), connecting directly to `libngspice` via ctypes for real-time simulation feedback.

![phases](https://img.shields.io/badge/phases-0–7%20complete-brightgreen)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

---

## Features

- **All ngspice analyses** — Transient, AC, DC Sweep, Operating Point, Noise, Transfer Function, Sensitivity, Pole-Zero, Distortion
- **PlotLab** — trace selector, dual Y-axis, cursors, live streaming during simulation
- **Netlist editor** — syntax highlighting, dot-command autocomplete, error markers, value quick-edit (Ctrl+double-click)
- **Schematic import** — KiCad 6/7 `.kicad_sch` and Eagle `.sch` → SPICE netlist; reads `Sim.Library`, `Sim.SpiceModel`, `Sim.Pins` properties
- **Measurements dock** — named expression table evaluated against simulation data (`np.max(vec("v(out)"))`, RMS, peak-to-peak, etc.)
- **Script / Co-Sim** — Python REPL with live session access; external V/I source callbacks via `ngSpice_Init_Sync`
- **Project files** — save/load netlist + analysis config + measurements as `.ngsui` JSON
- **Persistent layout** — window geometry and dock positions restored on relaunch

---

## Requirements

### System library (install before pip)

ngspice-ui links to `libngspice` at runtime. This is a **system package**, not a Python package.

| OS | Command |
|---|---|
| Debian / Ubuntu / Kali | `sudo apt install ngspice libngspice0-dev` |
| Fedora / RHEL | `sudo dnf install ngspice` |
| Arch | `sudo pacman -S ngspice` |
| macOS (Homebrew) | `brew install ngspice` |
| Windows | Download the ngspice installer from [ngspice.sourceforge.io/download.html](https://ngspice.sourceforge.io/download.html) and tick **"Install as shared library"** |

If the library is in a non-standard location, set the `NGSPICE_LIB` environment variable to its full path:
```
export NGSPICE_LIB=/opt/local/lib/libngspice.so
```

### Python packages

- Python 3.11+
- PySide6 ≥ 6.6
- numpy ≥ 1.26
- matplotlib ≥ 3.8

---

## Installation

```bash
git clone https://github.com/garyPenhook/ngspice_ui.git
cd ngspice_ui
pip install .
```

For development (editable install + test runner):
```bash
pip install -e ".[dev]"
pytest
```

---

## Quick start

```bash
ngspice-ui
```

1. **Open** `examples/rc_lowpass.cir` via **File → Open Netlist** (or Ctrl+O)
2. The Analysis panel on the left already shows the `.ac` sweep from the file — leave it on **"Use netlist as-is"**
3. Press **F5** (or **Run ▶**) to simulate
4. The PlotLab on the right plots magnitude and phase automatically
5. Switch to the **Measurements** dock (bottom tab) and add a row:
   - Name: `fc`, Expression: `vec("frequency")[np.argmin(np.abs(vec("v(out)") - 1/np.sqrt(2)))]`
   - Click **Evaluate** to read out the −3 dB corner frequency

---

## Schematic import (KiCad / Eagle)

**File → Open Netlist** accepts `.kicad_sch` and `.sch` files directly. The importer:

- Resolves wire connectivity via Union-Find
- Converts power-label names to valid SPICE identifiers (`+5V` → `vp5v`, `(VGND)` → `vgnd`, `0V`/`GND` → node `0`)
- Reads `Sim.Library` / `Sim.SpiceModel` / `Sim.Pins` KiCad properties and emits `.include` directives for referenced model files

To get working models in KiCad:
1. Right-click a component → **Properties → Simulation**
2. Browse to the `.lib` file for the part (e.g. `1N4148.lib`)
3. Select the model name and map the pins
4. KiCad stores `Sim.Library`, `Sim.SpiceModel`, `Sim.Pins` on the symbol — the importer picks them up automatically on next import

---

## Project files

**File → Save Project** saves the current netlist, analysis settings, and measurement expressions to a `.ngsui` file (JSON). **Load Project** restores everything in one step.

---

## Keyboard shortcuts

| Key | Action |
|---|---|
| F5 | Run simulation |
| Ctrl+O | Open netlist or schematic |
| Ctrl+S | Save netlist |
| Ctrl+Shift+S | Save netlist as… |
| Ctrl+N | New (clear editor) |
| Ctrl+Q | Exit |
| Ctrl+double-click | Quick-edit SPICE value under cursor |
| Ctrl+↑ / ↓ | Script REPL history |
| Ctrl+Return | Run script |

---

## Architecture

```
ngspice_ui/
├── engine/
│   ├── bindings.py        # ctypes wrappers (sharedspice.h)
│   ├── callbacks.py       # C→Python callback glue, SimEvent dataclasses
│   └── session.py         # NgSpiceSession singleton, VectorData
├── gui/
│   ├── app.py             # QApplication entry point
│   ├── main_window.py     # Menu, toolbar, dock layout, project I/O
│   ├── controllers/
│   │   └── sim_controller.py   # Queue drain, Qt signal bridge
│   └── widgets/
│       ├── analysis_panel.py   # Analysis type + parameter form
│       ├── console.py          # ngspice stdout/stderr display
│       ├── cosim_widget.py     # External V/I source table
│       ├── measurements_widget.py  # Expression evaluator table
│       ├── netlist_editor.py   # QPlainTextEdit + highlight + autocomplete
│       ├── netlist_highlighter.py
│       ├── plot_canvas.py      # PlotLab (matplotlib + trace tree)
│       └── script_widget.py    # Python REPL
├── models/
│   └── analyses.py        # Analysis specs (single source of truth)
└── schematic/
    ├── kicad_import.py    # KiCad 6/7 .kicad_sch → SPICE
    └── eagle_import.py    # Eagle .sch → SPICE
```

**Threading model:** ngspice runs its simulation on a background thread and fires C callbacks. Those callbacks only enqueue data onto a `queue.Queue`; a 50 ms `QTimer` on the GUI thread drains the queue and emits Qt signals. No widget is ever touched from a callback thread.

**Single-instance constraint:** libngspice is a process-global singleton. Only one `NgSpiceSession` may exist per process; attempting to create a second raises `RuntimeError`.

---

## Examples

`examples/rc_lowpass.cir` — RC low-pass filter, 1 kHz corner frequency, includes `.ac`, `.tran`, and `.op` analyses.

---

## License

MIT

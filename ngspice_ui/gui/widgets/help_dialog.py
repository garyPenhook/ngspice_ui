from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout

_HTML = """
<!DOCTYPE html>
<html>
<head>
<style>
  body { font-family: sans-serif; font-size: 13px; margin: 16px; color: #ddd; background: transparent; }
  h1 { font-size: 20px; color: #5af; border-bottom: 1px solid #444; padding-bottom: 4px; margin-top: 0; }
  h2 { font-size: 15px; color: #8cf; margin-top: 18px; margin-bottom: 4px; }
  h3 { font-size: 13px; color: #adf; margin-top: 12px; margin-bottom: 2px; }
  code, pre { font-family: monospace; background: #222; border-radius: 3px; padding: 1px 4px; }
  pre { display: block; padding: 8px; margin: 6px 0; white-space: pre-wrap; }
  table { border-collapse: collapse; width: 100%; margin: 6px 0; }
  th { background: #2a2a3a; text-align: left; padding: 4px 8px; }
  td { padding: 3px 8px; border-bottom: 1px solid #333; }
  .key { background: #333; border: 1px solid #555; border-radius: 3px; padding: 0 5px;
         font-family: monospace; font-size: 12px; }
  ul { margin: 4px 0; padding-left: 20px; }
  li { margin: 2px 0; }
  .tip { background: #1a2a1a; border-left: 3px solid #4a4; padding: 6px 10px; margin: 8px 0; }
</style>
</head>
<body>

<h1>ngspice-ui User Guide</h1>

<p>ngspice-ui is a PySide6 graphical front-end for <b>ngspice</b> (libngspice · ctypes).
It lets you write, simulate, and analyse SPICE netlists without touching the command line.</p>

<h2>Quick Start</h2>
<ol>
  <li>Open a netlist: <b>File → Open Netlist</b> or drag a <code>.cir / .net / .sp</code> file onto the window.</li>
  <li>Choose an analysis type in the <b>Analysis</b> dock (left panel).</li>
  <li>Press <span class="key">F5</span> (or <b>Simulation → Run ▶</b>) to run.</li>
  <li>Results appear automatically in the <b>Plot</b> area. Press <b>Plot</b> in the toolbar to refresh manually.</li>
</ol>

<h2>Menu Bar</h2>

<h3>File</h3>
<table>
<tr><th>Item</th><th>Shortcut</th><th>Description</th></tr>
<tr><td>New</td><td><span class="key">Ctrl+N</span></td><td>Clear the editor (prompts to save unsaved changes).</td></tr>
<tr><td>Open Netlist…</td><td><span class="key">Ctrl+O</span></td><td>Open a SPICE netlist, KiCad schematic, Eagle schematic, or project file.</td></tr>
<tr><td>Open Recent</td><td></td><td>Up to 10 recently opened files.</td></tr>
<tr><td>Save Netlist</td><td><span class="key">Ctrl+S</span></td><td>Save the current netlist to its file.</td></tr>
<tr><td>Save Netlist As…</td><td><span class="key">Ctrl+Shift+S</span></td><td>Save to a new file.</td></tr>
<tr><td>Save Project…</td><td></td><td>Save netlist + analysis setup + measurements + notes + scripts as a <code>.ngsui</code> bundle.</td></tr>
<tr><td>Load Project…</td><td></td><td>Restore a previously saved <code>.ngsui</code> project.</td></tr>
<tr><td>Exit</td><td><span class="key">Ctrl+Q</span></td><td>Quit the application.</td></tr>
</table>

<h3>Edit</h3>
<table>
<tr><th>Item</th><th>Shortcut</th><th>Description</th></tr>
<tr><td>Find / Replace…</td><td><span class="key">Ctrl+H</span></td><td>Open the find-and-replace bar in the netlist editor.</td></tr>
<tr><td>Lint Netlist</td><td><span class="key">F4</span></td><td>Run static checks on the netlist and highlight issues.</td></tr>
</table>

<h3>View</h3>
<p>Toggle any dock panel on/off, and choose between Dark and Light themes.</p>
<table>
<tr><th>Shortcut</th><th>Panel</th></tr>
<tr><td><span class="key">Alt+1</span></td><td>Analysis</td></tr>
<tr><td><span class="key">Alt+2</span></td><td>Console</td></tr>
<tr><td><span class="key">Alt+3</span></td><td>Measurements</td></tr>
<tr><td><span class="key">Alt+4</span></td><td>Script / Co-Sim</td></tr>
<tr><td><span class="key">Alt+5</span></td><td>Schematic (KiCad)</td></tr>
</table>

<h2>Panels</h2>

<h3>Netlist Editor (centre-left)</h3>
<p>Full-featured code editor with SPICE syntax highlighting and error squiggles from the linter.
Supports standard text editing shortcuts (<span class="key">Ctrl+Z</span> undo,
<span class="key">Ctrl+A</span> select all, etc.).</p>
<ul>
  <li>Modified files show <b>*</b> in the title bar.</li>
  <li>Line/column position shown in the status bar.</li>
  <li>Errors highlighted after linting (<span class="key">F4</span>) or after a failed simulation run.</li>
  <li>Find / Replace: <span class="key">Ctrl+H</span> — supports plain text and regex.</li>
</ul>

<h3>Plot (centre-right)</h3>
<p>Interactive matplotlib canvas. After a simulation the plot auto-refreshes.
Press <b>Plot</b> in the toolbar to re-render from the current session data.</p>
<ul>
  <li>Pan: hold the middle mouse button and drag, or use the pan tool.</li>
  <li>Zoom: scroll wheel, or the zoom-box tool.</li>
  <li>Home: resets to the auto-scaled view.</li>
  <li>Save: saves the current figure to a PNG/PDF/SVG via the toolbar.</li>
  <li>Probing a net in the Schematic dock adds it to the plot immediately.</li>
</ul>

<h3>Analysis Dock (left, <span class="key">Alt+1</span>)</h3>
<p>Choose the analysis type and configure its parameters before running.</p>
<table>
<tr><th>Type</th><th>Parameters</th></tr>
<tr><td>DC Operating Point (<code>.op</code>)</td><td>No parameters needed.</td></tr>
<tr><td>DC Sweep (<code>.dc</code>)</td><td>Source name, start, stop, step.</td></tr>
<tr><td>AC Analysis (<code>.ac</code>)</td><td>Scale (dec/oct/lin), points, start freq, stop freq.</td></tr>
<tr><td>Transient (<code>.tran</code>)</td><td>Step time, stop time, optional start time.</td></tr>
<tr><td>Noise (<code>.noise</code>)</td><td>Output node, input source, frequency sweep.</td></tr>
<tr><td>Transfer Function (<code>.tf</code>)</td><td>Output variable, input source.</td></tr>
<tr><td>Sensitivity (<code>.sens</code>)</td><td>Output variable.</td></tr>
</table>
<p>Temperature override is also available here; it appends <code>.temp</code> lines to the netlist before submission.</p>

<h3>Console (<span class="key">Alt+2</span>)</h3>
<p>Shows raw ngspice output: info messages, warnings, errors, and print results.
All text is selectable and copyable.</p>

<h3>Measurements (<span class="key">Alt+3</span>)</h3>
<p>Define post-simulation measurements (rise time, fall time, min, max, integral, etc.) on simulation vectors.
Measurements are re-evaluated automatically after each run and are saved as part of a project.</p>

<h3>Notes</h3>
<p>A free-text notepad saved inside the project file. Useful for documenting circuit intent or simulation findings.</p>

<h3>Script / Co-Sim (<span class="key">Alt+4</span>)</h3>
<ul>
  <li><b>Script tab</b> — Run Python snippets against the live ngspice session object. Access simulation vectors,
      issue ngspice commands, post-process results.</li>
  <li><b>Co-Sim tab</b> — Connect an external Python process to ngspice for mixed-signal co-simulation.</li>
</ul>

<h3>Schematic (KiCad, <span class="key">Alt+5</span>)</h3>
<p>Read-only viewer for <code>.kicad_sch</code> files. After opening a KiCad schematic the viewer loads automatically.
Click a wire or net label to probe it — the net is added to the plot.</p>
<p>After a <code>.op</code> simulation, node voltages are overlaid on the schematic automatically.</p>

<h3>Schematic (Eagle)</h3>
<p>Read-only viewer for Eagle <code>.sch</code> XML files. Same probe-to-plot behaviour as the KiCad viewer.</p>

<h3>Snippets</h3>
<p>A library of reusable SPICE fragments (component models, sub-circuits, analysis templates).
Double-click a snippet to insert it at the cursor in the netlist editor.</p>

<h3>Model Library</h3>
<p>Browse installed SPICE model files. Select a model and click <b>Insert</b> to add the
<code>.include</code> or <code>.model</code> line directly into the editor.</p>

<h3>Param Sweep</h3>
<p>Sweep a netlist parameter over a range of values and overlay all runs on the plot.
Set the parameter name, start, stop, and number of steps, then click <b>Run Sweep</b>.</p>
<div class="tip">The parameter must be defined in the netlist with a <code>.param name=value</code> line.</div>

<h3>Monte Carlo</h3>
<p>Run multiple simulations with randomly varied component values to characterise yield and tolerance.
Configure the number of runs and the tolerance percentage, then click <b>Run Monte Carlo</b>.
All run results are overlaid on the plot.</p>

<h2>Schematic Import</h2>
<p>ngspice-ui can import schematics from KiCad and Eagle and generate an initial SPICE netlist.</p>
<ul>
  <li><b>KiCad</b> — Open a <code>.kicad_sch</code> file. The importer reads the schematic hierarchy,
      converts component references to SPICE element lines, sanitises net names, and adds simulation
      library includes if found.</li>
  <li><b>Eagle</b> — Open an Eagle <code>.sch</code> XML file. Same conversion process.</li>
</ul>
<div class="tip">Generated netlists often need minor manual edits (model names, stimulus sources, analysis line)
before they will simulate correctly.</div>

<h2>SPICE Netlist Primer</h2>
<pre>* RC low-pass filter example
V1 vin 0 AC 1 SIN(0 1 1k)
R1 vin vout 1k
C1 vout 0 100n
.ac dec 100 1 1MEG
.end</pre>
<p>Key rules:</p>
<ul>
  <li>First line is the <b>title</b> — it is always ignored by ngspice.</li>
  <li>Lines starting with <code>*</code> are comments.</li>
  <li>Element lines start with a letter that identifies the type:
      <code>R</code> resistor, <code>C</code> capacitor, <code>L</code> inductor,
      <code>V</code> voltage source, <code>I</code> current source,
      <code>D</code> diode, <code>Q</code> BJT, <code>M</code> MOSFET,
      <code>X</code> subcircuit.</li>
  <li>The last line must be <code>.end</code>.</li>
  <li>Node <code>0</code> is always ground.</li>
</ul>

<h2>Analysis Commands</h2>
<table>
<tr><th>Command</th><th>Example</th></tr>
<tr><td><code>.op</code></td><td><code>.op</code></td></tr>
<tr><td><code>.dc</code></td><td><code>.dc V1 0 5 0.1</code></td></tr>
<tr><td><code>.ac</code></td><td><code>.ac dec 100 1 10MEG</code></td></tr>
<tr><td><code>.tran</code></td><td><code>.tran 1n 1u</code></td></tr>
<tr><td><code>.noise</code></td><td><code>.noise v(out) V1 dec 100 1 1MEG</code></td></tr>
<tr><td><code>.tf</code></td><td><code>.tf v(out) V1</code></td></tr>
<tr><td><code>.sens</code></td><td><code>.sens v(out)</code></td></tr>
<tr><td><code>.param</code></td><td><code>.param Rval=1k</code></td></tr>
<tr><td><code>.include</code></td><td><code>.include models/1N4148.lib</code></td></tr>
<tr><td><code>.model</code></td><td><code>.model 1N4148 D (Is=2.52n N=1.752)</code></td></tr>
<tr><td><code>.subckt</code></td><td><code>.subckt opamp in- in+ out ...</code></td></tr>
<tr><td><code>.temp</code></td><td><code>.temp 27</code></td></tr>
</table>

<h2>Keyboard Shortcuts Summary</h2>
<table>
<tr><th>Key</th><th>Action</th></tr>
<tr><td><span class="key">F5</span></td><td>Run simulation</td></tr>
<tr><td><span class="key">F4</span></td><td>Lint netlist</td></tr>
<tr><td><span class="key">Ctrl+N</span></td><td>New netlist</td></tr>
<tr><td><span class="key">Ctrl+O</span></td><td>Open file</td></tr>
<tr><td><span class="key">Ctrl+S</span></td><td>Save netlist</td></tr>
<tr><td><span class="key">Ctrl+Shift+S</span></td><td>Save netlist as…</td></tr>
<tr><td><span class="key">Ctrl+H</span></td><td>Find / Replace in editor</td></tr>
<tr><td><span class="key">Ctrl+Q</span></td><td>Exit</td></tr>
<tr><td><span class="key">Alt+1</span></td><td>Toggle Analysis dock</td></tr>
<tr><td><span class="key">Alt+2</span></td><td>Toggle Console dock</td></tr>
<tr><td><span class="key">Alt+3</span></td><td>Toggle Measurements dock</td></tr>
<tr><td><span class="key">Alt+4</span></td><td>Toggle Script / Co-Sim dock</td></tr>
<tr><td><span class="key">Alt+5</span></td><td>Toggle Schematic (KiCad) dock</td></tr>
</table>

<h2>Project Files (.ngsui)</h2>
<p>A project file is a JSON bundle that stores:</p>
<ul>
  <li>The netlist text</li>
  <li>Analysis type and parameters</li>
  <li>Measurement definitions</li>
  <li>Notes content</li>
  <li>Script and Co-Sim configuration</li>
</ul>
<p>Open a project with <b>File → Load Project</b> or by dragging a <code>.ngsui</code> file onto the window.</p>

<h2>Drag &amp; Drop</h2>
<p>Drag files directly onto the main window:</p>
<ul>
  <li><code>.cir .net .sp .spi</code> — opens as a netlist</li>
  <li><code>.kicad_sch</code> — imports as KiCad schematic</li>
  <li><code>.sch</code> — imports as Eagle schematic</li>
  <li><code>.ngsui</code> — loads as a project</li>
</ul>

<h2>Tips &amp; Troubleshooting</h2>
<ul>
  <li>If ngspice cannot find a model, add a <code>.include path/to/model.lib</code> line to the netlist.</li>
  <li>Node names are case-insensitive in SPICE but net probing in the schematic viewer is case-sensitive — use lowercase node names for consistency.</li>
  <li>Use <span class="key">F4</span> to lint before running — it catches common typos faster than the simulator does.</li>
  <li>The Console shows the raw ngspice log; scroll up to see warnings from earlier in the run.</li>
  <li>For large transient simulations, increase the step time to speed up the run.</li>
  <li>The <code>.param</code> line must appear <em>before</em> any element line that references the parameter.</li>
</ul>

</body>
</html>
"""


class HelpDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ngspice-ui User Guide")
        self.resize(780, 620)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml(_HTML)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.addWidget(browser)
        layout.addWidget(buttons)

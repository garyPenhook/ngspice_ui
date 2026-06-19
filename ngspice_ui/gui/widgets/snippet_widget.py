"""SPICE snippet library — sidebar with insertable template netlists."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

_SNIPPETS: list[tuple[str, str]] = [
    (
        "RC Low-pass",
        """\
* RC Low-pass filter
Vin in 0 AC 1 SIN(0 1 1k)
R1 in out 1k
C1 out 0 159n
.ac dec 20 10 100k
.tran 1us 2ms
.end
""",
    ),
    (
        "Voltage divider",
        """\
* Resistor voltage divider
Vin in 0 DC 5
R1 in mid 10k
R2 mid 0 10k
.op
.end
""",
    ),
    (
        "BJT common-emitter",
        """\
* NPN common-emitter amplifier
Vcc vcc 0 DC 12
Vin in 0 AC 1
Rb1 vcc base 100k
Rb2 base 0 47k
Rc vcc col 4.7k
Re col 0 1k
C1 in base 10u
Q1 col base 0 NPN_2N3904
.model NPN_2N3904 NPN(IS=1e-14 BF=300)
.op
.ac dec 20 10 10MEG
.end
""",
    ),
    (
        "MOSFET inverter",
        """\
* CMOS inverter (NMOS + PMOS)
Vdd vdd 0 DC 3.3
Vin in 0 PULSE(0 3.3 10n 1n 1n 50n 100n)
Mn out in 0 0 NMOS W=1u L=180n
Mp out in vdd vdd PMOS W=2u L=180n
.model NMOS NMOS(VT0=0.5 KP=200u)
.model PMOS PMOS(VT0=-0.5 KP=100u)
.tran 1n 500n
.end
""",
    ),
    (
        "Op-amp non-inverting",
        """\
* Ideal op-amp non-inverting amplifier (gain=11)
Vin in 0 AC 1 SIN(0 0.1 1k)
Vcc vcc 0 DC 15
Vee vee 0 DC -15
R1 out fb 10k
R2 fb 0 1k
XU1 in fb out vcc vee OPAMP
.subckt OPAMP inp inn out vcc vee
Eout out 0 inp inn 100k
.ends OPAMP
.ac dec 20 1 1MEG
.tran 10u 3m
.end
""",
    ),
    (
        "LC Band-pass",
        """\
* LC band-pass filter (f0 ≈ 1 MHz)
Vin in 0 AC 1
L1 in mid 25.3u
C1 mid out 1n
R1 out 0 50
.ac dec 50 100k 10MEG
.end
""",
    ),
    (
        "Current mirror",
        """\
* Simple NMOS current mirror
Vdd vdd 0 DC 5
Iref vdd ref 100u
M1 ref ref 0 0 NMOS W=10u L=1u
M2 out ref 0 0 NMOS W=10u L=1u
Rload vdd out 10k
.model NMOS NMOS(VT0=0.7 KP=200u)
.op
.dc Vdd 0 5 0.1
.end
""",
    ),
    (
        "Diode half-wave rectifier",
        """\
* Half-wave rectifier
Vin in 0 SIN(0 5 60)
D1 in out 1N4148
Rload out 0 1k
Cfilter out 0 100u
.model 1N4148 D(IS=2.52e-9 N=1.752 RS=0.568 CJO=4e-12)
.tran 100u 50m
.end
""",
    ),
    (
        ".param sweep template",
        """\
* Parametric sweep example
.param Rval=1k
Vin in 0 DC 1
R1 in out {Rval}
Rload out 0 1k
.step param Rval list 100 1k 10k 100k
.op
.end
""",
    ),
    (
        "Monte Carlo template",
        """\
* Monte Carlo — add gaussian variation to components
.param Rtol=0.05
.param R1val = {gauss(10k, Rtol, 3)}
Vin in 0 DC 1
R1 in out {R1val}
R2 out 0 10k
.op
.end
""",
    ),
]


class SnippetWidget(QWidget):
    """Snippet sidebar: select a template, preview it, insert into editor."""

    insert_requested = Signal(str)  # text to insert at editor cursor

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        self._list = QListWidget()
        for name, _ in _SNIPPETS:
            self._list.addItem(QListWidgetItem(name))
        self._list.currentRowChanged.connect(self._on_select)

        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)

        btn_insert = QPushButton("Insert into editor")
        btn_insert.clicked.connect(self._insert)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._list)
        splitter.addWidget(self._preview)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        lay.addWidget(QLabel("<b>SPICE Snippets</b>"))
        lay.addWidget(splitter)
        lay.addWidget(btn_insert)

        if _SNIPPETS:
            self._list.setCurrentRow(0)

    def _on_select(self, row: int) -> None:
        if 0 <= row < len(_SNIPPETS):
            self._preview.setPlainText(_SNIPPETS[row][1])

    def _insert(self) -> None:
        row = self._list.currentRow()
        if 0 <= row < len(_SNIPPETS):
            self.insert_requested.emit(_SNIPPETS[row][1])

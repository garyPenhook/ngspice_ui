"""Read-only graphical viewer for Eagle .sch schematics (Eagle XML format)."""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QMouseEvent, QPainter, QPen, QWheelEvent,
)
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

_C_BG    = QColor("#1a1a1a")
_C_WIRE  = QColor("#00cc44")
_C_BODY  = QColor("#ccaa00")
_C_PIN   = QColor("#ccaa00")
_C_LABEL = QColor("#00cccc")
_C_REF   = QColor("#e0e0e0")
_C_VALUE = QColor("#909070")
_C_HIGHLIGHT = QColor("#ffff00")

EAGLE_MM = 25.4 / 90.0  # Eagle units → mm (90 dpi grid)


@dataclass
class _EWire:
    x1: float; y1: float; x2: float; y2: float
    net: str = ""


@dataclass
class _ELabel:
    text: str; x: float; y: float


@dataclass
class _EPart:
    ref: str; value: str; x: float; y: float; angle: float


@dataclass
class _ESchematic:
    wires: list[_EWire] = field(default_factory=list)
    labels: list[_ELabel] = field(default_factory=list)
    parts: list[_EPart] = field(default_factory=list)
    bbox: tuple = (0.0, 0.0, 100.0, 100.0)
    net_wires: dict[str, list[int]] = field(default_factory=dict)


def _parse_eagle(path: Path) -> _ESchematic:
    tree = ET.parse(str(path))
    root = tree.getroot()
    sch = _ESchematic()
    xs: list[float] = []
    ys: list[float] = []

    def mm(v: str) -> float:
        return float(v) * EAGLE_MM

    sheets = root.findall(".//sheet")
    if not sheets:
        raise ValueError("No <sheet> element found in Eagle schematic")

    sheet = sheets[0]

    # Nets → wires
    for net in sheet.findall("nets/net"):
        net_name = net.get("name", "")
        for seg in net.findall("segment"):
            for wire in seg.findall("wire"):
                x1 = mm(wire.get("x1", "0"))
                y1 = mm(wire.get("y1", "0"))
                x2 = mm(wire.get("x2", "0"))
                y2 = mm(wire.get("y2", "0"))
                idx = len(sch.wires)
                sch.wires.append(_EWire(x1, y1, x2, y2, net_name))
                sch.net_wires.setdefault(net_name, []).append(idx)
                xs += [x1, x2]; ys += [y1, y2]
            for lbl in seg.findall("label"):
                lx = mm(lbl.get("x", "0"))
                ly = mm(lbl.get("y", "0"))
                sch.labels.append(_ELabel(net_name, lx, ly))

    # Parts
    for inst in sheet.findall("instances/instance"):
        ref = inst.get("part", "")
        px = mm(inst.get("x", "0"))
        py = mm(inst.get("y", "0"))
        angle = float(inst.get("rot", "R0").lstrip("MR") or 0)
        # Find value from <parts>
        part_el = root.find(f".//part[@name='{ref}']")
        value = part_el.get("value", "") if part_el is not None else ""
        sch.parts.append(_EPart(ref, value, px, py, angle))
        xs.append(px); ys.append(py)

    if xs:
        pad = 5.0
        sch.bbox = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)

    return sch


class _EagleCanvas(QWidget):
    net_probed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._sch: _ESchematic | None = None
        self._err = ""
        self._scale = 5.0
        self._pan_x = 20.0
        self._pan_y = 20.0
        self._drag_start: QPointF | None = None
        self._drag_pan0 = (0.0, 0.0)
        self._fitted = False
        self._highlighted_net: str | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

    def load(self, path: Path) -> None:
        try:
            self._sch = _parse_eagle(path)
            self._err = ""
        except Exception as exc:
            self._sch = None
            self._err = str(exc)
        self._highlighted_net = None
        self._fitted = False
        self.update()
        if self.isVisible():
            self._fit()

    def clear(self) -> None:
        self._sch = None
        self._fitted = False
        self.update()

    def fit(self) -> None:
        self._fit()

    def zoom_in(self) -> None:
        self._zoom(1.25, self.width() / 2, self.height() / 2)

    def zoom_out(self) -> None:
        self._zoom(0.8, self.width() / 2, self.height() / 2)

    def _fit(self) -> None:
        if self._sch is None or self.width() < 10:
            return
        xmin, ymin, xmax, ymax = self._sch.bbox
        w_mm = max(xmax - xmin, 1.0)
        h_mm = max(ymax - ymin, 1.0)
        sx = (self.width() - 20) / w_mm
        sy = (self.height() - 20) / h_mm
        self._scale = min(sx, sy, 40.0)
        self._pan_x = (self.width() - w_mm * self._scale) / 2 - xmin * self._scale
        self._pan_y = (self.height() - h_mm * self._scale) / 2 - ymin * self._scale
        self._fitted = True
        self.update()

    def _zoom(self, factor: float, cx: float, cy: float) -> None:
        self._pan_x = cx + (self._pan_x - cx) * factor
        self._pan_y = cy + (self._pan_y - cy) * factor
        self._scale *= factor
        self.update()

    def _screen_to_sch(self, sx, sy):
        return (sx - self._pan_x) / self._scale, (sy - self._pan_y) / self._scale

    def _net_at(self, sx, sy) -> str | None:
        if self._sch is None:
            return None
        mx, my = self._screen_to_sch(sx, sy)
        eps = 1.0
        for w in self._sch.wires:
            dx, dy = w.x2 - w.x1, w.y2 - w.y1
            length = math.hypot(dx, dy)
            if length < 1e-6:
                d = math.hypot(mx - w.x1, my - w.y1)
            else:
                t = max(0.0, min(1.0, ((mx - w.x1) * dx + (my - w.y1) * dy) / (length * length)))
                d = math.hypot(mx - (w.x1 + t * dx), my - (w.y1 + t * dy))
            if d < eps:
                return w.net
        return None

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._fitted and self._sch is not None:
            self._fit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._fitted and self._sch is not None:
            self._fit()

    def wheelEvent(self, event: QWheelEvent) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        pos = event.position()
        self._zoom(factor, pos.x(), pos.y())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        mods = event.modifiers()
        if event.button() == Qt.MouseButton.LeftButton and mods & Qt.KeyboardModifier.ControlModifier:
            net = self._net_at(pos.x(), pos.y())
            if net:
                self.net_probed.emit(net)
            return
        net = self._net_at(pos.x(), pos.y())
        if event.button() == Qt.MouseButton.LeftButton and net:
            self._highlighted_net = net if self._highlighted_net != net else None
            self.update()
            return
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._drag_start = pos
            self._drag_pan0 = (self._pan_x, self._pan_y)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is not None:
            d = event.position() - self._drag_start
            self._pan_x = self._drag_pan0[0] + d.x()
            self._pan_y = self._drag_pan0[1] + d.y()
            self.update()
        else:
            net = self._net_at(event.position().x(), event.position().y())
            self.setToolTip(net or "")

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_start = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), _C_BG)

        if self._sch is None:
            p.setPen(QPen(_C_REF))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       self._err or "No Eagle schematic loaded")
            return

        sch = self._sch
        lw = max(0.05, 0.15 / self._scale)
        p.translate(self._pan_x, self._pan_y)
        p.scale(self._scale, self._scale)

        # Wires
        hl = self._highlighted_net
        pen_w = QPen(_C_WIRE, lw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        pen_hl = QPen(_C_HIGHLIGHT, lw * 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        for w in sch.wires:
            p.setPen(pen_hl if (hl and w.net == hl) else pen_w)
            p.drawLine(QPointF(w.x1, w.y1), QPointF(w.x2, w.y2))

        # Part placeholders (cross + ref/value)
        p.setPen(QPen(_C_BODY, lw))
        cs = 0.8
        font = QFont("Sans Serif")
        font.setPixelSize(max(6, min(12, int(self._scale * 1.2))))

        for part in sch.parts:
            p.setPen(QPen(_C_BODY, lw))
            p.drawLine(QPointF(part.x - cs, part.y), QPointF(part.x + cs, part.y))
            p.drawLine(QPointF(part.x, part.y - cs), QPointF(part.x, part.y + cs))

        p.resetTransform()
        p.setFont(font)
        for part in sch.parts:
            sx = part.x * self._scale + self._pan_x
            sy = part.y * self._scale + self._pan_y
            p.setPen(QPen(_C_REF))
            p.drawText(QRectF(sx + 2, sy - 14, 80, 14), part.ref)
            if part.value:
                p.setPen(QPen(_C_VALUE))
                p.drawText(QRectF(sx + 2, sy + 2, 80, 14), part.value)

        # Labels
        p.setFont(font)
        p.setPen(QPen(_C_LABEL))
        for lbl in sch.labels:
            sx = lbl.x * self._scale + self._pan_x
            sy = lbl.y * self._scale + self._pan_y
            p.drawText(QRectF(sx + 1, sy - 12, 120, 12), lbl.text)

        p.end()


class EagleView(QWidget):
    """Eagle schematic viewer, mirrors the SchematicView API."""

    net_probed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._canvas = _EagleCanvas()
        self._canvas.net_probed.connect(self.net_probed)

        btn_fit = QToolButton(); btn_fit.setText("Fit"); btn_fit.setFixedWidth(36)
        btn_in  = QToolButton(); btn_in.setText("+");   btn_in.setFixedWidth(28)
        btn_out = QToolButton(); btn_out.setText("−");  btn_out.setFixedWidth(28)
        self._info = QLabel()
        self._info.setStyleSheet("color: #808080; font-size: 9pt;")

        btn_fit.clicked.connect(self._canvas.fit)
        btn_in.clicked.connect(self._canvas.zoom_in)
        btn_out.clicked.connect(self._canvas.zoom_out)

        tb = QHBoxLayout()
        tb.setContentsMargins(4, 2, 4, 2)
        tb.setSpacing(4)
        for w in (btn_fit, btn_in, btn_out, self._info):
            tb.addWidget(w)
        tb.addStretch()
        tb_widget = QWidget(); tb_widget.setLayout(tb)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(tb_widget)
        lay.addWidget(self._canvas, 1)

    def load(self, path: Path) -> None:
        self._canvas.load(path)
        self._info.setText(path.name)

    def clear(self) -> None:
        self._canvas.clear()

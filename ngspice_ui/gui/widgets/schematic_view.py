"""Read-only graphical viewer for KiCad 6/7 .kicad_sch files."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...schematic.kicad.sexpr import atom as _atom
from ...schematic.kicad.sexpr import find as _find
from ...schematic.kicad.sexpr import find_all as _find_all
from ...schematic.kicad.sexpr import parse_sexp as _parse_sexp
from ...schematic.kicad.sexpr import prop as _prop
from ...schematic.kicad.sexpr import sub_symbol_unit as _sub_unit

# ---------------------------------------------------------------------------
# Colors  (KiCad-inspired dark scheme)
# ---------------------------------------------------------------------------
_C_BG = QColor("#1a1a2e")
_C_WIRE = QColor("#00c800")
_C_BUS = QColor("#0000d0")
_C_JCT = QColor("#00c800")
_C_NCONN = QColor("#c80000")
_C_BODY = QColor("#d4a017")
_C_PIN = QColor("#d4a017")
_C_LABEL = QColor("#00c8c8")
_C_GLABEL = QColor("#c800c8")
_C_POWER = QColor("#00c800")
_C_REF = QColor("#e8e8e8")
_C_VALUE = QColor("#a0a080")

# ---------------------------------------------------------------------------
# Internal data classes
# ---------------------------------------------------------------------------


@dataclass
class _Wire:
    x1: float
    y1: float
    x2: float
    y2: float
    is_bus: bool = False


@dataclass
class _Junction:
    x: float
    y: float


@dataclass
class _NoConnect:
    x: float
    y: float


@dataclass
class _Label:
    text: str
    x: float
    y: float
    angle: float
    kind: str  # 'local' | 'global' | 'hier' | 'power'


@dataclass
class _LibGraphic:
    kind: str  # 'polyline'|'circle'|'arc'|'pin'
    pts: list = field(default_factory=list)
    filled: bool = False
    cx: float = 0
    cy: float = 0
    radius: float = 0
    pin_angle: float = 0
    pin_length: float = 0
    pin_name: str = ""
    pin_number: str = ""


@dataclass
class _SymDef:
    lib_id: str
    # unit → graphics; unit 0 is common to every unit. Multi-unit ICs draw
    # only the placed unit's graphics (plus unit 0), not every unit at once.
    graphics_by_unit: dict[int, list[_LibGraphic]] = field(default_factory=dict)

    def graphics_for_unit(self, unit: int) -> list[_LibGraphic]:
        return self.graphics_by_unit.get(0, []) + self.graphics_by_unit.get(unit, [])


@dataclass
class _PlacedSym:
    lib_id: str
    x: float
    y: float
    angle: float
    mirror_x: bool
    mirror_y: bool
    reference: str
    value: str
    unit: int = 1
    ref_x: float = 0
    ref_y: float = 0
    ref_angle: float = 0
    hide_ref: bool = False
    val_x: float = 0
    val_y: float = 0
    val_angle: float = 0
    hide_val: bool = False


@dataclass
class _Schematic:
    wires: list[_Wire] = field(default_factory=list)
    junctions: list[_Junction] = field(default_factory=list)
    noconns: list[_NoConnect] = field(default_factory=list)
    labels: list[_Label] = field(default_factory=list)
    syms: list[_PlacedSym] = field(default_factory=list)
    sym_defs: dict[str, _SymDef] = field(default_factory=dict)
    bbox: tuple = (0.0, 0.0, 100.0, 100.0)
    # wire-index → net-id (built lazily via Union-Find)
    wire_net: dict[int, int] = field(default_factory=dict)
    # net-id → label name
    net_names: dict[int, str] = field(default_factory=dict)


def _build_net_connectivity(sch: _Schematic) -> None:
    """Union-Find wire connectivity → populate wire_net and net_names.

    Uses an endpoint → [wire_index] dict for O(n) union-find construction
    instead of O(n²) pairwise comparison.
    """
    parent: list[int] = list(range(len(sch.wires)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        pa, pb = find(a), find(b)
        if pa != pb:
            parent[pa] = pb

    # Round endpoints to a fixed grid to make float equality robust
    _GRID = 1e-4

    def _snap(v: float) -> int:
        return round(v / _GRID)

    # Build endpoint → list of wire indices in O(n)
    endpoint_map: dict[tuple[int, int], list[int]] = {}
    for i, w in enumerate(sch.wires):
        for sx, sy in ((_snap(w.x1), _snap(w.y1)), (_snap(w.x2), _snap(w.y2))):
            endpoint_map.setdefault((sx, sy), []).append(i)

    # Union all wires that share an endpoint
    for wires_at_pt in endpoint_map.values():
        for k in range(1, len(wires_at_pt)):
            union(wires_at_pt[0], wires_at_pt[k])

    for i in range(len(sch.wires)):
        sch.wire_net[i] = find(i)

    # Map labels to nets via the same endpoint dict
    for lbl in sch.labels:
        key = (_snap(lbl.x), _snap(lbl.y))
        for wire_idx in endpoint_map.get(key, []):
            sch.net_names[find(wire_idx)] = lbl.text


# ---------------------------------------------------------------------------
# Arc through 3 points as polyline samples
# ---------------------------------------------------------------------------


def _arc_path(
    sx: float, sy: float, mx: float, my: float, ex: float, ey: float, steps: int = 32
) -> QPainterPath:
    d = 2 * (sx * (my - ey) + mx * (ey - sy) + ex * (sy - my))
    if abs(d) < 1e-10:
        path = QPainterPath()
        path.moveTo(sx, sy)
        path.lineTo(ex, ey)
        return path
    ux = (
        (sx * sx + sy * sy) * (my - ey)
        + (mx * mx + my * my) * (ey - sy)
        + (ex * ex + ey * ey) * (sy - my)
    ) / d
    uy = (
        (sx * sx + sy * sy) * (ex - mx)
        + (mx * mx + my * my) * (sx - ex)
        + (ex * ex + ey * ey) * (mx - sx)
    ) / d
    r = math.hypot(sx - ux, sy - uy)
    a0 = math.atan2(sy - uy, sx - ux)
    a1 = math.atan2(ey - uy, ex - ux)
    am = math.atan2(my - uy, mx - ux)
    # Choose sweep direction that passes through mid
    span_ccw = (a1 - a0) % (2 * math.pi)
    span_cw = -((a0 - a1) % (2 * math.pi))
    mid_ccw = (am - a0) % (2 * math.pi)
    span = span_ccw if mid_ccw <= span_ccw else span_cw
    path = QPainterPath()
    for i in range(steps + 1):
        t = i / steps
        a = a0 + span * t
        xp = ux + r * math.cos(a)
        yp = uy + r * math.sin(a)
        if i == 0:
            path.moveTo(xp, yp)
        else:
            path.lineTo(xp, yp)
    return path


# ---------------------------------------------------------------------------
# Graphics parser
# ---------------------------------------------------------------------------


def _is_filled(node: list) -> bool:
    fn = _find(node, "fill")
    if fn is None:
        return False
    tn = _find(fn, "type")
    return tn is not None and _atom(tn, 1) not in ("none", "")


def _parse_graphics(node: list, out: list[_LibGraphic]) -> None:
    for poly in _find_all(node, "polyline"):
        pts_n = _find(poly, "pts")
        if pts_n is None:
            continue
        pts2 = [
            (float(_atom(xy, 1, "0")), float(_atom(xy, 2, "0"))) for xy in _find_all(pts_n, "xy")
        ]
        out.append(_LibGraphic(kind="polyline", pts=pts2, filled=_is_filled(poly)))

    for rect in _find_all(node, "rectangle"):
        s = _find(rect, "start")
        e = _find(rect, "end")
        if s is None or e is None:
            continue
        x1, y1 = float(_atom(s, 1, "0")), float(_atom(s, 2, "0"))
        x2, y2 = float(_atom(e, 1, "0")), float(_atom(e, 2, "0"))
        pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
        out.append(_LibGraphic(kind="polyline", pts=pts, filled=_is_filled(rect)))

    for circ in _find_all(node, "circle"):
        c = _find(circ, "center")
        rn = _find(circ, "radius")
        if c is None or rn is None:
            continue
        out.append(
            _LibGraphic(
                kind="circle",
                cx=float(_atom(c, 1, "0")),
                cy=float(_atom(c, 2, "0")),
                radius=float(_atom(rn, 1, "0")),
                filled=_is_filled(circ),
            )
        )

    for arc in _find_all(node, "arc"):
        s = _find(arc, "start")
        m = _find(arc, "mid")
        e = _find(arc, "end")
        if None in (s, m, e):
            continue
        out.append(
            _LibGraphic(
                kind="arc",
                pts=[
                    (float(_atom(s, 1, "0")), float(_atom(s, 2, "0"))),
                    (float(_atom(m, 1, "0")), float(_atom(m, 2, "0"))),
                    (float(_atom(e, 1, "0")), float(_atom(e, 2, "0"))),
                ],
            )
        )

    for pin in _find_all(node, "pin"):
        at = _find(pin, "at")
        if at is None:
            continue
        ln = _find(pin, "length")
        num_n = _find(pin, "number")
        name_n = _find(pin, "name")
        out.append(
            _LibGraphic(
                kind="pin",
                pts=[(float(_atom(at, 1, "0")), float(_atom(at, 2, "0")))],
                pin_angle=float(_atom(at, 3, "0")),
                pin_length=float(_atom(ln, 1, "1.016")) if ln else 1.016,
                pin_number=_atom(num_n, 1) if num_n else "",
                pin_name=_atom(name_n, 1) if name_n else "",
            )
        )


# ---------------------------------------------------------------------------
# Full schematic parser
# ---------------------------------------------------------------------------


def _parse_schematic(path: Path) -> _Schematic:
    text = path.read_text(encoding="utf-8", errors="replace")
    root = _parse_sexp(text)
    if not isinstance(root, list) or not root or root[0] != "kicad_sch":
        raise ValueError(f"Not a valid KiCad 6/7 .kicad_sch: {path.name}")

    sch = _Schematic()
    xs: list[float] = []
    ys: list[float] = []

    def _track(x: float, y: float) -> None:
        xs.append(x)
        ys.append(y)

    # -- Wires & buses --
    for tag, is_bus in (("wire", False), ("bus_wire", False), ("bus", True)):
        for w in _find_all(root, tag):
            pts = _find(w, "pts")
            if pts is None:
                continue
            xys = _find_all(pts, "xy")
            if len(xys) >= 2:
                x1, y1 = float(_atom(xys[0], 1, "0")), float(_atom(xys[0], 2, "0"))
                x2, y2 = float(_atom(xys[1], 1, "0")), float(_atom(xys[1], 2, "0"))
                sch.wires.append(_Wire(x1, y1, x2, y2, is_bus=(tag == "bus")))
                _track(x1, y1)
                _track(x2, y2)

    # -- Junctions --
    for j in _find_all(root, "junction"):
        at = _find(j, "at")
        if at:
            x, y = float(_atom(at, 1, "0")), float(_atom(at, 2, "0"))
            sch.junctions.append(_Junction(x, y))
            _track(x, y)

    # -- No-connects --
    for nc in _find_all(root, "no_connect"):
        at = _find(nc, "at")
        if at:
            x, y = float(_atom(at, 1, "0")), float(_atom(at, 2, "0"))
            sch.noconns.append(_NoConnect(x, y))
            _track(x, y)

    # -- Labels --
    for tag, kind in (
        ("label", "local"),
        ("global_label", "global"),
        ("hierarchical_label", "hier"),
    ):
        for lbl in _find_all(root, tag):
            name = _atom(lbl, 1)
            at = _find(lbl, "at")
            if name and at:
                x, y = float(_atom(at, 1, "0")), float(_atom(at, 2, "0"))
                a = float(_atom(at, 3, "0"))
                sch.labels.append(_Label(name, x, y, a, kind))
                _track(x, y)

    # -- Lib symbol definitions --
    ls = _find(root, "lib_symbols")
    if ls:
        for sym in _find_all(ls, "symbol"):
            lib_id = _atom(sym, 1)
            sdef = _SymDef(lib_id=lib_id)
            for sub in _find_all(sym, "symbol"):
                sub_name = _atom(sub, 1)
                # skip De Morgan (body style 2)
                parts = sub_name.rsplit("_", 1)
                if len(parts) == 2 and parts[1] == "2":
                    continue
                unit_graphics = sdef.graphics_by_unit.setdefault(_sub_unit(sub_name), [])
                _parse_graphics(sub, unit_graphics)
            sch.sym_defs[lib_id] = sdef

    # -- Placed symbols --
    for sym in _find_all(root, "symbol"):
        lib_id_n = _find(sym, "lib_id")
        if lib_id_n is None:
            continue
        lib_id = _atom(lib_id_n, 1)
        if lib_id.startswith("power:"):
            # treat power symbols as labels
            at = _find(sym, "at")
            if at:
                x, y = float(_atom(at, 1, "0")), float(_atom(at, 2, "0"))
                pname = _prop(sym, "Value") or lib_id.split(":")[-1]
                sch.labels.append(_Label(pname, x, y, 0.0, "power"))
                _track(x, y)
            continue

        ref = _prop(sym, "Reference")
        if not ref or ref.startswith("#"):
            continue

        at = _find(sym, "at")
        if at is None:
            continue
        sx = float(_atom(at, 1, "0"))
        sy_ = float(_atom(at, 2, "0"))
        sa = float(_atom(at, 3, "0"))
        mir = _find(sym, "mirror")
        mx_ = isinstance(mir, list) and _atom(mir, 1) == "x"
        my_ = isinstance(mir, list) and _atom(mir, 1) == "y"

        unit_n = _find(sym, "unit")
        unit = 1
        if unit_n is not None:
            try:
                unit = int(_atom(unit_n, 1, "1"))
            except ValueError:
                unit = 1

        value = _prop(sym, "Value")

        # Property positions (absolute in placed symbol)
        ref_x = sx
        ref_y = sy_
        ref_a = 0.0
        hide_ref = False
        val_x = sx
        val_y = sy_
        val_a = 0.0
        hide_val = False
        for prop in _find_all(sym, "property"):
            pname = _atom(prop, 1)
            pat = _find(prop, "at")
            if pat is None:
                continue
            px_, py_ = float(_atom(pat, 1, "0")), float(_atom(pat, 2, "0"))
            pa_ = float(_atom(pat, 3, "0"))
            eff = _find(prop, "effects")
            hidden = eff is not None and _find(eff, "hide") is not None
            if pname == "Reference":
                ref_x, ref_y, ref_a, hide_ref = px_, py_, pa_, hidden
            elif pname == "Value":
                val_x, val_y, val_a, hide_val = px_, py_, pa_, hidden

        psym = _PlacedSym(
            lib_id=lib_id,
            x=sx,
            y=sy_,
            angle=sa,
            mirror_x=mx_,
            mirror_y=my_,
            reference=ref,
            value=value,
            unit=unit,
            ref_x=ref_x,
            ref_y=ref_y,
            ref_angle=ref_a,
            hide_ref=hide_ref,
            val_x=val_x,
            val_y=val_y,
            val_angle=val_a,
            hide_val=hide_val,
        )
        sch.syms.append(psym)
        _track(sx, sy_)

    if xs:
        pad = 5.0
        sch.bbox = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)

    _build_net_connectivity(sch)
    return sch


# ---------------------------------------------------------------------------
# Painter widget
# ---------------------------------------------------------------------------

_C_HIGHLIGHT = QColor("#ffff00")
_C_OP_LABEL = QColor("#00ffcc")


class _SchCanvas(QWidget):
    """Inner painter canvas with pan/zoom, net highlighting, and OP annotations."""

    net_probed = Signal(str)  # emitted with net name on Ctrl+click

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sch: _Schematic | None = None
        self._scale = 5.0
        self._pan_x = 20.0
        self._pan_y = 20.0
        self._drag_start: QPointF | None = None
        self._drag_pan0 = (0.0, 0.0)
        self._fitted = False
        self._highlighted_net: int | None = None
        self._op_voltages: dict[str, float] = {}
        self._show_op = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

    def load(self, path: Path) -> None:
        try:
            self._sch = _parse_schematic(path)
        except Exception as exc:
            self._sch = None
            self._err = str(exc)
        else:
            self._err = ""
        self._highlighted_net = None
        self._fitted = False
        self.update()
        if self.isVisible():
            self._fit()

    def set_op_voltages(self, voltages: dict[str, float], show: bool = True) -> None:
        self._op_voltages = voltages
        self._show_op = show
        self.update()

    def _screen_to_sch(self, sx: float, sy: float) -> tuple[float, float]:
        return (sx - self._pan_x) / self._scale, (sy - self._pan_y) / self._scale

    def _net_at(self, sx: float, sy: float) -> int | None:
        if self._sch is None:
            return None
        mx, my = self._screen_to_sch(sx, sy)
        eps = 1.0  # mm tolerance
        for i, w in enumerate(self._sch.wires):
            # point-to-segment distance
            dx, dy = w.x2 - w.x1, w.y2 - w.y1
            length = math.hypot(dx, dy)
            if length < 1e-6:
                d = math.hypot(mx - w.x1, my - w.y1)
            else:
                t = max(0.0, min(1.0, ((mx - w.x1) * dx + (my - w.y1) * dy) / (length * length)))
                px = w.x1 + t * dx
                py = w.y1 + t * dy
                d = math.hypot(mx - px, my - py)
            if d < eps:
                return self._sch.wire_net.get(i)
        return None

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

    # -- private --

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
        ctrl = mods & Qt.KeyboardModifier.ControlModifier
        if event.button() == Qt.MouseButton.LeftButton and ctrl:
            # Ctrl+click → probe net
            net_id = self._net_at(pos.x(), pos.y())
            if net_id is not None and self._sch is not None:
                name = self._sch.net_names.get(net_id, f"net{net_id}")
                self.net_probed.emit(name)
            return
        if event.button() == Qt.MouseButton.LeftButton and not ctrl:
            net_id = self._net_at(pos.x(), pos.y())
            if net_id is not None:
                self._highlighted_net = net_id if self._highlighted_net != net_id else None
                self.update()
                return
        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self._drag_start = pos
            self._drag_pan0 = (self._pan_x, self._pan_y)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._drag_start is not None:
            d = pos - self._drag_start
            self._pan_x = self._drag_pan0[0] + d.x()
            self._pan_y = self._drag_pan0[1] + d.y()
            self.update()
        else:
            # Hover tooltip
            net_id = self._net_at(pos.x(), pos.y())
            if net_id is not None and self._sch is not None:
                name = self._sch.net_names.get(net_id, f"net{net_id}")
                self.setToolTip(name)
            else:
                self.setToolTip("")

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_start = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    # -- rendering --

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), _C_BG)

        if self._sch is None:
            msg = getattr(self, "_err", "") or "No schematic loaded"
            p.setPen(QPen(_C_REF))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, msg)
            return

        sch = self._sch
        lw = max(0.05, 0.15 / self._scale)  # line width in mm
        lw_bus = lw * 2.5

        p.translate(self._pan_x, self._pan_y)
        p.scale(self._scale, self._scale)

        # 1. Wires (with net highlighting)
        hl_net = self._highlighted_net
        pen_wire = QPen(_C_WIRE, lw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        pen_hl = QPen(_C_HIGHLIGHT, lw * 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        pen_bus = QPen(_C_BUS, lw_bus, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)

        for i, w in enumerate(sch.wires):
            if w.is_bus:
                continue
            net_id = sch.wire_net.get(i)
            p.setPen(pen_hl if (hl_net is not None and net_id == hl_net) else pen_wire)
            p.drawLine(QPointF(w.x1, w.y1), QPointF(w.x2, w.y2))

        p.setPen(pen_bus)
        for w in sch.wires:
            if w.is_bus:
                p.drawLine(QPointF(w.x1, w.y1), QPointF(w.x2, w.y2))

        # 2. Junctions
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_C_JCT))
        jr = 0.5
        for j in sch.junctions:
            p.drawEllipse(QPointF(j.x, j.y), jr, jr)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # 3. No-connects
        p.setPen(QPen(_C_NCONN, lw))
        nc_s = 0.6
        for nc in sch.noconns:
            p.drawLine(QPointF(nc.x - nc_s, nc.y - nc_s), QPointF(nc.x + nc_s, nc.y + nc_s))
            p.drawLine(QPointF(nc.x + nc_s, nc.y - nc_s), QPointF(nc.x - nc_s, nc.y + nc_s))

        # 4. Symbol bodies
        pen_body = QPen(
            _C_BODY, lw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin
        )
        pen_pin = QPen(_C_PIN, lw)
        brush_fill = QBrush(QColor(30, 30, 50))

        for sym in sch.syms:
            sdef = sch.sym_defs.get(sym.lib_id)
            if sdef is None:
                # draw a small cross placeholder
                p.setPen(pen_body)
                cs = 1.0
                p.drawLine(QPointF(sym.x - cs, sym.y), QPointF(sym.x + cs, sym.y))
                p.drawLine(QPointF(sym.x, sym.y - cs), QPointF(sym.x, sym.y + cs))
                continue

            p.save()
            p.translate(sym.x, sym.y)
            p.rotate(-sym.angle)  # KiCad CCW → Qt CW, so negate
            sx_m = -1.0 if sym.mirror_x else 1.0
            sy_m = -1.0 if sym.mirror_y else 1.0
            p.scale(sx_m, sy_m)

            for g in sdef.graphics_for_unit(sym.unit):
                if g.kind == "polyline":
                    if len(g.pts) < 2:
                        continue
                    path = QPainterPath()
                    path.moveTo(g.pts[0][0], g.pts[0][1])
                    for gx, gy in g.pts[1:]:
                        path.lineTo(gx, gy)
                    p.setPen(pen_body)
                    if g.filled:
                        p.setBrush(brush_fill)
                        p.drawPath(path)
                        p.setBrush(Qt.BrushStyle.NoBrush)
                    else:
                        p.drawPath(path)

                elif g.kind == "circle":
                    p.setPen(pen_body)
                    if g.filled:
                        p.setBrush(brush_fill)
                    p.drawEllipse(QPointF(g.cx, g.cy), g.radius, g.radius)
                    if g.filled:
                        p.setBrush(Qt.BrushStyle.NoBrush)

                elif g.kind == "arc" and len(g.pts) == 3:
                    (ax0, ay0), (axm, aym), (ax1, ay1) = g.pts
                    path = _arc_path(ax0, ay0, axm, aym, ax1, ay1)
                    p.setPen(pen_body)
                    p.drawPath(path)

                elif g.kind == "pin":
                    px, py = g.pts[0]
                    pr = math.radians(g.pin_angle)
                    # pin stub goes from tip (connection) toward body
                    bx = px - g.pin_length * math.cos(pr)
                    by_ = py - g.pin_length * math.sin(pr)
                    p.setPen(pen_pin)
                    p.drawLine(QPointF(px, py), QPointF(bx, by_))

            p.restore()

        # 5. Labels & text (drawn in screen coords via helper)
        for lbl in sch.labels:
            color = {
                "local": _C_LABEL,
                "global": _C_GLABEL,
                "hier": _C_GLABEL,
                "power": _C_POWER,
            }.get(lbl.kind, _C_LABEL)
            self._draw_label(p, lbl.x, lbl.y, lbl.angle, lbl.text, color)

        # 6. Ref / value text
        for sym in sch.syms:
            if not sym.hide_ref:
                self._draw_label(p, sym.ref_x, sym.ref_y, sym.ref_angle, sym.reference, _C_REF)
            if not sym.hide_val and sym.value and sym.value != sym.reference:
                self._draw_label(p, sym.val_x, sym.val_y, sym.val_angle, sym.value, _C_VALUE)

        # 7. OP voltage annotations
        if self._show_op and self._op_voltages:
            p.save()
            p.resetTransform()
            font = QFont("Sans Serif")
            font.setPixelSize(max(7, min(12, int(self._scale * 1.2))))
            p.setFont(font)
            p.setPen(QPen(_C_OP_LABEL))
            for lbl in sch.labels:
                key = lbl.text.lower()
                for k, v in self._op_voltages.items():
                    if k.lower() == key or k.lower() == f"v({key})":
                        sx = lbl.x * self._scale + self._pan_x
                        sy = lbl.y * self._scale + self._pan_y
                        p.drawText(QRectF(sx + 2, sy - 14, 80, 14), f"{v:.3g}V")
                        break
            p.restore()

        p.end()

    def _draw_label(
        self, p: QPainter, x_mm: float, y_mm: float, angle_deg: float, text: str, color: QColor
    ) -> None:
        """Draw text at schematic-space (x_mm, y_mm), always legible size."""
        sx = x_mm * self._scale + self._pan_x
        sy = y_mm * self._scale + self._pan_y

        p.save()
        p.resetTransform()
        p.setPen(QPen(color))
        font = QFont("Sans Serif")
        font.setPixelSize(max(7, min(14, int(self._scale * 1.5))))
        p.setFont(font)

        p.translate(sx, sy)
        # Normalise angle so text is never upside-down
        a = angle_deg % 360
        if 90 < a <= 270:
            a -= 180
        p.rotate(-a)

        fm = p.fontMetrics()
        h = fm.height()
        p.drawText(QRectF(1, -h, 200, h * 1.4), text)
        p.restore()


# ---------------------------------------------------------------------------
# Public dock widget
# ---------------------------------------------------------------------------


class SchematicView(QWidget):
    """Read-only KiCad schematic viewer, suitable for embedding in a QDockWidget."""

    net_probed = Signal(str)  # forwarded from canvas

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._canvas = _SchCanvas()
        self._canvas.net_probed.connect(self.net_probed)

        btn_fit = QToolButton()
        btn_fit.setText("Fit")
        btn_fit.setFixedWidth(36)
        btn_in = QToolButton()
        btn_in.setText("+")
        btn_in.setFixedWidth(28)
        btn_out = QToolButton()
        btn_out.setText("−")
        btn_out.setFixedWidth(28)
        self._info = QLabel()
        self._info.setStyleSheet("color: #808080; font-size: 9pt;")

        self._btn_op = QToolButton()
        self._btn_op.setText("OP")
        self._btn_op.setCheckable(True)
        self._btn_op.setToolTip("Toggle operating-point voltage overlays")
        self._btn_op.toggled.connect(self._on_op_toggled)

        btn_fit.clicked.connect(self._canvas.fit)
        btn_in.clicked.connect(self._canvas.zoom_in)
        btn_out.clicked.connect(self._canvas.zoom_out)

        tb = QHBoxLayout()
        tb.setContentsMargins(4, 2, 4, 2)
        tb.setSpacing(4)
        for w in (btn_fit, btn_in, btn_out, self._btn_op, self._info):
            tb.addWidget(w)
        tb.addStretch()

        tb_widget = QWidget()
        tb_widget.setLayout(tb)

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

    def set_op_voltages(self, voltages: dict[str, float]) -> None:
        self._canvas.set_op_voltages(voltages, show=self._btn_op.isChecked())

    def _on_op_toggled(self, checked: bool) -> None:
        self._canvas._show_op = checked
        self._canvas.update()
        self._info.setText("")

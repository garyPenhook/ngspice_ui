"""
Thread-safe callback trampolines for libngspice.

ngspice fires C callbacks from its background simulation thread.
These trampolines do the minimum work — decode bytes, pack a lightweight
dataclass — then push to a thread-safe queue consumed by the GUI thread.
No Qt imports here; this module stays headless.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass, field
from typing import Any

import ctypes

from .bindings import (
    CB_BGThreadRunning,
    CB_ControlledExit,
    CB_SendChar,
    CB_SendData,
    CB_SendInitData,
    CB_SendStat,
    VecInfoAll,
    VecValuesAll,
)


# ---------------------------------------------------------------------------
# Event dataclasses
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CharEvent:
    """One line of stdout/stderr from ngspice."""
    line: str


@dataclass(slots=True)
class StatEvent:
    """Simulation status message with completion percentage."""
    message: str
    percent: int          # 0–100 (parsed from the string)


@dataclass(slots=True)
class ExitEvent:
    """ngspice is requesting process exit."""
    status: int
    immediate: bool
    on_quit: bool


@dataclass(slots=True)
class DataPointEvent:
    """Values of all output vectors at one accepted simulation time step."""
    vec_index: int
    values: dict[str, complex]  # name → real (+ 0j) or complex
    scale_name: str


@dataclass(slots=True)
class InitDataEvent:
    """Vector metadata emitted once when a simulation starts."""
    plot_name: str
    plot_title: str
    plot_type: str
    vector_names: list[str]
    real_flags: list[bool]      # parallel to vector_names


@dataclass(slots=True)
class BGThreadEvent:
    """Background simulation thread started or stopped."""
    running: bool


SimEvent = CharEvent | StatEvent | ExitEvent | DataPointEvent | InitDataEvent | BGThreadEvent


# ---------------------------------------------------------------------------
# Callback builder
# ---------------------------------------------------------------------------

def build_callbacks(
    event_queue: "queue.Queue[SimEvent]",
) -> dict[str, Any]:
    """Return a dict of ctypes callback objects keyed by their argtypes name.

    All returned objects MUST be kept alive (referenced) for as long as
    ngSpice_Init's callbacks are registered — ctypes does not keep a reference.
    Store the returned dict on the owning session object.
    """

    def _send_char(msg: bytes, _ident: int, _user: Any) -> int:
        line = msg.decode("utf-8", errors="replace").rstrip()
        event_queue.put_nowait(CharEvent(line=line))
        return 0

    def _send_stat(msg: bytes, _ident: int, _user: Any) -> int:
        text = msg.decode("utf-8", errors="replace").strip()
        # ngspice format: "<message> <percent>%" or just "<message>"
        percent = 0
        if text.endswith("%"):
            parts = text.rsplit(None, 1)
            if len(parts) == 2:
                try:
                    percent = int(parts[1].rstrip("%"))
                    text = parts[0]
                except ValueError:
                    pass
        event_queue.put_nowait(StatEvent(message=text, percent=percent))
        return 0

    def _controlled_exit(
        status: int, immediate: bool, on_quit: bool, _ident: int, _user: Any
    ) -> int:
        event_queue.put_nowait(ExitEvent(status=status, immediate=immediate, on_quit=on_quit))
        return 0

    def _send_data(raw_ptr: int, _count: int, _ident: int, _user: Any) -> int:
        if not raw_ptr:
            return 0
        p_all = ctypes.cast(raw_ptr, ctypes.POINTER(VecValuesAll))
        all_ = p_all.contents
        values: dict[str, complex] = {}
        scale_name = ""
        for i in range(all_.veccount):
            v = all_.vecsa[i].contents
            name = v.name.decode("utf-8", errors="replace") if v.name else ""
            val = complex(v.creal, v.cimag) if v.is_complex else complex(v.creal, 0.0)
            values[name] = val
            if v.is_scale:
                scale_name = name
        event_queue.put_nowait(
            DataPointEvent(vec_index=all_.vecindex, values=values, scale_name=scale_name)
        )
        return 0

    def _send_init_data(raw_ptr: int, _ident: int, _user: Any) -> int:
        if not raw_ptr:
            return 0
        p_all = ctypes.cast(raw_ptr, ctypes.POINTER(VecInfoAll))
        all_ = p_all.contents
        names: list[str] = []
        real_flags: list[bool] = []
        for i in range(all_.veccount):
            vi = all_.vecs[i].contents
            names.append(vi.vecname.decode("utf-8", errors="replace") if vi.vecname else "")
            real_flags.append(bool(vi.is_real))
        event_queue.put_nowait(
            InitDataEvent(
                plot_name=all_.name.decode() if all_.name else "",
                plot_title=all_.title.decode() if all_.title else "",
                plot_type=all_.type.decode() if all_.type else "",
                vector_names=names,
                real_flags=real_flags,
            )
        )
        return 0

    def _bg_thread_running(running: bool, _ident: int, _user: Any) -> int:
        event_queue.put_nowait(BGThreadEvent(running=bool(running)))
        return 0

    return {
        "send_char":        CB_SendChar(_send_char),
        "send_stat":        CB_SendStat(_send_stat),
        "controlled_exit":  CB_ControlledExit(_controlled_exit),
        "send_data":        CB_SendData(_send_data),
        "send_init_data":   CB_SendInitData(_send_init_data),
        "bg_thread_running": CB_BGThreadRunning(_bg_thread_running),
    }

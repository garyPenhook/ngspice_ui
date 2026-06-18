"""
NgSpiceSession — Pythonic wrapper over libngspice.

One instance per process (libngspice is a global singleton).
Thread-safety: public methods may be called from the GUI thread;
callbacks fire on ngspice's background thread and push to event_queue.
"""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import c_char_p, c_int
from typing import Callable

import numpy as np

from .bindings import NgSpiceNotFoundError, get_lib  # noqa: F401 (re-exported)
from .callbacks import SimEvent, build_callbacks


class NgSpiceSession:
    """Manages a single libngspice instance.

    Parameters
    ----------
    suppress_spinit:
        If True, suppress reading the global spice init file (spinit).
    suppress_spiceinit:
        If True, suppress reading the user's .spiceinit file.
    """

    _instance_lock = threading.Lock()
    _instance: "NgSpiceSession | None" = None

    def __new__(cls, *args, **kwargs):
        with cls._instance_lock:
            if cls._instance is not None:
                raise RuntimeError(
                    "Only one NgSpiceSession may exist per process. "
                    "libngspice is a global singleton. "
                    "Call NgSpiceSession.get() to retrieve the existing instance."
                )
            inst = super().__new__(cls)
            cls._instance = inst
            return inst

    @classmethod
    def get(cls) -> "NgSpiceSession":
        """Return the existing session, raising if none has been created."""
        with cls._instance_lock:
            if cls._instance is None:
                raise RuntimeError("No NgSpiceSession exists. Create one first.")
            return cls._instance

    def __init__(
        self,
        suppress_spinit: bool = False,
        suppress_spiceinit: bool = False,
    ) -> None:
        self._lib = get_lib()
        self.event_queue: queue.Queue[SimEvent] = queue.Queue()

        # Must keep callback objects alive for the entire session lifetime
        self._callbacks = build_callbacks(self.event_queue)

        ret = self._lib.ngSpice_Init(
            self._callbacks["send_char"],
            self._callbacks["send_stat"],
            self._callbacks["controlled_exit"],
            self._callbacks["send_data"],
            self._callbacks["send_init_data"],
            self._callbacks["bg_thread_running"],
            None,  # userData
        )
        if ret != 0:
            raise RuntimeError(f"ngSpice_Init returned {ret}")

        # nospinit/nospiceinit must be called after Init (they touch Init-allocated state)
        if suppress_spinit:
            self._lib.ngSpice_nospinit()
        if suppress_spiceinit:
            self._lib.ngSpice_nospiceinit()

    # ------------------------------------------------------------------
    # Netlist loading
    # ------------------------------------------------------------------

    def load_netlist(self, lines: list[str]) -> None:
        """Load a circuit from a list of netlist lines.

        Halts any running background simulation first — ngspice's bg thread
        must fully exit before ngSpice_Circ is safe to call.

        The list must contain the netlist body; a trailing '.end' is added
        automatically if absent. The array is NULL-terminated for the C API.
        """
        self._safe_halt()

        clean = [ln.rstrip() for ln in lines]
        if not clean or clean[-1].strip().lower() != ".end":
            clean.append(".end")

        # Build array of c_char_p, NULL-terminated
        arr_type = c_char_p * (len(clean) + 1)
        arr = arr_type(*(ln.encode("utf-8") for ln in clean), None)
        ret = self._lib.ngSpice_Circ(arr)
        if ret != 0:
            raise RuntimeError(f"ngSpice_Circ returned {ret}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _safe_halt(self, timeout: float = 3.0) -> None:
        """Ensure no background simulation is running before calling ngSpice_Circ.

        Raises RuntimeError if the bg thread does not stop within *timeout* seconds,
        because calling ngSpice_Circ against a live thread risks libngspice races.
        """
        import time
        if not self._lib.ngSpice_running():
            return
        self._lib.ngSpice_Command(b"bg_halt")
        deadline = time.monotonic() + timeout
        while self._lib.ngSpice_running() and time.monotonic() < deadline:
            time.sleep(0.05)
        if self._lib.ngSpice_running():
            raise RuntimeError(
                "bg simulation did not stop within timeout; "
                "cannot safely load a new netlist"
            )

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def command(self, cmd: str) -> None:
        """Send any ngspice command string (executed immediately)."""
        ret = self._lib.ngSpice_Command(cmd.encode("utf-8"))
        if ret != 0:
            raise RuntimeError(f"ngSpice_Command({cmd!r}) returned {ret}")

    def run(self) -> None:
        """Start a foreground simulation (blocks until done)."""
        self.command("run")

    def bg_run(self) -> None:
        """Start simulation in ngspice's background thread (non-blocking)."""
        self.command("bg_run")

    def bg_halt(self) -> None:
        """Pause a running background simulation."""
        self.command("bg_halt")

    def bg_resume(self) -> None:
        """Resume a paused background simulation."""
        self.command("bg_resume")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """True if a background simulation thread is active."""
        return bool(self._lib.ngSpice_running())

    def set_breakpoint(self, time: float) -> bool:
        """Set a transient simulation breakpoint at *time* (seconds)."""
        return bool(self._lib.ngSpice_SetBkpt(time))

    # ------------------------------------------------------------------
    # Data access
    # ------------------------------------------------------------------

    def current_plot(self) -> str:
        """Return the name of the current (most recently run) plot."""
        name = self._lib.ngSpice_CurPlot()
        return name.decode("utf-8") if name else ""

    def all_plots(self) -> list[str]:
        """Return names of all plots created so far."""
        ptr = self._lib.ngSpice_AllPlots()
        return _char_pp_to_list(ptr)

    def all_vecs(self, plot: str) -> list[str]:
        """Return vector names in *plot*."""
        ptr = self._lib.ngSpice_AllVecs(plot.encode("utf-8"))
        return _char_pp_to_list(ptr)

    def get_vector(self, vecname: str) -> "VectorData":
        """Fetch a vector by name (e.g. 'tran1.v(out)' or 'v(out)').

        Acquires the realloc lock while reading so the bg thread cannot
        resize the buffer mid-read.
        """
        self._lib.ngSpice_LockRealloc()
        try:
            p = self._lib.ngGet_Vec_Info(vecname.encode("utf-8"))
            if not p:
                raise KeyError(f"Vector not found: {vecname!r}")
            return VectorData.from_c(p.contents)
        finally:
            self._lib.ngSpice_UnlockRealloc()

    # ------------------------------------------------------------------
    # Co-simulation
    # ------------------------------------------------------------------

    def init_sync(
        self,
        vsrc_fn: "Callable[[float, str], float] | None" = None,
        isrc_fn: "Callable[[float, str], float] | None" = None,
        sync_fn: "Callable[[float, float], float | None] | None" = None,
    ) -> None:
        """Register Python callables as co-simulation callbacks.

        vsrc_fn(time, srcname) -> float
            Called by ngspice to obtain the voltage (V) for an external
            voltage source named *srcname* at simulation time *time*.

        isrc_fn(time, srcname) -> float
            Same for an external current source (A).

        sync_fn(actual_time, old_delta) -> float | None
            Delta-time negotiation hook.  Return a proposed shorter step
            (in seconds) or None to accept ngspice's own choice.

        Pass all None to register no-op callbacks (effectively disables
        any previously registered co-sim functions without unloading the
        interface).

        Netlist side: declare external sources with the ``external`` keyword, e.g.::

            Vext n1 n2 external

        ngspice will call vsrc_fn whenever it needs the value of 'vext'.
        Using ``dc 0`` instead of ``external`` produces no callbacks.

        The ctypes callback objects are kept alive on this session instance
        for the duration of the process (libngspice holds raw C pointers).
        """
        from .bindings import CB_GetISRCData, CB_GetSyncData, CB_GetVSRCData

        def _vsrc(voltage_ptr, time, srcname, srcindex, userdata):
            if vsrc_fn is not None:
                try:
                    name = srcname.decode("utf-8") if srcname else ""
                    voltage_ptr[0] = float(vsrc_fn(float(time), name))
                except Exception:
                    pass
            return 0

        def _isrc(current_ptr, time, srcname, srcindex, userdata):
            if isrc_fn is not None:
                try:
                    name = srcname.decode("utf-8") if srcname else ""
                    current_ptr[0] = float(isrc_fn(float(time), name))
                except Exception:
                    pass
            return 0

        def _sync(actual_time, delta_ptr, old_delta, index, is_diff, nm, userdata):
            if sync_fn is not None:
                try:
                    result = sync_fn(float(actual_time), float(old_delta))
                    if result is not None:
                        delta_ptr[0] = float(result)
                except Exception:
                    pass
            return 0

        vsrc_c = CB_GetVSRCData(_vsrc)
        isrc_c = CB_GetISRCData(_isrc)
        sync_c = CB_GetSyncData(_sync)
        # Must keep alive — libngspice holds raw C function pointers
        self._sync_callbacks = (vsrc_c, isrc_c, sync_c)

        ident = c_int(0)
        ret = self._lib.ngSpice_Init_Sync(
            vsrc_c, isrc_c, sync_c, ctypes.byref(ident), None
        )
        if ret != 0:
            raise RuntimeError(f"ngSpice_Init_Sync returned {ret}")

    # ------------------------------------------------------------------
    # Reset / cleanup
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset ngspice state as much as possible."""
        self._lib.ngSpice_Reset()

    def __del__(self) -> None:
        # Best-effort; libngspice has no explicit teardown API
        try:
            if self._lib.ngSpice_running():
                self._lib.ngSpice_Command(b"bg_halt")
        except Exception:
            pass
        with NgSpiceSession._instance_lock:
            NgSpiceSession._instance = None


# ---------------------------------------------------------------------------
# VectorData helper
# ---------------------------------------------------------------------------


class VectorData:
    """A named vector pulled from libngspice, backed by numpy arrays."""

    __slots__ = ("name", "v_type", "v_flags", "data", "is_complex")

    def __init__(
        self,
        name: str,
        v_type: int,
        v_flags: int,
        data: np.ndarray,
        is_complex: bool,
    ) -> None:
        self.name = name
        self.v_type = v_type
        self.v_flags = v_flags
        self.data = data          # float64 or complex128
        self.is_complex = is_complex

    @classmethod
    def from_c(cls, vi) -> "VectorData":
        """Construct from a VectorInfo ctypes struct (must be lock-held)."""
        name = vi.v_name.decode("utf-8", errors="replace") if vi.v_name else ""
        n = vi.v_length
        if vi.v_compdata:
            raw = np.ctypeslib.as_array(vi.v_compdata, shape=(n,))
            data = raw["cx_real"].astype(np.float64) + 1j * raw["cx_imag"].astype(np.float64)
            is_complex = True
        else:
            data = np.ctypeslib.as_array(vi.v_realdata, shape=(n,)).copy()
            is_complex = False
        return cls(name=name, v_type=vi.v_type, v_flags=vi.v_flags,
                   data=data, is_complex=is_complex)

    def __repr__(self) -> str:
        return f"VectorData({self.name!r}, len={len(self.data)}, complex={self.is_complex})"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _char_pp_to_list(ptr) -> list[str]:
    """Convert a NULL-terminated char** to a Python list of strings."""
    if not ptr:
        return []
    result = []
    i = 0
    while ptr[i] is not None:
        result.append(ptr[i].decode("utf-8", errors="replace"))
        i += 1
    return result

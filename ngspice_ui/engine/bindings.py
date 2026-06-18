"""
Raw ctypes bindings for libngspice (sharedspice.h).

Library location is never hardcoded: resolved via NGSPICE_LIB env var,
ctypes.util.find_library, or platform-specific fallback globs.
"""

import ctypes
import ctypes.util
import glob
import os
import platform
import subprocess
import sys
from ctypes import (
    CFUNCTYPE,
    POINTER,
    Structure,
    c_bool,
    c_char_p,
    c_double,
    c_int,
    c_short,
    c_void_p,
)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

class NgSpiceNotFoundError(RuntimeError):
    """Raised when libngspice cannot be located."""


def _find_lib_path() -> str:
    """Return a path (or bare name) that ctypes.CDLL can load.

    Resolution order:
    1. NGSPICE_LIB environment variable (absolute path or name).
    2. ctypes.util.find_library("ngspice") — uses ldconfig / dyld / PE search.
    3. Platform-specific fallback globs.
    4. Raise NgSpiceNotFoundError with an actionable message.
    """
    tried: list[str] = []

    # 1. Env var override
    env_val = os.environ.get("NGSPICE_LIB", "").strip()
    if env_val:
        tried.append(f"NGSPICE_LIB={env_val!r}")
        return env_val

    # 2. System search (ldconfig / dyld / PE)
    found = ctypes.util.find_library("ngspice")
    if found:
        return found
    tried.append("ctypes.util.find_library('ngspice') → None")

    # 3. Fallback globs per platform
    system = platform.system()
    candidates: list[str] = []

    if system == "Linux":
        prefixes = ["/usr", "/usr/local"]
        # Also honour the active Conda/venv prefix
        conda = os.environ.get("CONDA_PREFIX") or sys.prefix
        if conda and conda not in prefixes:
            prefixes.append(conda)
        for p in prefixes:
            candidates += glob.glob(f"{p}/lib*/libngspice.so*")
            candidates += glob.glob(f"{p}/lib/*/libngspice.so*")

    elif system == "Darwin":
        # Try Homebrew prefix if brew is on PATH
        try:
            brew_prefix = subprocess.check_output(
                ["brew", "--prefix", "ngspice"], stderr=subprocess.DEVNULL, text=True
            ).strip()
            candidates += glob.glob(f"{brew_prefix}/lib/libngspice*.dylib")
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
        for base in ["/usr/local/lib", "/opt/homebrew/lib", "/opt/local/lib"]:
            candidates += glob.glob(f"{base}/libngspice*.dylib")

    elif system == "Windows":
        prog = os.environ.get("ProgramFiles", r"C:\Program Files")
        prog86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        for base in [prog, prog86]:
            candidates += glob.glob(rf"{base}\Spice*\bin\ngspice.dll")
            candidates += glob.glob(rf"{base}\ngspice*\bin\ngspice.dll")
            candidates += glob.glob(rf"{base}\ngspice*\Spice_64\bin\ngspice.dll")

    # Return the first existing candidate
    for path in candidates:
        if os.path.isfile(path):
            return path

    tried.append(f"platform fallback globs ({system}): no match in {candidates!r}")

    raise NgSpiceNotFoundError(
        "Could not locate libngspice.\n\n"
        "Tried:\n" + "\n".join(f"  • {t}" for t in tried) + "\n\n"
        "Fixes:\n"
        "  • Install ngspice (e.g. `apt install ngspice libngspice0-dev`,\n"
        "    `brew install ngspice`, or https://ngspice.sourceforge.io/download.html)\n"
        "  • Set NGSPICE_LIB=/path/to/libngspice.so (or .dylib / .dll) and retry."
    )


def _load_lib() -> ctypes.CDLL:
    """Load and return the libngspice shared library."""
    path = _find_lib_path()
    try:
        lib = ctypes.CDLL(path)
    except OSError as exc:
        raise NgSpiceNotFoundError(
            f"Found libngspice at {path!r} but could not load it: {exc}\n"
            "Check that the file is a valid shared library for this architecture."
        ) from exc
    return lib


# ---------------------------------------------------------------------------
# Structs  (mirroring sharedspice.h exactly)
# ---------------------------------------------------------------------------

class NgComplex(Structure):
    _fields_ = [
        ("cx_real", c_double),
        ("cx_imag", c_double),
    ]


class VectorInfo(Structure):
    """struct vector_info — returned by ngGet_Vec_Info."""
    _fields_ = [
        ("v_name",     c_char_p),
        ("v_type",     c_int),
        ("v_flags",    c_short),
        ("v_realdata", POINTER(c_double)),
        ("v_compdata", POINTER(NgComplex)),
        ("v_length",   c_int),
    ]


class VecValues(Structure):
    """struct vecvalues — one vector's value at a single time step."""
    _fields_ = [
        ("name",       c_char_p),
        ("creal",      c_double),
        ("cimag",      c_double),
        ("is_scale",   c_bool),
        ("is_complex", c_bool),
    ]


class VecValuesAll(Structure):
    """struct vecvaluesall — all vectors' values at a single accepted point."""
    _fields_ = [
        ("veccount", c_int),
        ("vecindex", c_int),
        ("vecsa",    POINTER(POINTER(VecValues))),
    ]


class VecInfo(Structure):
    """struct vecinfo — metadata for one vector in a plot."""
    _fields_ = [
        ("number",     c_int),
        ("vecname",    c_char_p),
        ("is_real",    c_bool),
        ("pdvec",      c_void_p),
        ("pdvecscale", c_void_p),
    ]


class VecInfoAll(Structure):
    """struct vecinfoall — metadata for all vectors in a plot, at sim start."""
    _fields_ = [
        ("name",     c_char_p),
        ("title",    c_char_p),
        ("date",     c_char_p),
        ("type",     c_char_p),
        ("veccount", c_int),
        ("vecs",     POINTER(POINTER(VecInfo))),
    ]


# ---------------------------------------------------------------------------
# Callback function types  (from sharedspice.h typedef section)
# ---------------------------------------------------------------------------

#: stdout/stderr output from ngspice
CB_SendChar       = CFUNCTYPE(c_int, c_char_p, c_int, c_void_p)

#: simulation status string + percent
CB_SendStat       = CFUNCTYPE(c_int, c_char_p, c_int, c_void_p)

#: ngspice requesting process exit
CB_ControlledExit = CFUNCTYPE(c_int, c_int, c_bool, c_bool, c_int, c_void_p)

#: per-accepted-point vector data during simulation
#  Receive as c_void_p; callbacks cast to the correct struct pointer themselves.
CB_SendData       = CFUNCTYPE(c_int, c_void_p, c_int, c_int, c_void_p)

#: vector metadata emitted once at simulation start
CB_SendInitData   = CFUNCTYPE(c_int, c_void_p, c_int, c_void_p)

#: background thread running/stopped notification
CB_BGThreadRunning = CFUNCTYPE(c_int, c_bool, c_int, c_void_p)

#: external voltage source value request
CB_GetVSRCData    = CFUNCTYPE(c_int, POINTER(c_double), c_double, c_char_p, c_int, c_void_p)

#: external current source value request
CB_GetISRCData    = CFUNCTYPE(c_int, POINTER(c_double), c_double, c_char_p, c_int, c_void_p)

#: synchronisation callback (delta time negotiation)
CB_GetSyncData    = CFUNCTYPE(
    c_int, c_double, POINTER(c_double), c_double, c_int, c_int, c_int, c_void_p
)


# ---------------------------------------------------------------------------
# Prototype wiring
# ---------------------------------------------------------------------------

def _wire_prototypes(lib: ctypes.CDLL) -> None:
    """Set argtypes/restype on every exported ngSpice_* function."""

    lib.ngSpice_Init.restype  = c_int
    lib.ngSpice_Init.argtypes = [
        CB_SendChar, CB_SendStat, CB_ControlledExit,
        CB_SendData, CB_SendInitData, CB_BGThreadRunning,
        c_void_p,
    ]

    lib.ngSpice_Init_Sync.restype  = c_int
    lib.ngSpice_Init_Sync.argtypes = [
        CB_GetVSRCData, CB_GetISRCData, CB_GetSyncData,
        POINTER(c_int), c_void_p,
    ]

    lib.ngSpice_Command.restype  = c_int
    lib.ngSpice_Command.argtypes = [c_char_p]

    lib.ngGet_Vec_Info.restype  = POINTER(VectorInfo)
    lib.ngGet_Vec_Info.argtypes = [c_char_p]

    lib.ngSpice_Circ.restype  = c_int
    lib.ngSpice_Circ.argtypes = [POINTER(c_char_p)]

    lib.ngSpice_CurPlot.restype  = c_char_p
    lib.ngSpice_CurPlot.argtypes = []

    lib.ngSpice_AllPlots.restype  = POINTER(c_char_p)
    lib.ngSpice_AllPlots.argtypes = []

    lib.ngSpice_AllVecs.restype  = POINTER(c_char_p)
    lib.ngSpice_AllVecs.argtypes = [c_char_p]

    lib.ngSpice_running.restype  = c_bool
    lib.ngSpice_running.argtypes = []

    lib.ngSpice_SetBkpt.restype  = c_bool
    lib.ngSpice_SetBkpt.argtypes = [c_double]

    lib.ngSpice_Reset.restype  = c_int
    lib.ngSpice_Reset.argtypes = []

    lib.ngSpice_nospinit.restype  = c_int
    lib.ngSpice_nospinit.argtypes = []

    lib.ngSpice_nospiceinit.restype  = c_int
    lib.ngSpice_nospiceinit.argtypes = []

    lib.ngSpice_LockRealloc.restype  = c_int
    lib.ngSpice_LockRealloc.argtypes = []

    lib.ngSpice_UnlockRealloc.restype  = c_int
    lib.ngSpice_UnlockRealloc.argtypes = []


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_lib: ctypes.CDLL | None = None


def get_lib() -> ctypes.CDLL:
    """Return the loaded, prototype-wired libngspice (loaded once)."""
    global _lib
    if _lib is None:
        _lib = _load_lib()
        _wire_prototypes(_lib)
    return _lib

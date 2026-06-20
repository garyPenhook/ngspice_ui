"""Integration tests for NgSpiceSession against a real libngspice."""

import ctypes.util
import queue
import time

import pytest

from ngspice_ui.engine.callbacks import BGThreadEvent, CharEvent
from ngspice_ui.engine.session import NgSpiceSession

pytestmark = pytest.mark.skipif(
    ctypes.util.find_library("ngspice") is None,
    reason="libngspice not installed on this system",
)

RC_NETLIST = [
    "RC Low-Pass Filter test",
    "V1 in 0 AC 1",
    "R1 in out 1k",
    "C1 out 0 159.15n",
]


@pytest.fixture(scope="module")
def session():
    """Single session shared across this module; cleaned up at the end."""
    s = NgSpiceSession()
    yield s
    s.reset()
    # Tear down the singleton so other test modules can create a fresh one
    NgSpiceSession._instance = None


def test_second_session_raises(session):
    with pytest.raises(RuntimeError, match="singleton"):
        NgSpiceSession()


def test_get_returns_same(session):
    assert NgSpiceSession.get() is session


def test_load_netlist(session):
    session.load_netlist(RC_NETLIST + [".op"])


def test_op_runs_and_emits_output(session):
    session.load_netlist(RC_NETLIST + [".op"])
    session.run()
    # Drain the queue; we should have at least one CharEvent
    events = []
    try:
        while True:
            events.append(session.event_queue.get_nowait())
    except queue.Empty:
        pass
    assert any(isinstance(e, CharEvent) for e in events), "No CharEvents from .op run"


def test_op_dc_vector(session):
    session.load_netlist(RC_NETLIST + [".op"])
    session.run()
    # Drain queue
    try:
        while True:
            session.event_queue.get_nowait()
    except queue.Empty:
        pass
    cur = session.current_plot()
    assert cur, "current_plot() returned empty string"
    vecs = session.all_vecs(cur)
    assert len(vecs) > 0, "No vectors in op plot"


def test_tran_bg_run(session):
    netlist = RC_NETLIST + [".tran 10u 1m"]
    session.load_netlist(netlist)
    session.bg_run()
    # Wait up to 5 s for BGThreadEvent(running=False)
    deadline = time.time() + 5
    finished = False
    while time.time() < deadline:
        try:
            evt = session.event_queue.get(timeout=0.1)
            if isinstance(evt, BGThreadEvent) and not evt.running:
                finished = True
                break
        except queue.Empty:
            pass
    assert finished, "Background transient simulation did not complete within 5 s"


def test_tran_vector_shape(session):
    """After a .tran, the time vector must have > 1 point."""
    session.load_netlist(RC_NETLIST + [".tran 10u 1m"])
    session.run()
    cur = session.current_plot()
    time_vec = session.get_vector(f"{cur}.time")
    assert len(time_vec.data) > 1
    assert not time_vec.is_complex


def test_bg_run_resets_cosim_report_flags(session, monkeypatch):
    # Regression: co-sim failure flags must reset at each run boundary, so a
    # source that failed on run 1 re-reports on run 2 instead of silently forcing
    # 0 V/A. Stub command() so this only exercises the reset, not a real sim.
    session._cosim_reported = {"v": True, "i": True, "s": True}
    monkeypatch.setattr(session, "command", lambda cmd: None)
    session.bg_run()
    assert session._cosim_reported == {"v": False, "i": False, "s": False}


def test_ac_complex_vector(session):
    session.load_netlist(RC_NETLIST + [".ac dec 5 10 100k"])
    session.run()
    cur = session.current_plot()
    # v(out) in AC is complex
    out_vec = session.get_vector(f"{cur}.v(out)")
    assert out_vec.is_complex
    # At 10 Hz (well below 1 kHz corner) magnitude should be close to 1
    assert abs(abs(out_vec.data[0]) - 1.0) < 0.05, "AC magnitude at 10 Hz too far from 1"
    # At 100 kHz (100× above corner) magnitude should be much less than 1
    assert abs(out_vec.data[-1]) < 0.1, "AC magnitude at 100 kHz should be attenuated"

"""Single source of truth: analysis type → ngspice command and metadata."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParamSpec:
    name: str
    label: str
    placeholder: str = ""
    kind: str = "text"              # "text" | "choice"
    choices: tuple[str, ...] = ()
    default: str = ""
    required: bool = True           # False → omit from validate() checks


@dataclass(frozen=True)
class AnalysisSpec:
    label: str
    command: str        # template; .format(**kwargs) before use; prefix "." for netlist
    scale_name: str     # expected x-axis vector name in the result plot
    params: tuple[ParamSpec, ...] = ()


ANALYSES: dict[str, AnalysisSpec] = {
    "tran": AnalysisSpec(
        label="Transient",
        command="tran {tstep} {tstop}",   # extended by panel for tmax/uic
        scale_name="time",
        params=(
            ParamSpec("tstep", "Time step", placeholder="1us"),
            ParamSpec("tstop", "Stop time", placeholder="1ms"),
            ParamSpec("tmax", "Max step", placeholder="(optional)", required=False),
            ParamSpec("uic", "Use IC", kind="choice",
                      choices=("No", "Yes"), default="No", required=False),
        ),
    ),
    "ac": AnalysisSpec(
        label="AC Sweep",
        command="ac {variation} {points} {fstart} {fstop}",
        scale_name="frequency",
        params=(
            ParamSpec("variation", "Scale", kind="choice",
                      choices=("dec", "oct", "lin"), default="dec"),
            ParamSpec("points", "Points/dec", placeholder="10"),
            ParamSpec("fstart", "Start freq", placeholder="1Hz"),
            ParamSpec("fstop", "Stop freq", placeholder="1MEG"),
        ),
    ),
    "dc": AnalysisSpec(
        label="DC Sweep",
        command="dc {src} {vstart} {vstop} {vincr}",  # extended by panel for 2nd source
        scale_name="v-sweep",
        params=(
            ParamSpec("src", "Source", placeholder="V1"),
            ParamSpec("vstart", "Start", placeholder="0"),
            ParamSpec("vstop", "Stop", placeholder="5"),
            ParamSpec("vincr", "Step", placeholder="0.1"),
            ParamSpec("src2", "2nd Source", placeholder="(optional)", required=False),
            ParamSpec("vstart2", "2nd Start", placeholder="0", required=False),
            ParamSpec("vstop2", "2nd Stop", placeholder="5", required=False),
            ParamSpec("vincr2", "2nd Step", placeholder="1", required=False),
        ),
    ),
    "op": AnalysisSpec(
        label="Operating Point",
        command="op",
        scale_name="",
        params=(),
    ),
    "noise": AnalysisSpec(
        label="Noise",
        command="noise v({output}) {src} {variation} {points} {fstart} {fstop}",
        scale_name="frequency",
        params=(
            ParamSpec("output", "Output node", placeholder="out"),
            ParamSpec("src", "Input source", placeholder="V1"),
            ParamSpec("variation", "Scale", kind="choice",
                      choices=("dec", "oct", "lin"), default="dec"),
            ParamSpec("points", "Points/dec", placeholder="10"),
            ParamSpec("fstart", "Start freq", placeholder="1Hz"),
            ParamSpec("fstop", "Stop freq", placeholder="1MEG"),
        ),
    ),
    "tf": AnalysisSpec(
        label="Transfer Function",
        command="tf v({output}) {src}",
        scale_name="",
        params=(
            ParamSpec("output", "Output node", placeholder="out"),
            ParamSpec("src", "Input source", placeholder="V1"),
        ),
    ),
    "sens": AnalysisSpec(
        label="Sensitivity",
        command="sens v({output})",
        scale_name="",
        params=(
            ParamSpec("output", "Output node", placeholder="out"),
        ),
    ),
    "pz": AnalysisSpec(
        label="Pole-Zero",
        command="pz {node1} {node2} {node3} {node4} {circuit_type} {analysis_type}",
        scale_name="",
        params=(
            ParamSpec("node1", "Node 1 (+in)", placeholder="in"),
            ParamSpec("node2", "Node 2 (−in)", placeholder="0"),
            ParamSpec("node3", "Node 3 (+out)", placeholder="out"),
            ParamSpec("node4", "Node 4 (−out)", placeholder="0"),
            ParamSpec("circuit_type", "Circuit", kind="choice",
                      choices=("vol", "cur"), default="vol"),
            ParamSpec("analysis_type", "Analysis", kind="choice",
                      choices=("pz", "zer", "pol"), default="pz"),
        ),
    ),
    "disto": AnalysisSpec(
        label="Distortion",
        command="disto {variation} {points} {fstart} {fstop}",
        scale_name="frequency",
        params=(
            ParamSpec("variation", "Scale", kind="choice",
                      choices=("dec", "oct", "lin"), default="dec"),
            ParamSpec("points", "Points/dec", placeholder="10"),
            ParamSpec("fstart", "Start freq", placeholder="1Hz"),
            ParamSpec("fstop", "Stop freq", placeholder="1MEG"),
        ),
    ),
}

# Canonical key order for UI display
ANALYSIS_KEY_ORDER: tuple[str, ...] = (
    "tran", "ac", "dc", "op", "noise", "tf", "sens", "pz", "disto"
)

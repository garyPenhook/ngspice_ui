"""Pure Monte Carlo netlist generation — no GUI dependency."""

from __future__ import annotations

import re

import numpy as np

# Regex: number + optional SPICE multiplier + optional trailing unit text.
# MEG must be tried before M/m to avoid matching the 'M' in 'MEG'.
# The trailing [a-zA-Z]* captures unit strings like 'F', 'H', 'Ohm', 'Hz' that
# ngspice ignores when the multiplier has already been parsed.
_SPICE_VAL_RE = re.compile(
    r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)"  # numeric part
    r"\s*(MEG|[TGKkmMuUnNpPfF])?"  # optional multiplier
    r"[a-zA-Z]*$"  # optional unit (ignored)
)

# ngspice convention: M = milli (1e-3); MEG = mega (1e6).
_SUFFIX_MAP: dict[str, float] = {
    "T": 1e12,
    "G": 1e9,
    "MEG": 1e6,
    "K": 1e3,
    "k": 1e3,
    "m": 1e-3,
    "M": 1e-3,
    "u": 1e-6,
    "U": 1e-6,
    "n": 1e-9,
    "N": 1e-9,
    "p": 1e-12,
    "P": 1e-12,
    "f": 1e-15,
    "F": 1e-15,
}


def insert_before_end(netlist: str, *directives: str) -> str:
    """Return *netlist* with *directives* inserted just before the first ``.end``.

    Appending after ``.end`` is fragile (``.end`` terminates the deck for most
    analyses), so swept directives like ``.param``/``.temp`` are placed before
    it — and after any of the base netlist's own definitions, so they override
    by ngspice's last-assignment-wins rule. If there is no ``.end``, the
    directives are appended.
    """
    if not directives:
        return netlist
    lines = netlist.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().lower() == ".end":
            return "\n".join([*lines[:i], *directives, *lines[i:]])
    return "\n".join([*lines, *directives])


def parse_spice_val(s: str) -> float:
    """Convert a SPICE value string to float.

    Handles multiplier suffixes (k, MEG, u, n, p, …) and ignores trailing
    unit text (F, H, Ohm, Hz, …).  Examples: '10k', '2.2uF', '100MEG', '1k5'
    all parse correctly.  Raises ValueError for unrecognisable input.
    """
    s = s.strip()
    m = _SPICE_VAL_RE.match(s)
    if not m:
        raise ValueError(f"Cannot parse SPICE value: {s!r}")
    num = float(m.group(1))
    sfx = m.group(2)
    if sfx is None:
        return num
    return num * _SUFFIX_MAP[sfx]


def vary_value(
    nominal_str: str,
    pct: float,
    dist: str,
    rng: np.random.Generator | None = None,
) -> float:
    """Return a randomly varied version of *nominal_str*.

    *pct* is the ±percentage variation. *dist* is 'Gaussian' or 'Uniform'.
    Pass *rng* for reproducibility; defaults to the global numpy RNG.
    Raises ValueError if *nominal_str* cannot be parsed.
    """
    nom = parse_spice_val(nominal_str)
    rel = pct / 100.0
    r = rng if rng is not None else np.random
    if dist == "Gaussian":
        return float(nom * (1 + r.normal(0, rel / 3)))
    return float(nom * (1 + r.uniform(-rel, rel)))


def generate_netlists(
    base: str,
    variations: list[tuple[str, str, float, str]],
    n_runs: int,
    rng: np.random.Generator | None = None,
) -> list[str]:
    """Return *n_runs* netlists with component values varied as specified.

    *variations* is a list of ``(component_ref, nominal_str, pct, dist)`` tuples.
    Entries whose nominal value cannot be parsed are silently skipped (the
    component keeps its original netlist value) rather than generating 0.0.
    """
    netlists: list[str] = []
    for _ in range(n_runs):
        text = base
        for comp, nom, pct, dist in variations:
            try:
                new_val = vary_value(nom, pct, dist, rng)
            except ValueError:
                continue  # leave original value intact for unparseable entries
            text = re.sub(
                rf"(?m)^(\s*{re.escape(comp)}\s+\S+\s+\S+\s+)\S+",
                lambda m, v=new_val: m.group(1) + f"{v:.6g}",
                text,
                count=1,
            )
        netlists.append(text)
    return netlists

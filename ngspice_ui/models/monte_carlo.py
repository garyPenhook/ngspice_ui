"""Pure Monte Carlo netlist generation — no GUI dependency."""
from __future__ import annotations

import re

import numpy as np

_SUFFIX_MAP: dict[str, float] = {
    "T": 1e12, "G": 1e9, "MEG": 1e6,
    "K": 1e3, "k": 1e3,
    "m": 1e-3,
    "u": 1e-6, "U": 1e-6,
    "n": 1e-9, "N": 1e-9,
    "p": 1e-12, "P": 1e-12,
    "f": 1e-15, "F": 1e-15,
}
_SUFFIX_ORDER = ("MEG", "T", "G", "K", "k", "m", "u", "U", "n", "N", "p", "P", "f", "F")


def parse_spice_val(s: str) -> float:
    """Convert a SPICE value string (e.g. '10k', '2.2uF', '100MEG') to float."""
    s = s.strip()
    for sfx in _SUFFIX_ORDER:
        if s.upper().endswith(sfx.upper()):
            return float(s[: -len(sfx)]) * _SUFFIX_MAP[sfx]
    return float(s)


def vary_value(
    nominal_str: str,
    pct: float,
    dist: str,
    rng: np.random.Generator | None = None,
) -> float:
    """Return a randomly varied version of *nominal_str*.

    *pct* is the ±percentage variation. *dist* is 'Gaussian' or 'Uniform'.
    Pass *rng* for reproducibility; defaults to the global numpy RNG.
    """
    try:
        nom = parse_spice_val(nominal_str)
    except ValueError:
        return 0.0
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
    """
    netlists: list[str] = []
    for _ in range(n_runs):
        text = base
        for comp, nom, pct, dist in variations:
            new_val = vary_value(nom, pct, dist, rng)
            text = re.sub(
                rf"(?m)^(\s*{re.escape(comp)}\s+\S+\s+\S+\s+)\S+",
                lambda m, v=new_val: m.group(1) + f"{v:.6g}",
                text,
                count=1,
            )
        netlists.append(text)
    return netlists

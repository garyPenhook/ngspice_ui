"""Immutable snapshot of a completed simulation's vectors.

Decouples presentation (plotting, measurements, OP annotation) from the live,
process-global :class:`~ngspice_ui.engine.session.NgSpiceSession`. Once a run
finishes, :meth:`SimulationResult.from_session` copies every plot's vectors
into a frozen snapshot that can be read safely without racing the background
thread or being invalidated by the next ``load_netlist``.

The read API intentionally mirrors the session's
(``current_plot`` / ``all_plots`` / ``all_vecs`` / ``get_vector``) so consumers
treat a result and a session interchangeably for read-only access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ngspice_ui.engine.session import VectorData

# Matches a function-style vector reference such as ``v(out)`` or ``i(v1)``.
# ngspice's vector lister reports node voltages under the bare node name
# (``out``) and source/inductor currents as ``v1#branch``, but measurements,
# probes, and derived traces request them in SPICE access syntax ``v(out)`` /
# ``i(v1)``.  This bridges the two naming conventions.
_FUNC_REF_RE = re.compile(r"^\s*([vi])\s*\(\s*(.+?)\s*\)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class SimulationResult:
    """Frozen view over the vectors produced by one simulation."""

    current_plot_name: str = ""
    #: plot name -> ordered vector names (as reported by the engine)
    plots: dict[str, list[str]] = field(default_factory=dict)
    #: "plot.vec" -> copied VectorData
    vectors: dict[str, VectorData] = field(default_factory=dict)

    # -- session-compatible read API ----------------------------------------

    def current_plot(self) -> str:
        return self.current_plot_name

    def all_plots(self) -> list[str]:
        return list(self.plots)

    def all_vecs(self, plot: str) -> list[str]:
        return list(self.plots.get(plot, []))

    def get_vector(self, vecname: str) -> VectorData:
        """Resolve a vector by ``plot.vec`` / bare ``vec`` / ``v(node)`` / ``i(src)``.

        Three naming conventions must all resolve against the snapshot, whose
        keys are ``"<plot>.<engine-name>"`` (the engine reports node voltages as
        the bare node name and source currents as ``<src>#branch``):

        * fully-qualified ``tran1.out`` / ``tran1.v(out)``
        * bare ``out`` / ``v(out)`` against :attr:`current_plot_name`
        * SPICE access syntax ``v(out)`` → engine ``out``; ``i(v1)`` → ``v1#branch``

        Without this, documented measurements/probes that request ``v(out)`` (the
        natural SPICE spelling) failed even though the data was present under
        ``out``.
        """
        for key in self._candidate_keys(vecname):
            if key in self.vectors:
                return self.vectors[key]
        raise KeyError(f"Vector not found: {vecname!r}")

    def _candidate_keys(self, vecname: str):
        """Yield snapshot keys to try for *vecname*, most-specific first."""
        # (plot, leaf) pairs: an explicit, recognised plot prefix; the current
        # plot; and the raw name as a final fallback (already-qualified keys).
        pairs: list[tuple[str | None, str]] = []
        head, _, rest = vecname.partition(".")
        if rest and head in self.plots:
            pairs.append((head, rest))
        if self.current_plot_name:
            pairs.append((self.current_plot_name, vecname))
        pairs.append((None, vecname))

        for plot, leaf in pairs:
            for leaf_cand in self._expand_leaf(leaf):
                yield f"{plot}.{leaf_cand}" if plot else leaf_cand

    @staticmethod
    def _expand_leaf(name: str) -> list[str]:
        """Expand a leaf name to candidate engine names (unwrapping v()/i())."""
        cands = [name]
        m = _FUNC_REF_RE.match(name)
        if m:
            fn, inner = m.group(1).lower(), m.group(2)
            if fn == "v":
                cands.append(inner)  # v(out) -> out
            else:
                cands.append(f"{inner}#branch")  # i(v1) -> v1#branch
                cands.append(inner)
        return cands

    # -- construction --------------------------------------------------------

    @classmethod
    def from_session(cls, session) -> "SimulationResult":
        """Snapshot every (non-``const``) plot and vector from *session*.

        Vectors are copied eagerly so the result is independent of the live
        session. The ``const`` pseudo-plot is skipped (it holds only engine
        constants and is never plotted).
        """
        current = session.current_plot()
        plots: dict[str, list[str]] = {}
        vectors: dict[str, VectorData] = {}
        for plot in session.all_plots():
            if plot == "const":
                continue
            vecs = list(session.all_vecs(plot))
            plots[plot] = vecs
            for v in vecs:
                key = f"{plot}.{v}"
                try:
                    vectors[key] = session.get_vector(key)
                except Exception:
                    # A vector that vanished between listing and fetch is skipped
                    # rather than failing the whole snapshot.
                    pass
        return cls(current_plot_name=current, plots=plots, vectors=vectors)

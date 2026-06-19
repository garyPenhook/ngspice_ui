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

from dataclasses import dataclass, field

from ngspice_ui.engine.session import VectorData


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
        """Resolve a vector by fully-qualified ``plot.vec`` or bare ``vec`` name.

        A bare name resolves against :attr:`current_plot_name`, matching how
        ngspice resolves unqualified vectors in the current plot.
        """
        if vecname in self.vectors:
            return self.vectors[vecname]
        if "." not in vecname and self.current_plot_name:
            qualified = f"{self.current_plot_name}.{vecname}"
            if qualified in self.vectors:
                return self.vectors[qualified]
        raise KeyError(f"Vector not found: {vecname!r}")

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

"""ngspice post-sizing verification of op-amp performance metrics.

Re-simulates a *sized* circuit (W/L from
:class:`~circuitgenome.sizer.shared.models.SizingResult`) in ngspice using the
**same technology** as initial sizing, to cross-check the closed-form metrics
from ``_evaluate_metrics``.  For the card-less ``generic`` tech a Level-1 model
is synthesised from ``mu_cox``/``vth``/``lam`` (so SPICE ≈ the analytical
Level-1 formulas); for PTM nodes the BSIM4 ``.pm`` card is included (so the
delta reflects the Level-1-vs-device gap).

This is **best-effort verification**, not sign-off: each metric is measured by
an independent testbench and any that fails to converge/parse returns ``None``
(printed as ``n/a``) instead of raising.

Package layout:
    :mod:`.deck` — model emission, netlist parsing/sizing, ngspice runners
    :mod:`.rig` — port classification and the shared testbench rig
    :mod:`.measure` — per-metric testbenches
    :mod:`.op` — operating-point reading + DC bias-soundness verdict
    :mod:`.simulate` — the :func:`simulate_metrics` entry point
"""
from .deck import ngspice_available, pdk_netlist, sized_netlist
from .op import check_bias_soundness, read_op_operating_point
from .simulate import simulate_metrics

__all__ = [
    "check_bias_soundness",
    "ngspice_available",
    "pdk_netlist",
    "read_op_operating_point",
    "simulate_metrics",
    "sized_netlist",
]

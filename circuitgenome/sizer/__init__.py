"""
Initial Sizing module (Layer 3).

Computes transistor W/L values that satisfy a performance specification
using OR-Tools CP-SAT over the discrete geometry grid.

Typical usage::

    from circuitgenome.sizer import size_circuit, load_tech, SizingSpec

    tech = load_tech()                         # built-in generic config
    spec = SizingSpec(
        vdd=5.0, vss=0.0, ibias=10e-6, cl=20e-12,
        gain_min_db=80, gbw_min_hz=2.5e6,
        phase_margin_min_deg=60, slew_rate_min_vps=3.5e6,
    )
    result = size_circuit(parsed, sr_result, fbr_result, topology, tech, spec)
    for ref, s in result.transistors.items():
        print(f"{ref}: W={s.w_um}µm  L={s.l_um}µm")

The sized design is verified against ngspice through the same interface::

    from circuitgenome.sizer import ngspice_available, simulate_metrics

    if ngspice_available():
        measured = simulate_metrics(netlist_text, result, tech, spec)
"""
from .shared.loader import load_spec, load_tech
from .shared.models import (
    SizingResult,
    SizingSpec,
    TechParams,
    TransistorSizing,
    UnsupportedTechError,
)
from .shared.spice import (
    check_bias_soundness,
    ngspice_available,
    pdk_netlist,
    simulate_metrics,
    sized_netlist,
)
from .sizer import size_circuit

__all__ = [
    "load_spec",
    "load_tech",
    "size_circuit",
    "check_bias_soundness",
    "ngspice_available",
    "pdk_netlist",
    "simulate_metrics",
    "sized_netlist",
    "SizingResult",
    "SizingSpec",
    "TechParams",
    "TransistorSizing",
    "UnsupportedTechError",
]

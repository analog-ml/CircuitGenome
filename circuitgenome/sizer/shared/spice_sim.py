"""Facade for the :mod:`~circuitgenome.sizer.shared.spice` package.

The ngspice verification code lives in the ``spice/`` subpackage (deck
building, testbench rig, per-metric measurements, bias soundness); this module
keeps the historical import path stable for callers::

    from circuitgenome.sizer.shared.spice_sim import simulate_metrics
"""
from .spice import (
    check_bias_soundness,
    ngspice_available,
    read_op_operating_point,
    simulate_metrics,
    sized_netlist,
)

__all__ = [
    "check_bias_soundness",
    "ngspice_available",
    "read_op_operating_point",
    "simulate_metrics",
    "sized_netlist",
]

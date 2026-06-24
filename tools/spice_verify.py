#!/usr/bin/env python3
"""Standalone analytical-vs-SPICE metric comparison for a sized op-amp.

Runs initial sizing on a flat SPICE netlist and then verifies the metrics with
an ngspice simulation using the *same* technology, printing a side-by-side
table.  Thin wrapper over ``circuitgenome.sizer.spice_sim``; the
``circuitgenome size --simulate`` flag does the same inline.

Usage::

    python3 tools/spice_verify.py NETLIST.ckt \
        --topology two_stage_opamp_single_ended \
        --spec examples/two_stage_se_specs/spec_ptm45.yaml --tech ptm45
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from circuitgenome.recognizer import parse, recognize
from circuitgenome.recognizer.functional_block_recognizer import assign_slots
from circuitgenome.synthesizer.loader import load_topologies
from circuitgenome.sizer import load_tech, size_circuit, SizingSpec
from circuitgenome.sizer.spice_sim import ngspice_available, simulate_metrics

_COLS = [
    ("gain_db", "Open-loop gain", "dB", 1.0, "{:.2f}"),
    ("gbw_hz", "GBW", "MHz", 1e-6, "{:.3f}"),
    ("phase_margin_deg", "Phase margin", "deg", 1.0, "{:.1f}"),
    ("slew_rate_vps", "Slew rate", "V/us", 1e-6, "{:.3f}"),
    ("power_w", "Quiescent power", "mW", 1e3, "{:.4f}"),
    ("output_swing_max_v", "Output swing max", "V", 1.0, "{:.3f}"),
    ("output_swing_min_v", "Output swing min", "V", 1.0, "{:.3f}"),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("netlist", type=Path)
    ap.add_argument("--topology", required=True)
    ap.add_argument("--spec", type=Path, required=True)
    ap.add_argument("--tech", default=None, help="Built-in name or path (default: generic)")
    args = ap.parse_args()

    text = args.netlist.read_text()
    topology = next(t for t in load_topologies() if t.name == args.topology)
    parsed = parse(text)
    fbr = assign_slots(recognize(parsed), topology)
    tech = load_tech(args.tech)
    spec_data = yaml.safe_load(args.spec.read_text())
    spec = SizingSpec(**{k: v for k, v in spec_data.items()
                         if k in SizingSpec.__dataclass_fields__})
    result = size_circuit(parsed, recognize(parsed), fbr, topology, tech, spec)

    print(f"Netlist : {args.netlist.name}")
    print(f"Topology: {args.topology}   Tech: {tech.name}   Solver: {result.solver_status}")
    if not result.transistors:
        print("No feasible sizing — nothing to simulate.")
        return
    if not ngspice_available():
        print("ngspice not found on PATH — install it (e.g. `brew install ngspice`).")
        return

    sim = simulate_metrics(text, result, tech, spec)
    print(f"\n  {'metric':<20}{'analytical':>15}{'SPICE':>15}{'delta':>10}")
    for key, label, unit, scl, fmt in _COLS:
        a = result.metrics.get(key)
        s = sim.get(key)
        a_str = f"{fmt.format(a*scl)} {unit}" if a is not None else "n/a"
        s_str = f"{fmt.format(s*scl)} {unit}" if s is not None else "n/a"
        d_str = f"{(s-a)/abs(a)*100:+.0f}%" if (a not in (None, 0) and s is not None) else "-"
        print(f"  {label:<20}{a_str:>15}{s_str:>15}{d_str:>10}")
    for note in sim.get("notes", []) or []:
        print(f"  (i) {note}")
    print("  (SPICE = best-effort cross-check; FD AC metrics may show n/a)")


if __name__ == "__main__":
    main()

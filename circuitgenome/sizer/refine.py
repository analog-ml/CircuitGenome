"""SPICE-in-the-loop operating-point refinement of a sized circuit.

The analytical sizer assigns each device its KCL-assumed current.  When the bias
network can't actually deliver that current in silicon (e.g. a tail current
source pushed into triode by limited headroom, issue #76 cause A), the reported
gm/GBW/gain are optimistic.  This pass runs a single SPICE ``.op`` on the sized
circuit, reads the **actual** per-device current, and re-evaluates the metrics at
that operating point so the report tracks reality — and flags any device left in
triode.

The geometry is unchanged; only the operating currents (and hence the reported
metrics) are corrected.  Re-sizing to *recover* a starved current is a separate,
optional extension (a headroom-bound device can't be fixed by width).
"""
from __future__ import annotations

import dataclasses

from . import spice_sim
from .device_model import DeviceModel
from .models import SizingResult, SizingSpec, TechParams, TransistorSizing


def refine_with_spice(
    result: SizingResult,
    netlist_text: str,
    slot_transistors: dict[str, list],
    tech: TechParams,
    spec: SizingSpec,
    model: DeviceModel,
    gd_load_r: float = 0.0,
) -> SizingResult:
    """Return ``result`` with metrics re-evaluated at the SPICE operating point.

    Falls back to the unmodified ``result`` (with a note) when ngspice is
    unavailable or the bias point can't be read.
    """
    # Imported lazily to avoid a circular import (sizer imports refine indirectly
    # only via the CLI, not at module load).
    from .sizer import _evaluate_metrics

    op = spice_sim.read_op_operating_point(netlist_text, result, tech, spec)
    if not op:
        return dataclasses.replace(
            result,
            warnings=result.warnings
            + ["SPICE refinement skipped (ngspice unavailable, FD topology, or "
               "bias did not settle)."],
        )

    # Rebuild the sizing with each device's actual drain current; flag triode.
    refined: dict[str, TransistorSizing] = {}
    triode: list[str] = []
    for ref, s in result.transistors.items():
        d = op.get(ref)
        if not d or "id" not in d:
            refined[ref] = s
            continue
        ids_actual = abs(d["id"])
        if "vds" in d and "vdsat" in d and abs(d["vds"]) < abs(d["vdsat"]) - 1e-3:
            triode.append(ref)
        refined[ref] = TransistorSizing(
            ref=ref, w_um=s.w_um, l_um=s.l_um, ids_a=ids_actual,
            vgs_v=model.vgs(_dtype(slot_transistors, ref), s.w_um, s.l_um, ids_actual)
            if _dtype(slot_transistors, ref) else s.vgs_v,
            vds_sat_v=abs(d.get("vdsat", s.vds_sat_v)),
        )

    metrics, margins = _evaluate_metrics(
        refined, slot_transistors, result.cc_pf, tech, spec, model,
        cc2_pf=result.cc2_pf, gd_load_r=gd_load_r,
    )
    warnings = list(result.warnings)
    if triode:
        warnings.append(
            "SPICE refinement: device(s) in triode (starved current) — "
            + ", ".join(sorted(triode))
            + "; metrics re-evaluated at the actual operating point.")
    else:
        warnings.append("SPICE refinement: metrics re-evaluated at the SPICE "
                        "operating point.")
    return dataclasses.replace(
        result, transistors=refined, metrics=metrics, margins=margins,
        warnings=warnings,
    )


def _dtype(slot_transistors: dict[str, list], ref: str) -> str | None:
    for devs in slot_transistors.values():
        for d in devs:
            if d.ref == ref and d.type in ("nmos", "pmos"):
                return d.type
    return None

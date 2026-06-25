"""Size the non-load resistor blocks of a gm/Id-sized op-amp.

The Level-1 sizer (and gm/Id phase 1) only sized rail-referenced ``load``
resistors; `resistor_bias`, `resistor_tail`, and source-**degeneration** r1/r2
kept the 1 kΩ netlist placeholder, mis-biasing those variants. This module sizes
each per its role and reports the two metric effects (degeneration on gm1,
resistor-tail output conductance on CMRR).

Sized values flow out through ``SizingResult.resistors`` and
``spice_sim._inject_sizes`` replaces the placeholder, exactly like the load
resistors.
"""
from __future__ import annotations

from ..shared.models import SizingSpec, TechParams, TransistorSizing
from .blocks import OpAmpBlocks
from .intent import GmIdIntent


def size_resistors(
    blocks: OpAmpBlocks,
    slot_resistors: dict[str, list],
    ids_map: dict[str, float],
    sizing: dict[str, TransistorSizing],
    model,
    spec: SizingSpec,
    tech: TechParams,
    intent: GmIdIntent,
) -> tuple[dict[str, float], float, float | None, float]:
    """Return ``(extra_resistors, gm1_factor, gd_tail_override, gd_out_extra)``.

    * ``extra_resistors``: ``{ref: ohms}`` for degeneration / tail / bias / cmfb.
    * ``gm1_factor``: multiplies the input-pair gm for source degeneration
      (``1/(1+gm1·R)`` = ``1/(1+factor)``); ``1.0`` when none.
    * ``gd_tail_override``: ``1/R`` of a resistor tail (finite output conductance
      for CMRR), or ``None``.
    * ``gd_out_extra``: output-node conductance added by a resistive-sense CMFB
      averager (``1/R_sense``), loading the FD output; ``0.0`` when none.
    """
    out: dict[str, float] = {}
    gm1_factor = 1.0
    gd_tail_override: float | None = None
    gd_out_extra = 0.0
    vcm = (spec.vdd + spec.vss) / 2.0

    # --- Source degeneration (input-pair-slot resistors) ---
    ip = blocks.input_pair
    ip_res = slot_resistors.get("input_pair", [])
    if ip_res and intent.degeneration_factor > 0 and ip and ip.mosfets:
        d = ip.mosfets[0]
        s = sizing.get(d.ref)
        if s:
            gm1 = model.gm(d.type, s.w_um, s.l_um, s.ids_a)
            if gm1 > 0:
                r_val = intent.degeneration_factor / gm1
                for r in ip_res:
                    out[r.ref] = r_val
                gm1_factor = 1.0 / (1.0 + intent.degeneration_factor)

    # --- Resistor tail (tail_current-slot resistors): set the tail current ---
    tail_res = slot_resistors.get("tail_current", [])
    if tail_res and ip and ip.mosfets:
        ip_dev = ip.mosfets[0]
        s = sizing.get(ip_dev.ref)
        vgs = abs(model.vgs(ip_dev.type, s.w_um, s.l_um, s.ids_a)) if s else 0.0
        if ip_dev.type == "pmos":           # source toward vdd
            v_tail, vrail = vcm + vgs, spec.vdd
        else:                                # source toward vss
            v_tail, vrail = vcm - vgs, spec.vss
        i_tail = spec.ibias
        r_val = abs(vrail - v_tail) / i_tail if i_tail > 0 else 0.0
        for r in tail_res:
            out[r.ref] = r_val
        if r_val > 0:
            gd_tail_override = 1.0 / r_val

    # --- Resistor bias legs (bias_gen-slot resistors): out_i = ibias·R_i ---
    bias_res = slot_resistors.get("bias_gen", [])
    if bias_res and spec.ibias > 0:
        v_gate = _representative_bias_vgs(blocks, sizing) or 0.5 * (spec.vdd - spec.vss)
        r_val = v_gate / spec.ibias
        for r in bias_res:
            out[r.ref] = r_val

    # --- CMFB resistive-sense averager (cmfb-slot resistors): large, low-load ---
    cmfb_res = slot_resistors.get("cmfb", [])
    if cmfb_res:
        for r in cmfb_res:
            out[r.ref] = intent.cmfb_sense_r
        # Each output node is loaded by one sense resistor to the (virtual-ground)
        # sense node → adds 1/R_sense to the differential output conductance.
        gd_out_extra = 1.0 / intent.cmfb_sense_r if intent.cmfb_sense_r > 0 else 0.0

    return out, gm1_factor, gd_tail_override, gd_out_extra


def _representative_bias_vgs(blocks: OpAmpBlocks,
                             sizing: dict[str, TransistorSizing]) -> float | None:
    """A current-source |Vgs| to bias the resistor-bias rails to (approximate)."""
    bg = blocks.blocks.get("bias_gen")
    devs = bg.mosfets if bg else []
    for d in devs:
        s = sizing.get(d.ref)
        if s and s.vgs_v:
            return abs(s.vgs_v)
    return None

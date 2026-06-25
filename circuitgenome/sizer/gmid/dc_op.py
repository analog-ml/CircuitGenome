"""DC operating-point / headroom check for the gm/Id pipeline.

Phase 1: reuses the existing tail headroom pass
(:func:`~circuitgenome.sizer.gmid.headroom.apply_headroom`, which lowers the tail
mirror group's Vdsat to fit when it can) and adds **cascode awareness** — for a
*stacked* tail (cascode current source) the budget is the **sum** of the series
devices' Vdsat, which a single-device check misses (e.g. circuit_0110).  Returns
the warnings plus a ``bias_feasible`` verdict so callers can flag/de-rate the
reported metrics when the tail can't actually bias.

Later phases fold the full per-branch KVL solve in here.
"""
from __future__ import annotations

from ..shared.device_model import GmIdModel
from .headroom import apply_headroom
from ..shared.models import SizingSpec, TechParams, TransistorSizing

from .blocks import OpAmpBlocks


def _tail_stack_to_rail(tail_devs, net_tail: str) -> list:
    """Series-stacked tail devices from ``net_tail`` up to the supply rail."""
    by_drain = {d.terminals.get("d"): d for d in tail_devs}
    chain = []
    node = net_tail
    seen = set()
    while node in by_drain and node not in seen:
        seen.add(node)
        d = by_drain[node]
        chain.append(d)
        node = d.terminals.get("s")  # follow to the device below
    return chain


def check_dc_operating_point(
    model,
    blocks: OpAmpBlocks,
    slot_transistors: dict[str, list],
    all_transistors: dict[str, tuple],
    ids_map: dict[str, float],
    sizing: dict[str, TransistorSizing],
    spec: SizingSpec,
    tech: TechParams,
) -> tuple[list[str], bool]:
    """Return ``(warnings, bias_feasible)``.  Mutates ``sizing`` (tail re-size)."""
    if not isinstance(model, GmIdModel):
        return [], True

    warnings = list(apply_headroom(
        model, slot_transistors, all_transistors, ids_map, sizing, spec, tech))
    bias_feasible = not any("headroom" in w for w in warnings)

    # Cascode-aware budget: a stacked tail needs the *sum* of its devices' Vdsat.
    ip = blocks.input_pair
    tail = blocks.tail
    if ip and tail and ip.mosfets and tail.is_cascode:
        ip_dev = ip.mosfets[0]
        net_tail = ip_dev.terminals.get("s")
        s_ip = sizing.get(ip_dev.ref)
        if net_tail and s_ip:
            vcm = (spec.vdd + spec.vss) / 2.0
            vgs_pair = abs(model.vgs(ip_dev.type, s_ip.w_um, s_ip.l_um, s_ip.ids_a))
            headroom = (spec.vdd - (vcm + vgs_pair) if ip_dev.type == "pmos"
                        else (vcm - vgs_pair) - spec.vss)
            chain = _tail_stack_to_rail(tail.mosfets, net_tail)
            stacked_vdsat = sum(
                model.vds_sat(d.type, sizing[d.ref].w_um, sizing[d.ref].l_um,
                              sizing[d.ref].ids_a)
                for d in chain if d.ref in sizing)
            if stacked_vdsat > headroom and not any("cascode" in w for w in warnings):
                warnings.append(
                    f"cascode tail current source cannot bias: needs "
                    f"{stacked_vdsat * 1e3:.0f} mV of stacked Vdsat but only "
                    f"{headroom * 1e3:.0f} mV is available at Vcm={vcm:.2f} V — the "
                    f"input-pair current will collapse (use a non-cascode tail, "
                    f"lower the input common-mode, or raise the supply).")
                bias_feasible = False
    return warnings, bias_feasible

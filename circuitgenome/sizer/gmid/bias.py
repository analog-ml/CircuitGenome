"""DC bias feasibility for the gm/Id pipeline: tail headroom + cascode budget.

The gm/Id sizer assigns currents by KCL and sizes each device for a target
gm/Id, but the LUT is characterized at a fixed ``Vds=Vdd/2`` and never checks
that the *stacked* DC operating points fit the supply with every device
saturated.  This module is that check, in two stages:

1. :func:`apply_headroom` — the **tail saturation-budget** pass.  At low supplies
   (PTM nodes) a PMOS input pair lifts its source node ``net_tail`` to
   ``Vcm + |Vgs_pair|``, which can leave the PMOS tail less than its ``Vdsat`` of
   headroom against the rail — the tail drops into triode and sources far less
   than the assumed current, collapsing gm1 and GBW (issue #76, cause A).  When
   the tail is short it first tries to **lower the tail's Vdsat** by raising the
   tail current-mirror group's gm/Id (which keeps the mirror consistent — same
   gm/Id ⇒ same Vgs and a width that still tracks the current ratio).  If even
   the table's weak-inversion limit does not fit, it emits an honest warning.

2. :func:`check_dc_operating_point` — wraps the headroom pass and adds
   **cascode awareness**: for a *stacked* tail (cascode current source) the
   budget is the **sum** of the series devices' Vdsat, which a single-device
   check misses (e.g. circuit_0110).  Returns the warnings plus a
   ``bias_feasible`` verdict so callers can flag/de-rate the reported metrics
   when the tail can't actually bias.

The SPICE DC bias-soundness check grounds the final feasibility verdict; these
passes are the analytical (ngspice-free) estimate.  Later phases fold the full
per-branch KVL solve in here.
"""
from __future__ import annotations

from ..shared.device_model import GmIdModel
from ..shared.models import GridSpec, SizingSpec, TechParams, TransistorSizing

from .blocks import OpAmpBlocks

__all__ = ["apply_headroom", "check_dc_operating_point"]


# --------------------------------------------------------------------------- #
# Tail saturation-budget pass
# --------------------------------------------------------------------------- #
def _snap_w(g: GridSpec, w_um: float) -> float:
    return float(min(max(round(w_um / g.step) * g.step, g.min), g.max))


def _tail_gm_id_for_headroom(
    model: GmIdModel, dtype: str, l_um: float, headroom_v: float
) -> float | None:
    """Smallest gm/Id whose Vdsat fits ``headroom_v`` (highest rout that fits).

    Returns ``None`` when even the table's weak-inversion ceiling does not fit.
    """
    margin = 0.9 * headroom_v  # keep a little slack below the rail
    for gm_id in model.lut.gm_id_axis:  # ascending → Vdsat descending
        if model.lut.vdsat(dtype, float(gm_id), l_um) <= margin:
            return float(gm_id)
    return None


def apply_headroom(
    model: GmIdModel,
    slot_transistors: dict[str, list],
    all_transistors: dict[str, tuple],
    ids_map: dict[str, float],
    sizing: dict[str, TransistorSizing],
    spec: SizingSpec,
    tech: TechParams,
) -> list[str]:
    """Check/repair tail saturation headroom in place; return warnings.

    Mutates ``sizing`` when it re-sizes the tail mirror group to fit the budget.
    """
    ip = slot_transistors.get("input_pair", [])
    tc = slot_transistors.get("tail_current", [])
    if not (ip and tc):
        return []
    ip_dev, tc_dev = ip[0], tc[0]
    s_ip, s_tc = sizing.get(ip_dev.ref), sizing.get(tc_dev.ref)
    if not (s_ip and s_tc):
        return []

    vcm = (spec.vdd + spec.vss) / 2.0
    vgs_pair = abs(model.vgs(ip_dev.type, s_ip.w_um, s_ip.l_um, s_ip.ids_a))
    # PMOS pair sits above the gate (source toward vdd); NMOS pair below (toward vss).
    if ip_dev.type == "pmos":
        headroom = spec.vdd - (vcm + vgs_pair)
    else:
        headroom = (vcm - vgs_pair) - spec.vss

    vdsat_tail = model.vds_sat(tc_dev.type, s_tc.w_um, s_tc.l_um, s_tc.ids_a)
    if headroom >= vdsat_tail:
        return []  # tail already fits

    def _warn() -> list[str]:
        return [
            f"tail current source has insufficient saturation headroom "
            f"({headroom * 1e3:.0f} mV available vs {vdsat_tail * 1e3:.0f} mV Vdsat "
            f"at Vcm={vcm:.2f} V) — the input-pair bias current will fall short; "
            f"raise the supply, lower the input common-mode, or use the opposite "
            f"input polarity."
        ]

    if headroom <= 0:
        return _warn()  # no room at all; can't size around it

    # Try to fit by lowering the tail group's Vdsat (raise its gm/Id).
    gm_id_new = _tail_gm_id_for_headroom(model, tc_dev.type, s_tc.l_um, headroom)
    if gm_id_new is None:
        return _warn()

    # Re-size the whole tail current-mirror group (devices sharing the tail's
    # gate net) at the new gm/Id, preserving each device's current — so the
    # mirror ratios (W ∝ I at equal gm/Id) are unchanged.
    tail_gate = tc_dev.terminals.get("g")
    g = tech.width
    for ref, (dev, _slot) in all_transistors.items():
        if dev.type != tc_dev.type or dev.terminals.get("g") != tail_gate:
            continue
        s = sizing.get(ref)
        if not s:
            continue
        idw = model.lut.id_per_w(dev.type, gm_id_new, s.l_um)
        if idw <= 0:
            continue
        w_new = _snap_w(g, abs(s.ids_a) / idw)
        sizing[ref] = TransistorSizing(
            ref=ref, w_um=w_new, l_um=s.l_um, ids_a=s.ids_a,
            vgs_v=model.vgs(dev.type, w_new, s.l_um, s.ids_a),
            vds_sat_v=model.vds_sat(dev.type, w_new, s.l_um, s.ids_a),
        )

    # Verify the repair actually fit (snapping can leave a small residual).
    s_tc2 = sizing[tc_dev.ref]
    if model.vds_sat(tc_dev.type, s_tc2.w_um, s_tc2.l_um, s_tc2.ids_a) > headroom:
        return _warn()
    return []


# --------------------------------------------------------------------------- #
# Cascode-aware DC operating-point check
# --------------------------------------------------------------------------- #
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
    model: GmIdModel,
    blocks: OpAmpBlocks,
    slot_transistors: dict[str, list],
    all_transistors: dict[str, tuple],
    ids_map: dict[str, float],
    sizing: dict[str, TransistorSizing],
    spec: SizingSpec,
    tech: TechParams,
) -> tuple[list[str], bool]:
    """Return ``(warnings, bias_feasible)``.  Mutates ``sizing`` (tail re-size)."""
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

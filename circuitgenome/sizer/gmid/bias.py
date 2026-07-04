"""DC-bias feasibility for the gm/Id pipeline: headroom check, repair, verdict.

The gm/Id sizer assigns currents by KCL and sizes each device for a target
gm/Id, but the LUT is characterized at a fixed ``Vds=Vdd/2`` and never checks
that the *stacked* DC operating points fit the supply with every device
saturated.  At low supplies (PTM nodes) this bites the **tail current source**:
a PMOS input pair lifts its source node ``net_tail`` to ``Vcm + |Vgs_pair|``,
which can leave the PMOS tail less than its ``Vdsat`` of headroom against the
rail — the tail drops into triode and sources far less than the assumed current,
collapsing gm1 and GBW (issue #76, cause A).

:func:`check_dc_operating_point` runs two passes and returns
``(sizing, warnings, bias_feasible)`` without mutating its input:

1. **Headroom repair** — when the tail's Vdsat does not fit, try to lower it by
   raising the tail current-mirror group's gm/Id (which keeps the mirror
   consistent — the same gm/Id implies the same Vgs and a width that still
   tracks the current ratio).  When that alone cannot fit, also move the input
   pair toward weak inversion (raise its gm/Id → smaller ``|Vgs|`` → more tail
   headroom); the pair's gm requirement is a minimum, so the stronger pair is
   spec-safe.  If even the table's weak-inversion limit does not fit, emit an
   honest warning (the SPICE DC bias-soundness check then grounds the final
   feasibility verdict).
2. **Cascode-aware budget** — a *stacked* tail (cascode current source) needs
   the **sum** of the series devices' Vdsat, which a single-device check misses
   (e.g. circuit_0110).

The ``bias_feasible`` verdict lets callers flag/de-rate the reported metrics
when the tail can't actually bias.
"""
from __future__ import annotations

from ..shared.device_model import GmIdModel
from ..shared.models import GridSpec, SizingSpec, TechParams, TransistorSizing
from .blocks import OpAmpBlocks

__all__ = ["check_dc_operating_point"]


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


def _apply_headroom(
    model,
    slot_transistors: dict[str, list],
    all_transistors: dict[str, tuple],
    ids_map: dict[str, float],
    sizing: dict[str, TransistorSizing],
    spec: SizingSpec,
    tech: TechParams,
) -> tuple[dict[str, TransistorSizing], list[str]]:
    """Check/repair tail saturation headroom; return ``(sizing, warnings)``.

    Only active for :class:`~.device_model.GmIdModel` (the gm/Id path).  Never
    mutates the input ``sizing`` — a repaired tail mirror group (and, when
    needed, a weaker-inversion input pair) comes back in a new mapping (kept
    even when the snapped repair leaves a small residual, so the tail sits as
    close to fitting as the table allows).
    """
    if not isinstance(model, GmIdModel):
        return sizing, []
    ip = slot_transistors.get("input_pair", [])
    tc = slot_transistors.get("tail_current", [])
    if not (ip and tc):
        return sizing, []
    ip_dev, tc_dev = ip[0], tc[0]
    s_ip, s_tc = sizing.get(ip_dev.ref), sizing.get(tc_dev.ref)
    if not (s_ip and s_tc):
        return sizing, []

    vcm = (spec.vdd + spec.vss) / 2.0
    vgs_pair = abs(model.vgs(ip_dev.type, s_ip.w_um, s_ip.l_um, s_ip.ids_a))
    # PMOS pair sits above the gate (source toward vdd); NMOS pair below (toward vss).
    if ip_dev.type == "pmos":
        headroom = spec.vdd - (vcm + vgs_pair)
    else:
        headroom = (vcm - vgs_pair) - spec.vss

    vdsat_tail = model.vds_sat(tc_dev.type, s_tc.w_um, s_tc.l_um, s_tc.ids_a)
    if headroom >= vdsat_tail:
        return sizing, []  # tail already fits

    def _warn() -> list[str]:
        return [
            f"tail current source has insufficient saturation headroom "
            f"({headroom * 1e3:.0f} mV available vs {vdsat_tail * 1e3:.0f} mV Vdsat "
            f"at Vcm={vcm:.2f} V) — the input-pair bias current will fall short; "
            f"raise the supply, lower the input common-mode, or use the opposite "
            f"input polarity."
        ]

    def _resize_group(base: dict[str, TransistorSizing], devs, gm_id: float,
                      ) -> dict[str, TransistorSizing]:
        """Re-size *devs* at ``gm_id`` preserving each device's current."""
        out = dict(base)
        for dev in devs:
            s = base.get(dev.ref)
            idw = model.lut.id_per_w(dev.type, gm_id, s.l_um) if s else 0.0
            if not s or idw <= 0:
                continue
            w_new = _snap_w(tech.width, abs(s.ids_a) / idw)
            out[dev.ref] = TransistorSizing(
                ref=dev.ref, w_um=w_new, l_um=s.l_um, ids_a=s.ids_a,
                vgs_v=model.vgs(dev.type, w_new, s.l_um, s.ids_a),
                vds_sat_v=model.vds_sat(dev.type, w_new, s.l_um, s.ids_a),
            )
        return out

    # The tail current-mirror group: devices sharing the tail's gate net.
    # Re-sizing it at one gm/Id preserves the mirror ratios (W ∝ I).
    tail_gate = tc_dev.terminals.get("g")
    tail_group = [dev for dev, _slot in all_transistors.values()
                  if dev.type == tc_dev.type and dev.terminals.get("g") == tail_gate]

    # Candidate input-pair operating points: as-sized first, then progressively
    # weaker inversion — a gm requirement is a *minimum*, so raising the pair's
    # gm/Id (smaller |Vgs| → more tail headroom) is spec-safe.  Take the first
    # candidate whose headroom the tail can be re-sized to fit.
    pair_devs = [d for d in ip if d.type == ip_dev.type]
    cands: list[tuple[float | None, float]] = [(None, headroom)]
    for gm_id_pair in (float(g) for g in model.lut.gm_id_axis):
        idw = model.lut.id_per_w(ip_dev.type, gm_id_pair, s_ip.l_um)
        if idw <= 0:
            continue
        w_p = _snap_w(tech.width, abs(s_ip.ids_a) / idw)
        vgs_p = abs(model.vgs(ip_dev.type, w_p, s_ip.l_um, s_ip.ids_a))
        h = (spec.vdd - (vcm + vgs_p) if ip_dev.type == "pmos"
             else (vcm - vgs_p) - spec.vss)
        if h > headroom + 1e-9:
            cands.append((gm_id_pair, h))

    best: dict[str, TransistorSizing] | None = None
    for gm_id_pair, h in cands:
        if h <= 0:
            continue
        gm_id_tail = _tail_gm_id_for_headroom(model, tc_dev.type, s_tc.l_um, h)
        if gm_id_tail is None:
            continue
        repaired = sizing if gm_id_pair is None \
            else _resize_group(sizing, pair_devs, gm_id_pair)
        repaired = _resize_group(repaired, tail_group, gm_id_tail)
        s_tc2 = repaired[tc_dev.ref]
        # Verify the repair actually fit (snapping can leave a small residual).
        if model.vds_sat(tc_dev.type, s_tc2.w_um, s_tc2.l_um, s_tc2.ids_a) <= h:
            return repaired, []
        best = best or repaired  # keep the closest-fitting attempt

    return (best, _warn()) if best is not None else (sizing, _warn())


def check_dc_operating_point(
    model,
    blocks: OpAmpBlocks,
    slot_transistors: dict[str, list],
    all_transistors: dict[str, tuple],
    ids_map: dict[str, float],
    sizing: dict[str, TransistorSizing],
    spec: SizingSpec,
    tech: TechParams,
) -> tuple[dict[str, TransistorSizing], list[str], bool]:
    """Return ``(sizing, warnings, bias_feasible)``.

    The returned ``sizing`` reflects any tail headroom repair; the input mapping
    is never mutated.
    """
    if not isinstance(model, GmIdModel):
        return sizing, [], True

    sizing, warnings = _apply_headroom(
        model, slot_transistors, all_transistors, ids_map, sizing, spec, tech)
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
    return sizing, warnings, bias_feasible

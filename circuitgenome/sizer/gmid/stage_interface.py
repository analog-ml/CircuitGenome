"""Stage-interface DC feasibility: the first-stage output pin vs the load window.

The second stage's input device pins the first-stage output node at a level of
its own: its ``V_GS`` above the source rail (a common-source stage) or the
quiescent output level plus ``V_GS`` (a follower).  A cascode load needs that
same node inside a saturation window: above its NMOS output-leg stack (mirror
diode levels + Vdsat floors) and below its PMOS stack.  Per-device gm/Id
planning never compares the two (issue #124): every telescopic-load candidate
comes back ``bias_feasible = True`` and dies at the SPICE ``.op`` gate with
the mirror's output cascode in triode.

:func:`check_stage_interface` computes both levels post-geometry and, when the
pin falls outside the window, tries to close the gap with the plan's real
degrees of freedom before rejecting:

1. **Mirror stack** — the violated side's load mirror groups move toward weak
   inversion (lower diode ``V_GS`` and Vdsat lower the whole stack
   requirement).  Mirror devices carry no gm requirement, so this knob is
   always spec-safe; re-sizing the whole gate group at one gm/Id preserves the
   mirror ratios (W tracks each device's current).
2. **Second-stage device** — its ``|V_GS|`` may move only while
   ``gm/Id · Id`` still meets the stage's gm requirement (a minimum), so a
   follower can be pushed toward weak inversion (lower pin) but a
   common-source stage sized exactly at ``gm_req/Id`` cannot move the other
   way.

When no LUT point reaches the margin but some assignment still clears the raw
bounds, the closest-fitting sizing is kept and the SPICE gate stays the ground
truth.  When even the raw bounds cannot be cleared, the verdict is an honest
``bias_feasible = False`` with an explanatory warning — the candidate is
rejected before the SPICE evaluation it cannot pass.

Scope: single-ended cascode loads only.  A fully-differential first stage's
output levels are set by CMFB, not by the second-stage gate, and non-cascode
loads have enough headroom for the interface never to bind.
"""
from __future__ import annotations

from ..shared.device_model import GmIdModel
from ..shared.models import SizingSpec, TechParams, TransistorSizing
from ..shared.taxonomy import RAILS
from .blocks import OpAmpBlocks

__all__ = ["check_stage_interface"]

#: Saturation slack the repair aims for between the pin and each stack bound.
_MARGIN_V = 0.05


def _snap_w(tech: TechParams, w_um: float) -> float:
    g = tech.width
    return float(min(max(round(w_um / g.step) * g.step, g.min), g.max))


def _rail_v(net: str, spec: SizingSpec) -> float:
    return spec.vdd if net == "vdd!" else spec.vss


def _diode_level(net: str | None, diode_by_net: dict, sizing: dict,
                 spec: SizingSpec, _depth: int = 0) -> float | None:
    """DC level of ``net`` when a diode chain ties it to a rail, else ``None``."""
    if net is None or _depth > 8:
        return None
    if net in RAILS:
        return _rail_v(net, spec)
    d = diode_by_net.get(net)
    s = sizing.get(d.ref) if d is not None else None
    if s is None:
        return None
    base = _diode_level(d.terminals.get("s"), diode_by_net, sizing, spec,
                        _depth + 1)
    if base is None:
        return None
    return base + abs(s.vgs_v) if d.type == "nmos" else base - abs(s.vgs_v)


def _stack_bound(net: str, dtype: str, devs: list, pair_refs: set[str],
                 sizing: dict, spec: SizingSpec) -> float | None:
    """Level the ``dtype`` stack at ``net`` demands of the node.

    ``nmos`` → a *lower* bound (the node must sit above the stack to keep it
    saturated); ``pmos`` → an *upper* bound.  ``None`` when no such stack
    hangs off ``net``.

    Walks drain→source from ``net`` toward the rail, accumulating each crossed
    device's Vdsat, until an anchor with a known level: a supply rail, a
    mirror cascode whose gate level comes from diode drops (source pinned at
    gate − ``V_GS``), or the input pair (source pinned at Vcm ∓ ``V_GS`` by
    the feedback-held inputs).
    """
    sgn = 1.0 if dtype == "nmos" else -1.0
    vcm = (spec.vdd + spec.vss) / 2.0
    by_drain: dict[str, object] = {}
    diode_by_net: dict[str, object] = {}
    for d in devs:
        if d.type != dtype:
            continue
        by_drain.setdefault(d.terminals.get("d"), d)
        if d.terminals.get("g") == d.terminals.get("d"):
            diode_by_net.setdefault(d.terminals.get("d"), d)
    node, floor, seen = net, 0.0, set()
    while node not in seen:
        seen.add(node)
        dev = by_drain.get(node)
        s = sizing.get(dev.ref) if dev is not None else None
        if s is None:
            return None
        floor += abs(s.vds_sat_v)
        if dev.ref in pair_refs:
            return vcm - sgn * abs(s.vgs_v) + sgn * floor
        gate = dev.terminals.get("g")
        g_lvl = (_diode_level(gate, diode_by_net, sizing, spec)
                 if gate != node else None)
        if g_lvl is not None:
            return g_lvl - sgn * abs(s.vgs_v) + sgn * floor
        src = dev.terminals.get("s")
        if src in RAILS:
            return _rail_v(src, spec) + sgn * floor
        node = src
    return None


def _pin_device(blocks: OpAmpBlocks):
    """The next stage's signal device, whose gate is the first-stage output."""
    for slot in ("second_stage", "second_stage_p", "second_stage_n",
                 "third_stage", "third_stage_p", "third_stage_n"):
        b = blocks.blocks.get(slot)
        sig = b.signal_device if b else None
        if sig is not None:
            return sig
    return None


def _pin_level(dev, s: TransistorSizing, spec: SizingSpec) -> float | None:
    """DC level ``dev`` pins its gate (the stage-interface node) at."""
    vgs = abs(s.vgs_v)
    src, drn = dev.terminals.get("s"), dev.terminals.get("d")
    if src in RAILS:  # common-source: gate = source rail ± V_GS
        return _rail_v(src, spec) + (vgs if dev.type == "nmos" else -vgs)
    if drn in RAILS:  # follower: gate = quiescent output ± V_GS
        vout_q = (spec.vdd + spec.vss) / 2.0
        return vout_q + (vgs if dev.type == "nmos" else -vgs)
    return None


def _resize_at(model: GmIdModel, tech: TechParams, dev,
               s: TransistorSizing, gm_id: float) -> TransistorSizing | None:
    idw = model.lut.id_per_w(dev.type, gm_id, s.l_um)
    if idw <= 0:
        return None
    w = _snap_w(tech, abs(s.ids_a) / idw)
    return TransistorSizing(
        ref=dev.ref, w_um=w, l_um=s.l_um, ids_a=s.ids_a,
        vgs_v=model.vgs(dev.type, w, s.l_um, s.ids_a),
        vds_sat_v=model.vds_sat(dev.type, w, s.l_um, s.ids_a),
    )


def _mirror_group_devs(load_devs: list, dtype: str) -> list:
    """Load devices of ``dtype`` in gate groups that contain a diode member."""
    groups: dict[str, list] = {}
    for d in load_devs:
        gate = d.terminals.get("g")
        if d.type == dtype and gate:
            groups.setdefault(gate, []).append(d)
    return [d for gate, devs in groups.items()
            if any(m.terminals.get("d") == gate for m in devs)
            for d in devs]


def check_stage_interface(
    model,
    blocks: OpAmpBlocks,
    sizing: dict[str, TransistorSizing],
    gm_req_map: dict[str, float],
    spec: SizingSpec,
    tech: TechParams,
) -> tuple[dict[str, TransistorSizing], list[str], bool]:
    """Return ``(sizing, warnings, feasible)``; never mutates the input.

    A repaired sizing (mirror stack and/or second-stage device moved to a
    fitting gm/Id) comes back in a new mapping.  ``feasible`` is ``False``
    only when no LUT point clears the raw bounds.
    """
    if not isinstance(model, GmIdModel):
        return sizing, [], True
    if blocks.is_fully_differential or not blocks.has_cascode_load():
        return sizing, [], True
    net_mid = blocks.first_stage_out_net()
    ss_dev = _pin_device(blocks)
    ld, ip = blocks.load, blocks.input_pair
    s_ss = sizing.get(ss_dev.ref) if ss_dev is not None else None
    if not (net_mid and ld and ld.mosfets and s_ss):
        return sizing, [], True

    pair_refs = {d.ref for d in ip.mosfets} if ip else set()
    stack_devs = ld.mosfets + (ip.mosfets if ip else [])

    def window(szg):
        return (_stack_bound(net_mid, "nmos", stack_devs, pair_refs, szg, spec),
                _stack_bound(net_mid, "pmos", stack_devs, pair_refs, szg, spec))

    def slack(pin, lo, hi):
        vals = [pin - lo if lo is not None else float("inf"),
                hi - pin if hi is not None else float("inf")]
        return min(vals)

    lo, hi = window(sizing)
    pin = _pin_level(ss_dev, s_ss, spec)
    if pin is None or slack(pin, lo, hi) >= _MARGIN_V:
        return sizing, [], True

    # --- repair: scan mirror-group and second-stage gm/Id candidates -------
    axis = [float(g) for g in model.lut.gm_id_axis]
    mirror_devs = []
    if lo is not None and pin < lo + _MARGIN_V:
        mirror_devs += _mirror_group_devs(ld.mosfets, "nmos")
    if hi is not None and pin > hi - _MARGIN_V:
        mirror_devs += _mirror_group_devs(ld.mosfets, "pmos")
    gm_req_ss = gm_req_map.get(ss_dev.ref, 0.0)
    ss_cands = [None] + [g for g in axis if g * abs(s_ss.ids_a) >= gm_req_ss]

    best, best_slack = sizing, slack(pin, lo, hi)
    for gm_m in [None] + (axis if mirror_devs else []):
        base = sizing
        if gm_m is not None:
            base = dict(sizing)
            for d in mirror_devs:
                s = base.get(d.ref)
                ns = _resize_at(model, tech, d, s, gm_m) if s else None
                if ns is not None:
                    base[d.ref] = ns
        for gm_s in ss_cands:
            cand = base
            if gm_s is not None:
                ns = _resize_at(model, tech, ss_dev, s_ss, gm_s)
                if ns is None:
                    continue
                cand = dict(base)
                cand[ss_dev.ref] = ns
            lo2, hi2 = window(cand)
            pin2 = _pin_level(ss_dev, cand[ss_dev.ref], spec)
            if pin2 is None:
                continue
            sl = slack(pin2, lo2, hi2)
            if sl >= _MARGIN_V:
                return cand, [], True  # first fit = least deviation
            if sl > best_slack:
                best, best_slack = cand, sl

    if best_slack >= 0.0:
        # Clears the raw bounds but not the margin: keep the closest-fitting
        # sizing and let the SPICE gate ground the verdict.
        return best, [], True

    parts = []
    if lo is not None and pin < lo:
        parts.append(f"≥ {lo:.2f} V (its NMOS output-leg stack)")
    if hi is not None and pin > hi:
        parts.append(f"≤ {hi:.2f} V (its PMOS output-leg stack)")
    warning = (
        f"stage interface cannot bias: the second-stage input pins the "
        f"first-stage output at {pin:.2f} V but the load needs it "
        + " and ".join(parts)
        + " — no gm/Id assignment closes the gap, the load's output leg "
          "will triode (use a different load / second-stage pairing or "
          "raise the supply)."
    )
    return sizing, [warning], False

"""Tune the constructed bias generator's level devices (gm/Id pipeline).

The demand-driven bias generator (synthesizer ``bias_construction.py`` +
``config/bias_legs.yaml``) contains two kinds of devices whose operating
point is a *level choice*, not a mirror ratio, so the intent-table geometry
pass cannot size them:

- **Cascode-leg level diodes** (``cascode_gnd``/``cascode_vdd`` legs): the
  rail must sit at the consumer cascode's ``V_GS`` plus its stack's
  saturation floor. The leg's diode covers the ``V_GS`` part (re-sized here
  to match the consumers' planned ``V_GS``, so the rail *tracks* Vth over
  process/temperature) and the floor resistor under it covers the small
  Vdsat floor (``R = floor / I_leg``).
- **The pref branch's wide-swing cascode level** (``ncasc``): the branch's
  NMOS mirror is cascoded to pin its Vds near the master reference's
  (uncascoded it runs at ``vdd - |V_GSP|`` -- the ~4% extra-mirror-hop error
  of issue #103's A/B). The ``ncasc`` diode is sized *narrow* (high
  ``V_GS``) to push the pinned Vds as close to the master's ``V_GS`` as the
  LUT range and the pref node's headroom allow; the cascode itself is
  re-sized to the weak-inversion end (low ``V_GS`` and Vdsat) to maximize
  that reach.

Both detections are structural (actual device terminals -- a diode riding a
resistor to a supply, a mirror-drain cascode topped by a diode), so external
netlists without these shapes pass through untouched. Runs after geometry
and the DC operating-point check; its resistor values override the generic
fallback :func:`~.resistors.size_resistors` assigns to nets it cannot
derive.
"""
from __future__ import annotations

from ..shared.device_model import GmIdModel
from ..shared.models import SizingSpec, TechParams, TransistorSizing
from ..shared.taxonomy import RAILS
from .blocks import OpAmpBlocks
from .resistors import _bias_rail_target_v

__all__ = ["tune_bias_levels"]

#: Saturation slack kept between a pinned node and the limit it must clear.
_MARGIN_V = 0.05


def _sized(model: GmIdModel, ref: str, dtype: str, w_um: float, l_um: float,
           ids_a: float) -> TransistorSizing:
    return TransistorSizing(
        ref=ref, w_um=w_um, l_um=l_um, ids_a=ids_a,
        vgs_v=model.vgs(dtype, w_um, l_um, ids_a),
        vds_sat_v=model.vds_sat(dtype, w_um, l_um, ids_a),
    )


def _snap_w(tech: TechParams, w_um: float) -> float:
    g = tech.width
    return float(min(max(round(w_um / g.step) * g.step, g.min), g.max))


def _diode_candidates(model: GmIdModel, tech: TechParams, dtype: str,
                      l_um: float, ids_a: float) -> list[tuple[float, float]]:
    """``(w_um, |vgs|)`` per LUT gm/Id point, weak to strong ``|vgs|`` order."""
    out = []
    for gm_id in model.lut.gm_id_axis:
        idw = model.lut.id_per_w(dtype, float(gm_id), l_um)
        if idw <= 0:
            continue
        w = _snap_w(tech, abs(ids_a) / idw)
        out.append((w, abs(model.vgs(dtype, w, l_um, ids_a))))
    return out


def _size_diode_for_vgs(model: GmIdModel, tech: TechParams, dtype: str,
                        l_um: float, ids_a: float, target_vgs: float,
                        cap_vgs: float | None = None) -> float | None:
    """Width whose ``|vgs|`` lands closest to ``target_vgs`` (≤ ``cap_vgs``).

    Returns ``None`` when no LUT point satisfies the cap.
    """
    cands = _diode_candidates(model, tech, dtype, l_um, ids_a)
    if cap_vgs is not None:
        capped = [c for c in cands if c[1] <= cap_vgs]
        cands = capped or []
    if not cands:
        return None
    return min(cands, key=lambda c: abs(c[1] - target_vgs))[0]


def _tune_cascode_legs(bg, consumers, ids_map, sizing, model, spec, tech,
                       out_r: dict[str, float]) -> None:
    """Re-size each cascode leg's level diode and floor resistor in place.

    A cascode leg is a bias_gen resistor with one end on a supply and the
    other end (``mid``) under a bias_gen diode's source; the diode's drain
    is the rail.
    """
    for r in bg.resistors:
        t1, t2 = r.terminals.get("t1"), r.terminals.get("t2")
        supply = next((t for t in (t1, t2) if t in RAILS), None)
        mid = next((t for t in (t1, t2) if t and t not in RAILS), None)
        if supply is None or mid is None:
            continue
        diode = next(
            (d for d in bg.mosfets
             if d.terminals.get("s") == mid
             and d.terminals.get("d") == d.terminals.get("g")),
            None,
        )
        if diode is None:
            continue  # a tunable leg's rail resistor -- size_resistors owns it
        rail = diode.terminals["d"]
        s = sizing.get(diode.ref)
        target_abs = _bias_rail_target_v(rail, consumers, sizing, spec)
        if s is None or target_abs is None:
            continue
        vgs_targets = [
            abs(cs.vgs_v) for dev in consumers
            if dev.terminals.get("g") == rail
            and dev.terminals.get("d") != rail
            and (cs := sizing.get(dev.ref)) is not None and cs.vgs_v
        ]
        if not vgs_targets:
            continue
        target_vgs = sum(vgs_targets) / len(vgs_targets)
        i_leg = abs(ids_map.get(diode.ref, spec.ibias)) or spec.ibias

        w = _size_diode_for_vgs(model, tech, diode.type, s.l_um, s.ids_a,
                                target_vgs)
        if w is not None:
            sizing[diode.ref] = _sized(model, diode.ref, diode.type, w,
                                       s.l_um, s.ids_a)
        vgs_actual = abs(sizing[diode.ref].vgs_v)
        span = (target_abs - spec.vss) if supply != "vdd!" \
            else (spec.vdd - target_abs)
        floor = max(span - vgs_actual, 0.0)
        out_r[r.ref] = floor / i_leg if floor > 0 else 1.0


def _tune_pref_cascode(bg, sizing, model, spec, tech) -> None:
    """Re-size the pref branch's wide-swing ``ncasc`` level in place.

    The chain is found structurally: the master diode names the ibias net;
    the pref mirror (gate on ibias, source on gnd) carries a same-slot
    cascode on its drain, topped by a diode-connected PMOS (the pref diode);
    the cascode's gate names the ``ncasc`` diode.
    """
    master = next(
        (d for d in bg.mosfets
         if d.type == "nmos"
         and d.terminals.get("d") == d.terminals.get("g")
         and d.terminals.get("s") in RAILS),
        None,
    )
    if master is None:
        return
    ibias_net = master.terminals["g"]
    for mirror in bg.mosfets:
        if (mirror.type != "nmos" or mirror.ref == master.ref
                or mirror.terminals.get("g") != ibias_net
                or mirror.terminals.get("s") not in RAILS):
            continue
        drain = mirror.terminals.get("d")
        cascode = next(
            (c for c in bg.mosfets
             if c.type == "nmos" and c.terminals.get("s") == drain
             and c.terminals.get("g") not in (ibias_net, None)),
            None,
        )
        if cascode is None:
            continue
        ncasc_net = cascode.terminals["g"]
        ncasc_diode = next(
            (d for d in bg.mosfets
             if d.type == "nmos" and d.ref != cascode.ref
             and d.terminals.get("d") == ncasc_net
             and d.terminals.get("g") == ncasc_net),
            None,
        )
        pref_diode = next(
            (p for p in bg.mosfets
             if p.type == "pmos"
             and p.terminals.get("d") == cascode.terminals.get("d")
             and p.terminals.get("g") == cascode.terminals.get("d")),
            None,
        )
        s_m = sizing.get(mirror.ref)
        s_c = sizing.get(cascode.ref)
        s_d = sizing.get(ncasc_diode.ref) if ncasc_diode else None
        s_p = sizing.get(pref_diode.ref) if pref_diode else None
        if not (s_m and s_c and s_d and s_p):
            continue

        # Cascode to the weak-inversion end: lowest V_GS and Vdsat maximize
        # how high the ncasc diode can push the pinned node.
        gm_id_weak = float(model.lut.gm_id_axis[-1])
        idw = model.lut.id_per_w("nmos", gm_id_weak, s_c.l_um)
        if idw > 0:
            w_c = _snap_w(tech, abs(s_c.ids_a) / idw)
            sizing[cascode.ref] = _sized(model, cascode.ref, "nmos", w_c,
                                         s_c.l_um, s_c.ids_a)
            s_c = sizing[cascode.ref]

        # Pin the mirror's Vds (= vgs_ncasc - vgs_cascode) as close to the
        # master's V_GS as the LUT reaches, under the pref node's headroom.
        v_pref = spec.vdd - abs(s_p.vgs_v)
        vgs_master = abs(sizing.get(master.ref, s_m).vgs_v)
        n1_max = v_pref - abs(s_c.vds_sat_v) - _MARGIN_V - spec.vss
        target = vgs_master + abs(s_c.vgs_v)
        cap = n1_max + abs(s_c.vgs_v)
        w_d = _size_diode_for_vgs(model, tech, "nmos", s_d.l_um, s_d.ids_a,
                                  target, cap_vgs=cap)
        if w_d is not None:
            sizing[ncasc_diode.ref] = _sized(model, ncasc_diode.ref, "nmos",
                                             w_d, s_d.l_um, s_d.ids_a)


def tune_bias_levels(
    blocks: OpAmpBlocks,
    ids_map: dict[str, float],
    sizing: dict[str, TransistorSizing],
    model,
    spec: SizingSpec,
    tech: TechParams,
) -> tuple[dict[str, TransistorSizing], dict[str, float]]:
    """Return ``(sizing, level_resistors)`` with the bias levels tuned.

    The input mapping is never mutated. ``level_resistors`` carries the
    cascode legs' floor-resistor values; merge it *over* the generic values
    from :func:`~.resistors.size_resistors` (which cannot derive a level for
    a ``mid`` net and falls back to its representative value).
    """
    if not isinstance(model, GmIdModel):
        return sizing, {}
    bg = blocks.blocks.get("bias_gen")
    if bg is None or not bg.mosfets:
        return sizing, {}

    tuned = dict(sizing)
    level_r: dict[str, float] = {}
    consumers = [d for name, b in blocks.blocks.items() if name != "bias_gen"
                 for d in b.mosfets]
    _tune_cascode_legs(bg, consumers, ids_map, tuned, model, spec, tech,
                       level_r)
    _tune_pref_cascode(bg, tuned, model, spec, tech)
    return tuned, level_r

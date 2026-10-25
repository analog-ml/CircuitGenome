"""Post-geometry two-stage GBW / phase-margin refinement for the gm/Id pipeline.

:func:`~circuitgenome.sizer.physics.preprocess.compute_requirements` sizes a
two-stage Miller amplifier's gm1 and gm2 from closed forms that leave out the
output pole's pull on the 0 dB crossing, the bare Miller cap's
right-half-plane zero and the first-stage node's capacitance.  The metrics
read the full model instead (:func:`~..physics.equations.two_stage_loop`),
where such a design can fall short of the specs it was sized for (issue #274).

:func:`refine_two_stage_plan` finds the gm1 and gm2 the full model needs on the
sized circuit (:func:`~..physics.metrics.two_stage_replan`) and returns a plan
with them for the caller to re-size against.
"""
from __future__ import annotations

from dataclasses import replace

from ..models import SizingSpec, TransistorSizing
from ..physics.device_model import SIGNAL
from ..physics.metrics import two_stage_replan
from ..physics.stage_chain import build_stage_chain
from ..physics.taxonomy import SECOND_STAGE_SLOTS, is_signal_device
from .analyze import GmIdCircuitView
from .plan import CurrentPlan, SizingPlan


def refine_two_stage_plan(
    view: GmIdCircuitView,
    currents: CurrentPlan,
    plan: SizingPlan,
    sizing: dict[str, TransistorSizing],
    spec: SizingSpec,
) -> tuple[SizingPlan, bool]:
    """Return ``(plan, changed)`` with the input-pair and second-stage gm the
    full-model GBW and phase margin need.

    ``changed`` is ``True`` when either requirement rose -- the caller must
    re-size.  gm1 is searched up to the widest single PDK device; gm2 up to
    the table's weak-inversion ceiling, and a raised second stage joins the
    plan's ``wide_refs``, so the geometry builds it past the widest single
    device (a PTM45 second stage sits at 100 µm already).  A chain that is not
    two-stage, or already meets both specs, comes back unchanged.
    """
    if not plan.cc_pf or view.compensation_scheme is not None:
        return plan, False
    ss = next(((dev, sizing[ref]) for ref, (dev, slot) in view.all_transistors.items()
               if slot in SECOND_STAGE_SLOTS and is_signal_device(dev)
               and ref in sizing), None)
    if ss is None:
        return plan, False
    chain = build_stage_chain(view, sizing, plan.model, spec, cc_pf=plan.cc_pf,
                              gd_load_r=currents.gd_load_r)
    if len(chain.stages) != 2 or not chain.node_caps_f:
        return plan, False

    model, (dev, s) = plan.model, ss

    def gate_cap(w_um: float) -> float:
        # As the chain loads its node: inversion-level gate plus source overlap.
        return model.cgg(dev.type, w_um, s.l_um, s.ids_a) + model.cgso(dev.type, w_um)
    c_now = gate_cap(s.w_um)

    def c1_at(gm: float) -> float:
        w_um = model.geometry_for(dev.type, s.ids_a, SIGNAL, gm, l_um=s.l_um).w_um
        return chain.node_caps_f[0] - c_now + gate_cap(max(w_um, model.tech.width.min))

    ip = next(((d, sizing[ref]) for ref, (d, slot) in view.all_transistors.items()
               if slot == "input_pair" and is_signal_device(d) and ref in sizing), None)
    gm1_max = 0.0 if ip is None else min(
        model.gm(ip[0].type, model.tech.width.max, ip[1].l_um, ip[1].ids_a),
        model.gm_ceiling(ip[0].type, ip[1].ids_a, ip[1].l_um))
    gm1, gm2 = two_stage_replan(chain, spec, c1_at, gm1_max,
                                model.gm_ceiling(dev.type, s.ids_a, s.l_um))
    if gm1 is None and gm2 is None:
        return plan, False
    gm_req = dict(plan.gm_req_map)
    wide = set(plan.wide_refs)
    for ref, (_dev, slot) in view.all_transistors.items():
        if not gm_req.get(ref):
            continue
        if gm1 is not None and slot == "input_pair":
            gm_req[ref] = max(gm_req[ref], gm1)
        elif gm2 is not None and slot in SECOND_STAGE_SLOTS:
            gm_req[ref] = max(gm_req[ref], gm2)
            wide.add(ref)
    return replace(plan, gm_req_map=gm_req, wide_refs=frozenset(wide)), True

"""Post-geometry RNMC compensation refinement for the gm/Id pipeline.

:func:`~circuitgenome.sizer.physics.preprocess.compute_requirements` sizes a
reversed-nested-Miller loop from *estimates*: the input pair's gm is the one
the GBW spec asks for, and the third stage's input gate capacitance is a guess
from its gm.  Geometry then moves both — the DC-bias repair can push the input
pair deep into weak inversion (a ptm45 FD fixture lands at ~27× the requested
gm1, so the loop crosses 0 dB at ~54 MHz instead of 2 MHz), and the gm/Id
table puts a floor under every gm.  The inner pole pair sized on the estimates
can then sit in the right half plane.

:func:`refine_rnmc_plan` re-runs :func:`~..physics.rnmc.design_rnmc` on the
sized circuit's real small-signal values and, when it asks for a different
gm2/Cc2/gm3, returns a plan with the new requirements for the caller to
re-size against.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from circuitgenome.synthesizer.models import Device

from ..models import SizingSpec, TransistorSizing
from ..physics import rnmc
from ..physics.device_model import SIGNAL, GmIdModel
from ..physics.stage_chain import build_stage_chain
from ..physics.taxonomy import SECOND_STAGE_SLOTS, THIRD_STAGE_SLOTS, is_signal_device
from .analyze import GmIdCircuitView
from .plan import CurrentPlan, SizingPlan

_ADVISORY_PREFIX = "RNMC compensation:"
_CAP_PREFIX = _ADVISORY_PREFIX + " third-stage gm capped"


def _node_cap_at(model: GmIdModel, dev: Device, s: TransistorSizing,
                 c_node: float) -> Callable[[float], float]:
    """Gate capacitance on ``dev``'s node as a function of ``dev``'s gm.

    ``c_node`` is the node's capacitance at the current sizing; re-sizing
    ``dev`` for another gm (same current and length, LUT width) swaps its
    own gate capacitance for the new one.
    """
    width = model.tech.width
    c_now = model.cgs(dev.type, s.w_um, s.l_um)

    def at(gm: float) -> float:
        w_um = model.geometry_for(dev.type, s.ids_a, SIGNAL, gm, l_um=s.l_um).w_um
        w_um = min(max(w_um, width.min), width.max)
        return c_node - c_now + model.cgs(dev.type, w_um, s.l_um)
    return at


def refine_rnmc_plan(
    view: GmIdCircuitView,
    currents: CurrentPlan,
    plan: SizingPlan,
    sizing: dict[str, TransistorSizing],
    spec: SizingSpec,
    *,
    hold_gm2: bool = False,
) -> tuple[SizingPlan, bool]:
    """Return ``(plan, changed)`` with RNMC requirements matched to ``sizing``.

    ``changed`` is ``True`` when the second-stage gm requirement rose, Cc2
    grew or the third-stage gm requirement fell — the caller must re-size.
    The plan's RNMC advisories are updated for this design.  A
    non-RNMC circuit, or one without three live stages, comes back unchanged.
    ``hold_gm2`` caps gm2 at its sized value — for when raising it has broken
    the bias (the second stage's input also sets the first-stage output
    level), leaving Cc2 and gm3 as the knobs.
    """
    if (view.compensation_scheme != rnmc.RNMC or not plan.cc_pf
            or not plan.cc2_pf):
        return plan, False
    chain = build_stage_chain(view, sizing, plan.model, spec, cc_pf=plan.cc_pf,
                              cc2_pf=plan.cc2_pf, gd_load_r=currents.gd_load_r)
    stages = chain.stages
    if len(stages) < 3 or any(st.gm <= 0 for st in stages[:3]):
        return plan, False

    # The second stage's signal device, and the third stage's *input* device
    # (gate on a second-stage output) — the gates loading the Cc nodes.
    stage2_out = {dev.terminals.get("d") for dev, slot in view.all_transistors.values()
                  if slot in SECOND_STAGE_SLOTS and is_signal_device(dev)}
    ss = next(((dev, sizing[ref]) for ref, (dev, slot) in view.all_transistors.items()
               if slot in SECOND_STAGE_SLOTS and is_signal_device(dev)
               and ref in sizing), None)
    ts = next(((dev, sizing[ref]) for ref, (dev, slot) in view.all_transistors.items()
               if slot in THIRD_STAGE_SLOTS and dev.terminals.get("g") in stage2_out
               and ref in sizing), None)
    if ss is None or ts is None:
        return plan, False
    gm1, gm2, gm3 = chain.k_fs * stages[0].gm, stages[1].gm, stages[2].gm
    gm2_max = (gm2 if hold_gm2 else
               plan.model.gm_ceiling(ss[0].type, ss[1].ids_a, ss[1].l_um))
    # gm3 floor: the table's weakest-inversion gm/Id, unless an earlier pass
    # already asked for less than the device delivers — then geometry has a
    # floor of its own (e.g. the output-swing gm/Id floor) and gm3 is there.
    gm3_min = float(plan.model.lut.gm_id_axis[0]) * ts[1].ids_a
    if plan.gm_req_map.get(ts[0].ref, gm3) < gm3 * (1.0 - 1e-3):
        gm3_min = gm3
    cc1_f, cc2_f = plan.cc_pf * 1e-12, plan.cc2_pf * 1e-12
    g = tuple(1.0 / st.rout if st.rout < float("inf") else 0.0 for st in stages[:3])
    design = rnmc.design_rnmc(
        gm1, gm2, gm3, cc1_f=cc1_f, cc2_f=cc2_f, cl_f=spec.cl,
        gm2_max=gm2_max, gm3_min=min(gm3_min, gm3),
        pm_min_deg=spec.phase_margin_min_deg,
        c1_f=_node_cap_at(plan.model, *ss, chain.node_caps_f[0]),
        c2_f=_node_cap_at(plan.model, *ts, chain.node_caps_f[1]), g=g,
        mirror_pole_hz=chain.third_stage_mirror_pole_hz,
        buffer=chain.output_buffer)

    raise_gm2 = design.gm2 > gm2
    cap_gm3 = design.gm3 < gm3
    changed = raise_gm2 or cap_gm3 or design.cc2_f > cc2_f
    # The unmet-target advisory always reflects this pass; a gm3-cap advisory
    # from an earlier pass survives until a later pass caps gm3 again.
    new = rnmc.design_warnings(design, gm3, spec.phase_margin_min_deg)
    caps = ([w for w in new if w.startswith(_CAP_PREFIX)]
            or [w for w in plan.warnings if w.startswith(_CAP_PREFIX)])
    warnings = [w for w in plan.warnings if not w.startswith(_ADVISORY_PREFIX)]
    warnings += caps + [w for w in new if not w.startswith(_CAP_PREFIX)]
    gm_req = dict(plan.gm_req_map)
    # gm3 is capped on the third stage's input device only: a non-inverting
    # stage's mirror keeps its sizing, and with it the output device's low
    # Vdsat (swing).
    for ref, (dev, slot) in view.all_transistors.items():
        if not gm_req.get(ref):
            continue
        if raise_gm2 and slot in SECOND_STAGE_SLOTS:
            gm_req[ref] = max(gm_req[ref], design.gm2)
        elif (cap_gm3 and slot in THIRD_STAGE_SLOTS
              and dev.terminals.get("g") in stage2_out):
            gm_req[ref] = design.gm3
    return replace(plan, gm_req_map=gm_req, cc2_pf=design.cc2_f * 1e12,
                   warnings=warnings), changed

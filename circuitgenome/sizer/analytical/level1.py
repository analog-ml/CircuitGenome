"""Level-1 (square-law) sizing pipeline: discrete W/L via OR-Tools CP-SAT.

Used for the card-less ``generic`` technology.  Reuses the shared preprocessing
(:mod:`circuitgenome.sizer.physics.preprocess`) and metric evaluation
(:mod:`circuitgenome.sizer.physics.metrics`); the discrete geometry search is built
in :mod:`.constraints`.
"""
from __future__ import annotations

from ortools.sat.python import cp_model

from circuitgenome.recognizer.models import (
    FunctionalBlockRecognitionResult,
    ParsedNetlist,
    SubcircuitRecognitionResult,
)
from circuitgenome.synthesizer.models import TopologyTemplate

from ..physics import equations as eq
from ..physics.device_model import Level1Model
from ..physics.metrics import evaluate_metrics, two_stage_replan
from ..models import SizingResult, SizingSpec, TechParams, TransistorSizing
from ..physics.circuit_view import adoption_warnings, analyze_circuit
from ..physics.preprocess import (
    assign_ids,
    compute_requirements,
    size_compensation_caps,
    size_load_resistors,
)
from ..physics.stage_chain import build_stage_chain
from ..physics.taxonomy import SECOND_STAGE_SLOTS, is_signal_device
from .constraints import build_model


#: Re-plan/re-solve rounds for a two-stage amplifier's full-model GBW and PM.
_TWO_STAGE_PASSES = 2


def size_level1(
    parsed: ParsedNetlist,
    sr_result: SubcircuitRecognitionResult,
    fbr_result: FunctionalBlockRecognitionResult,
    topology: TopologyTemplate,
    tech: TechParams,
    spec: SizingSpec,
    *,
    time_limit_s: float = 30.0,
) -> SizingResult:
    """Size a circuit with the Level-1 square-law model + CP-SAT geometry search."""
    view = analyze_circuit(fbr_result, topology)
    slot_transistors = view.slot_transistors
    all_transistors = view.all_transistors
    topology_warnings = view.warnings
    ids_map = assign_ids(slot_transistors, all_transistors, spec)
    # Size resistor loads (deterministic) and model them in the first-stage Rout.
    resistors = size_load_resistors(view.slot_resistors, spec, tech)
    gd_load_r = (1.0 / min(resistors.values())) if resistors else 0.0

    # Level-1 square-law model; discrete W/L via CP-SAT.
    dev_model = Level1Model(tech)
    gm_req_map, vod_max_map, cc_pf, cc2_pf, gm_ceiling_warnings = compute_requirements(
        slot_transistors, all_transistors, ids_map, tech, spec, dev_model, gd_load_r,
        compensation_scheme=view.compensation_scheme,
    )
    all_warnings = (topology_warnings + gm_ceiling_warnings
                    + adoption_warnings(view.adopted))
    capacitors = size_compensation_caps(view.slot_capacitors, cc_pf, cc2_pf)

    def _solve(gm_req_map):
        """CP-SAT geometry for ``gm_req_map``: ``(status_name, sizing)``, the
        sizing ``None`` when no assignment was found."""
        cp_mdl, W_vars, L_vars = build_model(
            all_transistors, slot_transistors, ids_map, gm_req_map, vod_max_map, tech
        )
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_s
        status = solver.solve(cp_mdl)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return solver.status_name(status), None
        # Extract solution: convert integer step-units back to µm.
        sizing = {}
        for ref, (device, _slot) in all_transistors.items():
            w_um = solver.value(W_vars[ref]) * tech.width.step
            l_um = solver.value(L_vars[ref]) * tech.length.step
            ids_a = ids_map[ref]
            sizing[ref] = TransistorSizing(
                ref=ref, w_um=w_um, l_um=l_um, ids_a=ids_a,
                vgs_v=dev_model.vgs(device.type, w_um, l_um, ids_a),
                vds_sat_v=dev_model.vds_sat(device.type, w_um, l_um, ids_a),
            )
        return solver.status_name(status), sizing

    status_name, transistor_sizing = _solve(gm_req_map)
    if transistor_sizing is None:
        return SizingResult(
            transistors={},
            resistors=resistors,
            capacitors=capacitors,
            metrics={},
            margins={},
            solver_status=status_name,
            warnings=all_warnings,
        )

    def _chain(sizing):
        return build_stage_chain(view, sizing, dev_model, spec,
                                 cc_pf=cc_pf, cc2_pf=cc2_pf, gd_load_r=gd_load_r)

    # Two-stage: re-solve at the gm1/gm2 the full-model GBW and phase margin
    # need (issue #274).  Square law: W, and with it the second stage's own
    # gate on the first-stage node, grows as gm2².  The integer W grid can
    # overshoot a raised gm1 by ~10 %, so a second pass re-plans gm2 against
    # the gm1 the first one delivered.
    chain = _chain(transistor_sizing)

    def signal(sizing, slot_ok):
        return next(((d, sizing[ref]) for ref, (d, slot) in all_transistors.items()
                     if slot_ok(slot) and is_signal_device(d)), None)

    def gm_max(d, s):
        return min(dev_model.gm(d.type, tech.width.max, s.l_um, s.ids_a),
                   dev_model.gm_ceiling(d.type, s.ids_a, s.l_um))

    req = dict(gm_req_map)
    for _ in range(_TWO_STAGE_PASSES):
        ss = signal(transistor_sizing, lambda sl: sl in SECOND_STAGE_SLOTS)
        ip = signal(transistor_sizing, lambda sl: sl == "input_pair")
        if ss is None or ip is None or len(chain.stages) != 2 or not chain.node_caps_f:
            break
        gm_now = chain.stages[1].gm
        c_own = dev_model.cgs(ss[0].type, ss[1].w_um, ss[1].l_um)
        c1_now = chain.node_caps_f[0]
        gm1, gm2 = two_stage_replan(
            chain, spec, lambda gm: c1_now + c_own * ((gm / gm_now) ** 2 - 1.0),
            gm_max(*ip), gm_max(*ss))
        if gm1 is None and gm2 is None:
            break
        for ref, g in req.items():
            dev, slot = all_transistors[ref]
            if not g or not is_signal_device(dev):
                continue
            if gm1 is not None and slot == "input_pair":
                req[ref] = max(g, gm1)
            elif gm2 is not None and slot in SECOND_STAGE_SLOTS:
                req[ref] = max(g, gm2)
        status2, sizing2 = _solve(req)
        if sizing2 is None:
            break
        status_name, transistor_sizing = status2, sizing2
        chain = _chain(sizing2)

    metrics, margins = evaluate_metrics(chain, spec)
    return SizingResult(
        transistors=transistor_sizing,
        resistors=resistors,
        capacitors=capacitors,
        metrics=metrics,
        margins=margins,
        solver_status=status_name,
        open_loop_gain_measurable=eq.open_loop_gain_measurable(
            metrics.get("gain_db")),
        warnings=all_warnings,
    )

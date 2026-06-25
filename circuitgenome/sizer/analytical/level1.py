"""Level-1 (square-law) sizing pipeline: discrete W/L via OR-Tools CP-SAT.

Used for the card-less ``generic`` technology.  Reuses the shared preprocessing
(:mod:`circuitgenome.sizer.shared.preprocess`) and metric evaluation
(:mod:`circuitgenome.sizer.shared.metrics`); the discrete geometry search is built
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

from ..shared.device_model import Level1Model
from ..shared.metrics import _evaluate_metrics
from ..shared.models import SizingResult, SizingSpec, TechParams, TransistorSizing
from ..shared.preprocess import (
    _assign_ids,
    _check_topology_match,
    _compute_requirements,
    _deduplicate,
    _extract_slot_resistors,
    _extract_slot_transistors,
    _size_load_resistors,
)
from .constraints import build_model


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
    slot_transistors = _extract_slot_transistors(fbr_result)
    topology_warnings = _check_topology_match(slot_transistors, topology.name)
    all_transistors = _deduplicate(slot_transistors)
    ids_map = _assign_ids(slot_transistors, all_transistors, spec)
    # Size resistor loads (deterministic) and model them in the first-stage Rout.
    resistors = _size_load_resistors(_extract_slot_resistors(fbr_result), spec, tech)
    gd_load_r = (1.0 / min(resistors.values())) if resistors else 0.0

    # Level-1 square-law model; discrete W/L via CP-SAT.
    dev_model = Level1Model(tech)
    gm_req_map, vod_max_map, cc_pf, cc2_pf, gm_ceiling_warnings = _compute_requirements(
        slot_transistors, all_transistors, ids_map, tech, spec, dev_model, gd_load_r
    )
    all_warnings = topology_warnings + gm_ceiling_warnings

    cp_mdl, W_vars, L_vars = build_model(
        all_transistors, slot_transistors, ids_map, gm_req_map, vod_max_map, tech
    )
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    status = solver.solve(cp_mdl)
    status_name = solver.status_name(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return SizingResult(
            transistors={},
            cc_pf=cc_pf,
            metrics={},
            margins={},
            solver_status=status_name,
            cc2_pf=cc2_pf,
            warnings=all_warnings,
            resistors=resistors,
        )

    # Extract solution: convert integer step-units back to µm.
    w_step = tech.width.step
    l_step = tech.length.step
    transistor_sizing = {}
    for ref, (device, _slot) in all_transistors.items():
        w_um = solver.value(W_vars[ref]) * w_step
        l_um = solver.value(L_vars[ref]) * l_step
        ids_a = ids_map[ref]
        transistor_sizing[ref] = TransistorSizing(
            ref=ref, w_um=w_um, l_um=l_um, ids_a=ids_a,
            vgs_v=dev_model.vgs(device.type, w_um, l_um, ids_a),
            vds_sat_v=dev_model.vds_sat(device.type, w_um, l_um, ids_a),
        )

    metrics, margins = _evaluate_metrics(
        transistor_sizing, slot_transistors, cc_pf, tech, spec, dev_model,
        cc2_pf=cc2_pf, gd_load_r=gd_load_r,
    )
    return SizingResult(
        transistors=transistor_sizing,
        cc_pf=cc_pf,
        metrics=metrics,
        margins=margins,
        solver_status=status_name,
        cc2_pf=cc2_pf,
        warnings=all_warnings,
        resistors=resistors,
    )

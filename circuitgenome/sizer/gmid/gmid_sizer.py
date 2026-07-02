"""Orchestrator for the block-based gm/Id sizing pipeline.

``size_gmid`` runs the gm/Id path end-to-end, separate from the Level-1 CP-SAT
sizer, as five phases with explicit hand-offs:

1. **Analyze** (:mod:`.analyze`) — structural view: slots, blocks, cascodes,
   topology-mismatch warnings → :class:`~.analyze.CircuitView`.
2. **Bias currents** (:mod:`.plan`) — per-device IDS from KCL + ``spec.ibias``
   and the rail-referenced load resistors → :class:`~.plan.CurrentPlan`.
3. **Plan** (:mod:`.plan`) — per-stage gm requirements and compensation caps
   from the spec, per-device design intent from the functional-block registry
   → :class:`~.plan.SizingPlan`.
4. **Size** — deterministic geometry from the LUT (:mod:`.geometry`), the DC
   operating-point check and tail repair (:mod:`.bias`), and the non-load
   resistor network (:mod:`.resistors`).
5. **Evaluate** (:mod:`.evaluate`) — cascode-aware analytical metrics.

The model-independent topology math is reused from the
``circuitgenome.sizer.shared`` package rather than duplicated.
"""
from __future__ import annotations

from circuitgenome.recognizer.models import (
    FunctionalBlockRecognitionResult,
    ParsedNetlist,
    SubcircuitRecognitionResult,
)
from circuitgenome.synthesizer.models import TopologyTemplate

from ..shared.models import SizingResult, SizingSpec, TechParams
from .analyze import analyze_circuit
from .bias import check_dc_operating_point
from .evaluate import evaluate_circuit
from .geometry import assign_geometry_gmid
from .intent import DEFAULT_INTENT, GmIdIntent
from .plan import assign_currents, plan_devices
from .resistors import size_resistors


def size_gmid(
    parsed: ParsedNetlist,
    sr_result: SubcircuitRecognitionResult,
    fbr_result: FunctionalBlockRecognitionResult,
    topology: TopologyTemplate,
    tech: TechParams,
    spec: SizingSpec,
    intent: GmIdIntent = DEFAULT_INTENT,
) -> SizingResult:
    """Size a circuit via the gm/Id pipeline.  Requires ``tech.gmid_lut``."""
    # Phase 1 — Analyze: structural view of the recognised circuit.
    view = analyze_circuit(fbr_result, topology)

    # Phase 2 — Bias currents: IDS from KCL, rail-referenced load resistors.
    currents = assign_currents(view, spec, tech)

    # Phase 3 — Plan: gm requirements + compensation caps + per-device intent.
    plan = plan_devices(view, currents, spec, tech, intent)

    # Phase 4 — Size: LUT geometry, DC bias check/repair, resistor network.
    sizing, geom_warnings = assign_geometry_gmid(
        plan.model, view.all_transistors, view.slot_transistors,
        currents.ids_map, plan.tintents, plan.gm_req_map, tech)
    sizing, dc_warnings, bias_feasible = check_dc_operating_point(
        plan.model, view.blocks, view.slot_transistors, view.all_transistors,
        currents.ids_map, sizing, spec, tech)
    extra_r, modifiers = size_resistors(
        view.blocks, view.slot_resistors, currents.ids_map, sizing,
        plan.model, spec, tech, intent)

    # Phase 5 — Evaluate: analytical (ngspice-free) metrics from the sizing.
    metrics, margins = evaluate_circuit(
        view, currents, plan, sizing, modifiers, spec, tech)

    return SizingResult(
        transistors=sizing,
        cc_pf=plan.cc_pf,
        metrics=metrics,
        margins=margins,
        solver_status="GMID",
        cc2_pf=plan.cc2_pf,
        warnings=view.warnings + plan.warnings + geom_warnings + dc_warnings,
        resistors={**currents.load_resistors, **extra_r},
        bias_feasible=bias_feasible,
        transistor_intents=plan.tintents,
    )

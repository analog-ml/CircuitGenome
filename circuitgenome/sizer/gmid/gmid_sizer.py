"""Orchestrator for the block-based gm/Id sizing pipeline.

``size_gmid`` runs the gm/Id path end-to-end, separate from the Level-1 CP-SAT
sizer: build the block view, derive per-stage gm requirements (shared,
model-injected op-amp physics), choose per-device gm/Id geometry from the
:class:`~.intent.GmIdIntent`, check the DC operating point (cascode-aware
headroom), and evaluate the metrics.  The model-independent topology math is
reused from the ``circuitgenome.sizer.shared`` package rather than duplicated.
"""
from __future__ import annotations

from circuitgenome.recognizer.models import (
    FunctionalBlockRecognitionResult,
    ParsedNetlist,
    SubcircuitRecognitionResult,
)
from circuitgenome.synthesizer.models import TopologyTemplate

from ..shared.device_model import CASCODE, CURRENT_SOURCE, SIGNAL, GmIdModel
from ..shared.device_model import GmIdPolicy
from ..shared.gmid_lut import GmIdLut
from ..shared.models import SizingResult, SizingSpec, TechParams
from ..shared.metrics import _evaluate_metrics
from ..shared.preprocess import (
    _assign_ids,
    _check_topology_match,
    _compute_requirements,
    _deduplicate,
    _extract_slot_resistors,
    _extract_slot_transistors,
    _size_load_resistors,
)
from .blocks import _is_signal_dev, build_blocks, cascode_device_refs, node_rout
from .bias import check_dc_operating_point
from .geometry import assign_geometry_gmid
from .intent import DEFAULT_INTENT, GmIdIntent
from .resistors import size_resistors


def _model_for(tech: TechParams, intent: GmIdIntent) -> GmIdModel:
    """Build a :class:`GmIdModel` whose L/region policy comes from ``intent``."""
    policy = GmIdPolicy(
        signal_l_mult=intent.signal_l_mult,
        cs_l_mult=intent.current_source_l_mult,
        cs_gmid=intent.current_source_gm_id,
        signal_nominal_gmid=intent.signal_gm_id,
        cascode_l_mult=intent.cascode_l_mult,
        cascode_gmid=intent.cascode_gm_id,
    )
    return GmIdModel(tech, GmIdLut(tech.gmid_lut), policy)


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
    slot_transistors = _extract_slot_transistors(fbr_result)
    slot_resistors = _extract_slot_resistors(fbr_result)
    blocks = build_blocks(slot_transistors, slot_resistors)
    topology_warnings = _check_topology_match(slot_transistors, topology.name)
    all_transistors = _deduplicate(slot_transistors)
    ids_map = _assign_ids(slot_transistors, all_transistors, spec)

    resistors = _size_load_resistors(slot_resistors, spec, tech)
    gd_load_r = (1.0 / min(resistors.values())) if resistors else 0.0

    model = _model_for(tech, intent)
    gm_req_map, vod_max_map, cc_pf, cc2_pf, ceil_warnings = _compute_requirements(
        slot_transistors, all_transistors, ids_map, tech, spec, model, gd_load_r
    )

    cascodes = cascode_device_refs(slot_transistors)
    role_map = {}
    for ref, (device, _slot) in all_transistors.items():
        if _is_signal_dev(device):
            role_map[ref] = SIGNAL
        elif ref in cascodes:
            role_map[ref] = CASCODE
        else:
            role_map[ref] = CURRENT_SOURCE
    transistor_sizing, geom_warnings = assign_geometry_gmid(
        model, all_transistors, slot_transistors, ids_map, role_map, gm_req_map, tech
    )

    dc_warnings, bias_feasible = check_dc_operating_point(
        model, blocks, slot_transistors, all_transistors, ids_map,
        transistor_sizing, spec, tech
    )

    # Size the non-load resistor blocks (degeneration / tail / bias) and capture
    # their metric effects (degeneration on gm1, resistor-tail on gd_tail).
    extra_r, gm1_factor, gd_tail_override, gd_out_extra = size_resistors(
        blocks, slot_resistors, ids_map, transistor_sizing, model, spec, tech, intent
    )
    resistors = {**resistors, **extra_r}

    # Cascode loads: the single-device-gds estimate misses the gm·ro·ro boost, so
    # compute the first-stage output resistance cascode-aware from the blocks.
    rout1_override = None
    if blocks.has_cascode_load():
        out_net = blocks.first_stage_out_net()
        if out_net:
            tail = blocks.tail_net()
            all_mos = [device for device, _slot in all_transistors.values()]
            stop = frozenset({tail}) if tail else frozenset()
            rout1_override = node_rout(out_net, all_mos, model, transistor_sizing, stop)

    # Analytical (ngspice-free) estimate: a deterministic sizing-quality signal for
    # tests/programmatic callers. The CLI measures PTM performance with ngspice
    # (simulate_metrics) and displays that instead of these numbers.
    metrics, margins = _evaluate_metrics(
        transistor_sizing, slot_transistors, cc_pf, tech, spec, model,
        cc2_pf=cc2_pf, gd_load_r=gd_load_r, rout1_override=rout1_override,
        gm1_factor=gm1_factor, gd_tail_override=gd_tail_override,
        gd_out_extra=gd_out_extra,
    )

    return SizingResult(
        transistors=transistor_sizing,
        cc_pf=cc_pf,
        metrics=metrics,
        margins=margins,
        solver_status="GMID",
        cc2_pf=cc2_pf,
        warnings=topology_warnings + ceil_warnings + geom_warnings + dc_warnings,
        resistors=resistors,
        bias_feasible=bias_feasible,
    )

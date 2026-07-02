"""Phases 2+3 — Bias currents and the per-device sizing plan.

Phase 2 (:func:`assign_currents`) fixes what *cannot* be chosen: every device's
quiescent current follows from ``spec.ibias`` and KCL, and the rail-referenced
load resistors follow from the first-stage branch current.

Phase 3 (:func:`plan_devices`) derives what *must* be achieved and what is
*chosen*: the per-stage gm requirements and compensation caps from the
performance spec (through the gm/Id device model), and the per-device design
intent (role, gm/Id region, L) resolved from the functional-block registry.

Both phases are pure derivations — no geometry exists yet.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..shared.device_model import GmIdModel, GmIdPolicy
from ..shared.gmid_lut import GmIdLut
from ..shared.models import SizingSpec, TechParams
from ..shared.preprocess import assign_ids, compute_requirements, size_load_resistors
from .analyze import CircuitView
from .intent import GmIdIntent, TransistorIntent, resolve_transistor_intents


@dataclass
class CurrentPlan:
    """Quiescent currents and passive loads (Phase 2 output).

    :param ids_map: ref → quiescent IDS in A, assigned from KCL + ``spec.ibias``.
    :param load_resistors: ref → Ω for the rail-referenced ``load`` resistors.
    :param gd_load_r: conductance (1/R) the load resistors add at the
        first-stage output node; 0.0 when the load is active.
    """
    ids_map: dict[str, float]
    load_resistors: dict[str, float]
    gd_load_r: float


@dataclass
class SizingPlan:
    """Per-device targets and design intent (Phase 3 output).

    :param model: the gm/Id device model (LUT + L/region policy from the intent).
    :param gm_req_map: ref → required gm in A/V (0 for non-signal devices).
    :param cc_pf / cc2_pf: compensation cap(s) in pF (``None`` when absent).
    :param tintents: ref → resolved :class:`~.intent.TransistorIntent`.
    :param warnings: gm-ceiling advisories (a spec that needs more gm than the
        device can physically deliver at its bias current).
    """
    model: GmIdModel
    gm_req_map: dict[str, float]
    cc_pf: float | None
    cc2_pf: float | None
    tintents: dict[str, TransistorIntent]
    warnings: list[str]


def assign_currents(view: CircuitView, spec: SizingSpec, tech: TechParams) -> CurrentPlan:
    """Assign per-device quiescent currents and size the load resistors."""
    ids_map = assign_ids(view.slot_transistors, view.all_transistors, spec)
    load_resistors = size_load_resistors(view.slot_resistors, spec, tech)
    gd_load_r = (1.0 / min(load_resistors.values())) if load_resistors else 0.0
    return CurrentPlan(ids_map=ids_map, load_resistors=load_resistors,
                       gd_load_r=gd_load_r)


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


def plan_devices(
    view: CircuitView,
    currents: CurrentPlan,
    spec: SizingSpec,
    tech: TechParams,
    intent: GmIdIntent,
) -> SizingPlan:
    """Derive gm requirements, compensation caps and per-device intent."""
    model = _model_for(tech, intent)
    gm_req_map, _vod_max_map, cc_pf, cc2_pf, ceil_warnings = compute_requirements(
        view.slot_transistors, view.all_transistors, currents.ids_map,
        tech, spec, model, currents.gd_load_r,
    )
    tintents = resolve_transistor_intents(
        view.all_transistors, view.cascode_refs, intent.block_intents)
    return SizingPlan(model=model, gm_req_map=gm_req_map, cc_pf=cc_pf,
                      cc2_pf=cc2_pf, tintents=tintents, warnings=ceil_warnings)

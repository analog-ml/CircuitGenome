"""Phase 5 — Evaluate: analytical performance metrics from the solved sizing.

Wraps the shared (model-injected) metric evaluation with the gm/Id-specific
corrections: the cascode-aware first-stage output resistance (the
single-device-gds estimate misses the gm·ro·ro boost of a cascode load) and the
resistor-network metric modifiers from Phase 4 (degeneration on gm1, resistor
tail on gd_tail, CMFB averager loading on the output).

Analytical (ngspice-free) estimate: a deterministic sizing-quality signal for
tests/programmatic callers.  The CLI measures PTM performance with ngspice
(``spice_sim.simulate_metrics``) and displays that instead of these numbers.
"""
from __future__ import annotations

from ..shared.device_model import GmIdModel
from ..shared.metrics import _evaluate_metrics
from ..shared.models import SizingSpec, TechParams, TransistorSizing
from .analyze import CircuitView
from .blocks import node_rout
from .plan import CurrentPlan, SizingPlan
from .resistors import MetricModifiers


def _cascode_rout1(view: CircuitView, model: GmIdModel,
                   sizing: dict[str, TransistorSizing]) -> float | None:
    """Cascode-aware first-stage output resistance, or ``None`` when the load
    is not a cascode (the single-device estimate is then already right)."""
    blocks = view.blocks
    if not blocks.has_cascode_load():
        return None
    out_net = blocks.first_stage_out_net()
    if not out_net:
        return None
    tail = blocks.tail_net()
    all_mos = [device for device, _slot in view.all_transistors.values()]
    stop = frozenset({tail}) if tail else frozenset()
    return node_rout(out_net, all_mos, model, sizing, stop)


def _cascode_gd_tail(view: CircuitView, model: GmIdModel,
                     sizing: dict[str, TransistorSizing]) -> float | None:
    """Cascode-aware tail output conductance, or ``None`` when the tail is not a
    cascode current source (the single-device estimate is then already right).

    Mirrors :func:`_cascode_rout1` for the CMRR path: ``node_rout`` at the tail
    node walks the cascode stack down to the rail, so the ~``gm·ro`` boost that a
    cascode tail gives (invisible to the single-device ``gds``) reaches CMRR.
    """
    blocks = view.blocks
    if not blocks.has_cascode_tail():
        return None
    tail_net = blocks.tail_net()
    if not tail_net:
        return None
    all_mos = [device for device, _slot in view.all_transistors.values()]
    r = node_rout(tail_net, all_mos, model, sizing, frozenset())
    return 1.0 / r if r and r != float("inf") else None


def evaluate_circuit(
    view: CircuitView,
    currents: CurrentPlan,
    plan: SizingPlan,
    sizing: dict[str, TransistorSizing],
    modifiers: MetricModifiers,
    spec: SizingSpec,
    tech: TechParams,
) -> tuple[dict[str, float], dict[str, float]]:
    """Return ``(metrics, margins)`` for the solved sizing."""
    # Cascode tail conductance (CMRR) wins over the resistor-tail modifier when
    # present; the two tail kinds are mutually exclusive.
    gd_tail_override = _cascode_gd_tail(view, plan.model, sizing)
    if gd_tail_override is None:
        gd_tail_override = modifiers.gd_tail_override
    return _evaluate_metrics(
        sizing, view.slot_transistors, plan.cc_pf, tech, spec, plan.model,
        cc2_pf=plan.cc2_pf,
        gd_load_r=currents.gd_load_r,
        rout1_override=_cascode_rout1(view, plan.model, sizing),
        gm1_factor=modifiers.gm1_factor,
        gd_tail_override=gd_tail_override,
        gd_out_extra=modifiers.gd_out_extra,
    )

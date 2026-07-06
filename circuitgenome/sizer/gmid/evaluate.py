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
from ..shared.taxonomy import RAILS
from .analyze import CircuitView
from .blocks import node_rout
from .plan import CurrentPlan, SizingPlan
from .resistors import MetricModifiers

# Gain-derived metrics that a railed open-loop output makes unmeasurable — the
# small-signal formula sits on an operating point that does not exist (#148).
_RESISTOR_GATED_METRICS = ("gain_db", "gbw_hz", "phase_margin_deg",
                           "cmrr_db", "psrr_db")


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


def _driven_cs_device(view: CircuitView):
    """The next stage's signal device when it is a common-source stage (source
    on a rail), else ``None`` — the device the first-stage output must bias."""
    for slot in ("second_stage", "second_stage_p", "second_stage_n",
                 "third_stage", "third_stage_p", "third_stage_n"):
        b = view.blocks.blocks.get(slot)
        sig = b.signal_device if b else None
        if sig is not None:
            return sig if sig.terminals.get("s") in RAILS else None
    return None


def _resistor_load_bias(
    view: CircuitView, currents: CurrentPlan,
    sizing: dict[str, TransistorSizing], spec: SizingSpec,
) -> tuple[bool, list[str]]:
    """DC validity of a single-ended, rail-referenced resistor first-stage load.

    A fixed load resistor holds the first-stage output ``I·R`` from its
    reference rail, but the driven common-source stage needs its gate exactly
    ``|Vgs|`` from that *same* rail to carry its quiescent current in
    saturation.  The two are sized independently (``R`` from the nominal
    threshold + a fixed overdrive, ``|Vgs|`` from the gm/Id target), so when
    they disagree by more than the driven device's own ``Vdsat`` the second
    stage is pushed out of its intended operating regime and its high-impedance
    output rails open-loop — yet the small-signal formula would still report an
    optimistic ``gm·Rout`` gain (issue #148).

    Returns ``(invalid, notes)``: ``invalid`` gates the gain-derived metrics;
    ``notes`` always carries a corner-fragility advisory because a fixed
    resistor cannot track ``Vth`` across PVT the way an active load can.  Only
    single-ended resistor loads feeding a common-source stage are in scope (a
    fully-differential node is held by CMFB, not the driven gate).
    """
    if currents.gd_load_r <= 0 or view.blocks.is_fully_differential:
        return False, []
    drv = _driven_cs_device(view)
    s = sizing.get(drv.ref) if drv is not None else None
    if s is None:
        return False, []
    v_off = (spec.ibias / 2.0) / currents.gd_load_r  # I·R across the load R
    mismatch = abs(v_off - abs(s.vgs_v))
    fragility = (
        "first-stage load is a fixed rail-referenced resistor: its DC drop is "
        "sized from the nominal threshold and cannot track Vth across PVT "
        "corners, so the inter-stage bias is corner-fragile — verify gain "
        "across corners in SPICE.")
    if mismatch > s.vds_sat_v:
        rail = "gnd" if drv.type == "nmos" else "vdd"
        return True, [
            f"resistor-loaded first stage cannot bias the driven "
            f"{drv.type.upper()} second stage: the load holds the first-stage "
            f"output {v_off:.2f} V from {rail} but the stage needs "
            f"{abs(s.vgs_v):.2f} V (off by {mismatch:.2f} V > Vdsat "
            f"{s.vds_sat_v:.2f} V) — the open-loop output rails; "
            f"gain/GBW/PM/CMRR/PSRR reported as unmeasurable (issue #148).",
            fragility,
        ]
    return False, [fragility]


def evaluate_circuit(
    view: CircuitView,
    currents: CurrentPlan,
    plan: SizingPlan,
    sizing: dict[str, TransistorSizing],
    modifiers: MetricModifiers,
    spec: SizingSpec,
    tech: TechParams,
) -> tuple[dict[str, float], dict[str, float], list[str]]:
    """Return ``(metrics, margins, notes)`` for the solved sizing.

    ``notes`` surfaces the resistor-load DC-bias advisories (:func:`
    _resistor_load_bias`); when that operating point is invalid the
    gain-derived metrics are dropped rather than reported optimistically.
    """
    # Cascode tail conductance (CMRR) wins over the resistor-tail modifier when
    # present; the two tail kinds are mutually exclusive.
    gd_tail_override = _cascode_gd_tail(view, plan.model, sizing)
    if gd_tail_override is None:
        gd_tail_override = modifiers.gd_tail_override
    metrics, margins = _evaluate_metrics(
        sizing, view.slot_transistors, plan.cc_pf, tech, spec, plan.model,
        cc2_pf=plan.cc2_pf,
        gd_load_r=currents.gd_load_r,
        rout1_override=_cascode_rout1(view, plan.model, sizing),
        gm1_factor=modifiers.gm1_factor,
        gd_tail_override=gd_tail_override,
        gd_out_extra=modifiers.gd_out_extra,
    )
    invalid, notes = _resistor_load_bias(view, currents, sizing, spec)
    if invalid:
        for k in _RESISTOR_GATED_METRICS:
            metrics.pop(k, None)
            margins.pop(k, None)
    return metrics, margins, notes

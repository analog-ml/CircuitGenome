"""Phase 5 — Evaluate: analytical performance metrics from the solved sizing.

Builds the shared :class:`~circuitgenome.sizer.physics.stage_chain.StageChain` from the solved
sizing, folds in the Phase-4 resistor-network effects (degeneration on gm1,
resistor tail on gd_tail, CMFB averager loading on the output), and withholds
the gain-derived metrics when the inter-stage DC bias makes them meaningless.

Analytical (ngspice-free) estimate: a deterministic sizing-quality signal for
tests/programmatic callers.  The CLI measures PTM performance with ngspice
(``sizer.simulate_metrics``) and displays that instead of these numbers.
"""
from __future__ import annotations

from ..physics.metrics import evaluate_metrics
from ..models import SizingSpec, TechParams, TransistorSizing
from ..physics.stage_chain import build_stage_chain
from ..physics.taxonomy import RAILS
from .analyze import GmIdCircuitView
from .plan import CurrentPlan, SizingPlan
from .resistors import MetricModifiers


def _driven_cs_device(view: GmIdCircuitView):
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
    view: GmIdCircuitView, currents: CurrentPlan,
    sizing: dict[str, TransistorSizing], spec: SizingSpec,
) -> tuple[bool, list[str]]:
    """DC validity of a single-ended, rail-referenced resistor first-stage load.

    A fixed load resistor holds the first-stage output ``I·R`` from its
    reference rail, but the driven common-source stage needs its gate exactly
    ``|Vgs|`` from that *same* rail to carry its quiescent current in
    saturation.  ``R`` is seeded from the driven signal device's ``Vgs`` read
    from the LUT at the nominal corner (issue #158), so the two normally agree;
    but if the driven device operates far from the seed's nominal gm/Id, they can
    still disagree by more than its own ``Vdsat``, pushing the second stage out of
    its intended regime so its high-impedance output rails open-loop — yet the
    small-signal formula would still report an optimistic ``gm·Rout`` gain
    (issue #148).

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
        "sized from the LUT Vgs at the nominal corner and cannot track Vth "
        "across PVT corners, so the inter-stage bias is corner-fragile — verify "
        "gain across corners in SPICE.")
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
    view: GmIdCircuitView,
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
    chain = build_stage_chain(
        view, sizing, plan.model, spec,
        cc_pf=plan.cc_pf, cc2_pf=plan.cc2_pf, gd_load_r=currents.gd_load_r,
    )
    chain = modifiers.apply(chain)
    invalid, notes = _resistor_load_bias(view, currents, sizing, spec)
    if invalid:
        chain = chain.withheld()
    metrics, margins = evaluate_metrics(chain, spec)
    return metrics, margins, notes

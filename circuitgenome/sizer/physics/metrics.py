"""Performance metrics and spec margins from a :class:`~.stage_chain.StageChain`.

Pure algebra: every small-signal quantity has already been extracted by
:func:`~.stage_chain.build_stage_chain`, so nothing here touches a device, a
technology or a device model.  Both sizers reach this through the same two
arguments — the chain they built and the spec they were asked to meet.
"""
from __future__ import annotations

from . import equations as eq
from ..models import SizingSpec
from .stage_chain import StageChain


def evaluate_metrics(
    chain: StageChain, spec: SizingSpec
) -> tuple[dict[str, float], dict[str, float]]:
    """Return ``(metrics, margins)`` for ``chain`` against ``spec``.

    A margin is present only when the spec constrains that metric, and is
    always signed so **positive means the spec is met**.  A metric the chain
    cannot support (no compensation cap, a railed operating point, an absent
    stage, a mirror pole that cannot be placed) is omitted rather than reported
    as zero.

    Stage count picks the compensation regime, not the metric set: single- and
    multi-stage chains alike report gain, GBW, phase margin, slew rate, CMRR
    and PSRR (issue #221).

    One documented exception survives: a single stage whose load is a resistor
    or a wide-swing telescopic cascode reports no ``phase_margin_deg``, because
    this model cannot place that topology's non-dominant pole -- see
    :func:`~.stage_chain._mirror_pole_hz` for why each is a deliberate omission
    rather than a gap (issue #228).
    """
    metrics: dict[str, float] = {}
    margins: dict[str, float] = {}

    def _record(key: str, value: float, limit: float | None,
                *, is_max: bool = False) -> None:
        metrics[key] = value
        if limit is not None:
            margins[key] = (limit - value) if is_max else (value - limit)

    stages = chain.stages
    k_fs = chain.k_fs
    gm1 = stages[0].gm if stages else 0.0
    # k_fs is the transconductance the Miller loop and the first stage's gain
    # actually see; CMRR uses the raw device gm.
    gm1_loop = k_fs * gm1
    # Stages contribute gain only up to the first one with no finite output
    # resistance — beyond that the cascade product is not defined.
    usable = 0
    while usable < len(stages) and stages[usable].rout < float("inf"):
        usable += 1

    # --- Gain ---
    # An un-derated single-point upper bound: the naive per-stage cascade
    # product with no efficiency derate (#155).  It over-estimates multi-stage
    # DC gain and is not open-loop-measurable above eq.OPEN_LOOP_GAIN_CEILING_DB;
    # callers surface that via SizingResult.open_loop_measurable.
    if chain.gain_measurable and usable:
        stage_gains = [s.gm * s.rout for s in stages[:usable]]
        stage_gains[0] *= k_fs
        if all(g > 0 for g in stage_gains):
            _record("gain_db", eq.open_loop_gain_db(stage_gains), spec.gain_min_db)

    # --- GBW, phase margin, slew rate ---
    # Two compensation regimes, deliberately not sharing a formula (#221).  A
    # multi-stage chain is Miller-compensated: the dominant pole is at Cc and
    # the non-dominant one at the output.  A single-stage OTA is *load*
    # compensated: CL itself sets the dominant pole and the non-dominant one is
    # the mirror node.  The roles of CL and the internal node are exchanged
    # between them, so each regime gets its own branch.
    cc_f = (chain.cc_pf * 1e-12) if chain.cc_pf else None
    cc2_f = (chain.cc2_pf * 1e-12) if chain.cc2_pf else None
    if cc_f and len(stages) > 1 and gm1 > 0:
        if chain.gain_measurable:
            _record("gbw_hz", eq.unity_gain_bw(gm1_loop, cc_f), spec.gbw_min_hz)

            gm2 = stages[1].gm
            pm = None
            if len(stages) > 2 and gm2 > 0 and stages[2].gm > 0 and cc2_f:
                pm = eq.phase_margin_three_stage_deg(
                    gm1_loop, gm2, stages[2].gm, cc_f, cc2_f, spec.cl)
            elif gm2 > 0:
                pm = eq.phase_margin_two_stage_deg(gm1_loop, gm2, cc_f, spec.cl)
            if pm is not None:
                _record("phase_margin_deg", pm, spec.phase_margin_min_deg)

        # Slew is set by ibias/Cc alone, so a railed small-signal operating
        # point does not invalidate it (#148 gates only gain-derived metrics).
        _record("slew_rate_vps", eq.slew_rate_vps(spec.ibias, cc_f),
                spec.slew_rate_min_vps)
    elif len(stages) == 1 and gm1 > 0 and spec.cl > 0:
        # Load-compensated: every equation above still applies with CL in the
        # place of Cc, because CL is what the tail current drives and what sets
        # the dominant pole.  Phase margin is the exception -- it needs the
        # mirror pole, which the chain reports only when it can be placed.
        if chain.gain_measurable:
            gbw = eq.unity_gain_bw(gm1_loop, spec.cl)
            _record("gbw_hz", gbw, spec.gbw_min_hz)
            if chain.mirror_pole_hz:
                _record("phase_margin_deg",
                        eq.phase_margin_single_stage_deg(
                            gbw, chain.mirror_pole_hz),
                        spec.phase_margin_min_deg)

        _record("slew_rate_vps", eq.slew_rate_vps(spec.ibias, spec.cl),
                spec.slew_rate_min_vps)

    # --- Power (always available; a railed output still burns current) ---
    _record("power_w",
            eq.quiescent_power(spec.vdd, spec.vss, list(chain.supply_currents)),
            spec.power_max_w, is_max=True)

    # --- Output swing from the second stage's saturation overdrive ---
    vdsat_p, vdsat_n = chain.swing_vdsat
    if spec.output_swing_max_v is not None and vdsat_p is not None:
        _record("output_swing_max_v", spec.vdd - vdsat_p, spec.output_swing_max_v)
    if spec.output_swing_min_v is not None and vdsat_n is not None:
        _record("output_swing_min_v", spec.vss + vdsat_n,
                spec.output_swing_min_v, is_max=True)

    # --- CMRR: the raw pair gm against the tail's finite conductance ---
    if chain.gain_measurable and gm1 > 0 and chain.gd_tail > 0:
        _record("cmrr_db", eq.cmrr_db(gm1, chain.gd_tail), spec.cmrr_min_db)

    # --- PSRR (approximate, from the output-driving stage and its load) ---
    # Supply ripple reaches the output through the load branch's conductance
    # and is rejected in proportion to that stage's own gm — the second stage
    # on a multi-stage chain, the input pair itself on a single-stage one,
    # where the single-ended tap costs the same factor k_fs the gain sees
    # (#221).  The conductance is the one the load presents to *its own*
    # reference rail, which is the supply for a VDD-referenced load and ground
    # for a GND-referenced one; the estimate does not distinguish the two, and
    # never has (see eq.psrr_db_approx: "accurate PSRR requires simulation").
    gm_out = stages[1].gm if len(stages) > 1 else gm1_loop
    if chain.gain_measurable and gm_out > 0 and chain.gd_output_load > 0:
        _record("psrr_db", eq.psrr_db_approx(gm_out, chain.gd_output_load),
                spec.psrr_min_db)

    return metrics, margins

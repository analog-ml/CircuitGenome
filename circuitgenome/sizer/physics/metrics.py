"""Performance metrics and spec margins from a :class:`~.stage_chain.StageChain`.

Pure algebra: every small-signal quantity has already been extracted by
:func:`~.stage_chain.build_stage_chain`, so nothing here touches a device, a
technology or a device model.  Both sizers reach this through the same two
arguments — the chain they built and the spec they were asked to meet.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace

from . import equations as eq
from ..models import SizingSpec
from .rnmc import RNMC, comp_resistances, worst_half
from .stage_chain import StageChain


def _rnmc_model(chain: StageChain, cc_f: float,
                cc2_f: float) -> tuple[list[tuple[float, float]], dict]:
    """The RNMC full model's per-half compensation resistors and the keyword
    arguments every half shares: the chain's stage gm/rout, node parasitics,
    third-stage mirror and output buffer."""
    s = chain.stages
    caps = chain.node_caps_f or (0.0, 0.0, 0.0)
    g = [1.0 / st.rout if st.rout < float("inf") else 0.0 for st in s[:3]]
    buffer = chain.output_buffer
    pairs = comp_resistances(chain.comp_resistors, s[2].gm, cc_f, cc2_f,
                             buffer[1] if buffer else 0.0)
    kw = dict(c1_f=caps[0], c2_f=caps[1], c3_f=caps[2], g1=g[0], g2=g[1], g3=g[2],
              mirror_pole_hz=chain.third_stage_mirror_pole_hz, buffer=buffer)
    return pairs, kw


def _rnmc_phase_margin(chain: StageChain, gm1_loop: float, cc_f: float,
                       cc2_f: float, cl_f: float) -> float | None:
    """Full-model RNMC phase margin (:func:`~.equations.phase_margin_rnmc_deg`)
    on the worse half when the halves' compensation differs
    (:func:`~.rnmc.worst_half`)."""
    s = chain.stages
    pairs, kw = _rnmc_model(chain, cc_f, cc2_f)
    _, pm = worst_half(gm1_loop, s[1].gm, s[2].gm, cc_f, cc2_f, cl_f, pairs, **kw)
    return pm


def _rnmc_gbw(chain: StageChain, gm1_loop: float, cc_f: float,
              cc2_f: float, cl_f: float) -> float | None:
    """Full-model RNMC GBW (:func:`~.equations.unity_gain_bw_rnmc_hz`), the
    lower of the halves'; ``None`` when a half never reaches 0 dB."""
    s = chain.stages
    pairs, kw = _rnmc_model(chain, cc_f, cc2_f)
    gbws = [eq.unity_gain_bw_rnmc_hz(gm1_loop, s[1].gm, s[2].gm, cc_f, cc2_f,
                                     cl_f, comp_r=r, **kw) for r in pairs]
    return None if None in gbws else min(gbws)


def _miller_loop(chain: StageChain, gm1_loop: float, cc_f: float,
                 cc2_f: float | None, cl_f: float) -> tuple[float | None, float | None]:
    """``(GBW, PM)`` of a two-stage or nested-Miller chain from its full model
    (:func:`~.equations.two_stage_loop`, :func:`~.equations.nested_miller_loop`):
    one half single-ended, both halves of a fully-differential chain with its
    CMFB coupling them (``chain.cmfb_coupling``).

    Each series resistor is the one ``size_resistors`` sizes for these
    schemes, ``(Cc + CL)/(gm_out·Cc)`` against the last gain stage's gm, which
    puts its zero on the output pole.
    """
    s = chain.stages
    caps = chain.node_caps_f or (0.0,) * len(s)
    g = [1.0 / st.rout if st.rout < float("inf") else 0.0 for st in s]
    gm_out = s[-1].gm

    def r(flag: bool, c_f: float) -> float:
        return eq.compensation_resistor_ohm(c_f, cl_f, gm_out) if flag and gm_out > 0 else 0.0
    halves = chain.comp_resistors or ((False, False),)
    if len(s) > 2:
        return eq.nested_miller_loop(
            gm1_loop, s[1].gm, s[2].gm, cc_f, cc2_f, cl_f,
            c1_f=caps[0], c2_f=caps[1], c3_f=caps[2], g1=g[0], g2=g[1], g3=g[2],
            mirror_pole_hz=chain.second_stage_mirror_pole_hz,
            buffer=chain.output_buffer, cmfb=chain.cmfb_coupling,
            comp_r=tuple((r(r1, cc_f), r(r2, cc2_f)) for r1, r2 in halves))
    return eq.two_stage_loop(
        gm1_loop, s[1].gm, cc_f, cl_f, c1_f=caps[0], c2_f=caps[1], g1=g[0], g2=g[1],
        buffer=chain.output_buffer, cmfb=chain.cmfb_coupling,
        r_ohm=tuple(r(r1, cc_f) for r1, _r2 in halves))


#: How far the two-stage re-plan aims past each spec -- degrees of phase
#: margin, a fraction of GBW -- so the re-sized geometry's rounding cannot
#: leave it a hair short.
_PM_AIM_DEG = 0.5
_GBW_AIM = 0.005


def two_stage_replan(chain: StageChain, spec: SizingSpec,
                     c1_at: Callable[[float], float], gm1_max: float,
                     gm2_max: float) -> tuple[float | None, float | None]:
    """``(gm1, gm2)`` in A/V a two-stage chain needs to meet its GBW and
    phase-margin specs in the full model (:func:`~.equations.two_stage_loop`).

    The requirement side sizes both from closed forms -- ``gm1/(2π·Cc)`` and
    ``90° − arctan(gm1·CL/(gm2·Cc))`` -- which leave out the output pole's
    pull on the 0 dB crossing, the bare Miller cap's right-half-plane zero
    and the first-stage node's capacitance; with them a design sized to a
    spec reads up to 2-5° short of it (issue #274).  gm1 is raised first, to
    the least that meets GBW; then gm2, to the least that meets the phase
    margin at that gm1 (a faster output pole only lifts GBW further).
    ``c1_at(gm2)`` is the first-stage output's capacitance once the second
    stage is re-sized for ``gm2``; each gm is searched up to its ``*_max``.

    :returns: Each entry the raised gm, or ``None`` when it needs no raise,
        no value up to its maximum meets the spec, or the chain is not a
        two-stage one.
    """
    s = chain.stages
    if (len(s) != 2 or not chain.cc_pf or s[0].gm <= 0 or s[1].gm <= 0
            or not chain.gain_measurable):
        return None, None
    cc_f = chain.cc_pf * 1e-12
    caps = list(chain.node_caps_f or (0.0, 0.0))

    def loop(gm1: float, gm2: float) -> tuple[float, float]:
        caps[0] = c1_at(gm2)
        trial = replace(chain, stages=(replace(s[0], gm=gm1), replace(s[1], gm=gm2)),
                        node_caps_f=tuple(caps))
        gbw, pm = _miller_loop(trial, chain.k_fs * gm1, cc_f, None, spec.cl)
        return (gbw or 0.0), (-math.inf if pm is None else pm)

    def least(lo: float, hi: float, ok: Callable[[float], bool]) -> float | None:
        """Smallest value in ``[lo, hi]`` for which ``ok`` holds (monotone)."""
        if not ok(hi):
            return None
        for _ in range(40):
            mid = math.sqrt(lo * hi)
            lo, hi = (lo, mid) if ok(mid) else (mid, hi)
        return hi

    gm1, gm2 = s[0].gm, s[1].gm
    new_gm1 = new_gm2 = None
    gbw_min = spec.gbw_min_hz
    if gbw_min and loop(gm1, gm2)[0] < gbw_min:
        new_gm1 = least(gm1, max(gm1_max, gm1),
                        lambda g: loop(g, gm2)[0] >= gbw_min * (1.0 + _GBW_AIM))
        gm1 = new_gm1 or gm1
    pm_min = spec.phase_margin_min_deg
    if pm_min is not None and loop(gm1, gm2)[1] < pm_min:
        new_gm2 = least(gm2, max(gm2_max, gm2),
                        lambda g: loop(gm1, g)[1] >= pm_min + _PM_AIM_DEG)
    return new_gm1, new_gm2


def _slew_rate(chain: StageChain, cc_f: float, cc2_f: float | None,
               cl_f: float) -> float | None:
    """Slew rate: the slowest node, each charged by the one current that feeds it.

    A large step drives every stage into its current limit at once, and the
    output can move no faster than the slowest of these (``I / C``,
    :func:`~.equations.slew_rate_vps`):

    * the first stage steering ``k_slew`` of its tail current into the outer
      Miller cap;
    * the last gain stage's quiescent current into every cap on its output
      node -- the Miller caps whose far end is there, plus ``CL`` unless a
      follower sits behind it;
    * nested Miller only: the second stage into the inner cap ``Cc2``, which
      also ends on the output (reversed nested Miller hangs ``Cc2`` between
      the first two stages, off the output, so it adds no limit);
    * a follower output stage's bias current into ``CL``.

    A limit whose stage current is unknown (``0``) is skipped; ``None`` when
    none is known.
    """
    s = chain.stages
    nested = len(s) > 2 and bool(cc2_f) and chain.compensation_scheme != RNMC
    c_out = cc_f + (cc2_f if nested else 0.0)
    if chain.buffer_ids is None:
        c_out += cl_f
    limits = [(chain.k_slew * s[0].ids, cc_f), (s[-1].ids, c_out)]
    if nested:
        limits.append((s[1].ids, cc2_f))
    if chain.buffer_ids is not None:
        limits.append((chain.buffer_ids, cl_f))
    rates = [eq.slew_rate_vps(i, c) for i, c in limits if i > 0]
    return min(rates) if rates else None


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
    reports no ``phase_margin_deg``, because in this model it has no
    non-dominant pole to place -- see :func:`~.stage_chain._mirror_pole_hz`
    for why that is a deliberate omission rather than a gap (issue #228).
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
    # callers surface that via SizingResult.open_loop_gain_measurable.
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
            # GBW and phase margin both come off the amplifier's full
            # small-signal model (issue #274); a chain without one, or a loop
            # that never reaches 0 dB, falls back to gm1/(2π·Cc) behind the
            # output follower's gain.
            buffer = chain.output_buffer
            fol_gain = buffer[0] / (buffer[0] + buffer[2]) if buffer else 1.0
            gm2 = stages[1].gm
            gbw = pm = None
            if len(stages) > 2 and gm2 > 0 and stages[2].gm > 0 and cc2_f:
                if chain.compensation_scheme == RNMC:
                    pm = _rnmc_phase_margin(chain, gm1_loop, cc_f, cc2_f, spec.cl)
                    gbw = _rnmc_gbw(chain, gm1_loop, cc_f, cc2_f, spec.cl)
                else:
                    gbw, pm = _miller_loop(chain, gm1_loop, cc_f, cc2_f, spec.cl)
            elif len(stages) == 2 and gm2 > 0:
                gbw, pm = _miller_loop(chain, gm1_loop, cc_f, None, spec.cl)
            elif gm2 > 0:   # a three-stage chain with no Cc2 or no third stage
                pm = eq.phase_margin_two_stage_deg(gm1_loop, gm2, cc_f, spec.cl)
            _record("gbw_hz", gbw or eq.unity_gain_bw(gm1_loop, cc_f, gain=fol_gain),
                    spec.gbw_min_hz)
            if pm is not None:
                _record("phase_margin_deg", pm, spec.phase_margin_min_deg)

        # Slew is set by bias currents and caps alone, so a railed small-signal
        # operating point does not invalidate it (#148 gates only gain-derived
        # metrics).
        slew = _slew_rate(chain, cc_f, cc2_f, spec.cl)
        if slew is not None:
            _record("slew_rate_vps", slew, spec.slew_rate_min_vps)
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

        if stages[0].ids > 0:
            _record("slew_rate_vps",
                    eq.slew_rate_vps(chain.k_slew * stages[0].ids, spec.cl),
                    spec.slew_rate_min_vps)

    # --- Power (always available; a railed output still burns current) ---
    _record("power_w",
            eq.quiescent_power(spec.vdd, spec.vss, list(chain.supply_currents)),
            spec.power_max_w, is_max=True)

    # --- Output swing: each rail minus the output's headroom to it ---
    head_hi, head_lo = chain.swing_headroom
    if spec.output_swing_max_v is not None and head_hi is not None:
        _record("output_swing_max_v", spec.vdd - head_hi, spec.output_swing_max_v)
    if spec.output_swing_min_v is not None and head_lo is not None:
        _record("output_swing_min_v", spec.vss + head_lo,
                spec.output_swing_min_v, is_max=True)

    # --- CMRR and PSRR ---
    # Single-ended: the DC nodal solve of the whole circuit the chain carries,
    # which follows each topology's own common-mode and supply paths (#290).
    # Fully differential, and telescopic cascode loads (kept on the closed
    # form until the level estimate can see their drain imbalance, #290):
    # closed forms.  CMRR is the raw pair gm against the tail's finite
    # conductance; PSRR+ the output-driving stage's gm against its load's.
    gm_out = stages[1].gm if len(stages) > 1 else gm1_loop
    if chain.se_rejection_db is not None:
        if chain.gain_measurable:
            for key, value, limit in zip(("cmrr_db", "psrr_db"), chain.se_rejection_db,
                                         (spec.cmrr_min_db, spec.psrr_min_db)):
                if value is not None:
                    _record(key, value, limit)
    elif chain.gain_measurable:
        if gm1 > 0 and chain.gd_tail > 0:
            _record("cmrr_db", eq.cmrr_db(gm1, chain.gd_tail), spec.cmrr_min_db)
        if gm_out > 0 and chain.gd_output_load > 0:
            _record("psrr_db", eq.psrr_db_approx(gm_out, chain.gd_output_load),
                    spec.psrr_min_db)

    return metrics, margins

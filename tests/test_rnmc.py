"""Reversed-nested-Miller (RNMC) compensation: stability, PM model, sizing.

The numbers are the frozen ptm45 FD fixture's (``_FD_THREE_STAGE_NETLIST`` in
``test_spice_sim.py``): Cc1 = 0.5 pF, Cc2 = 0.125 pF, CL = 2 pF, the input
pair at gm1 ≈ 169 µS after bias repair, the second stage at its minimum width
(gm2 ≈ 0.23 mS) and the third stage at its ceiling (gm3 ≈ 1.73 mS) with a
~160 fF input gate on the second-stage output.  SPICE measured that sizing
with a right-half-plane response; raising gm2 to 0.72 mS alone left gain
coming back above 0 dB after the phase passed −180°.
"""
from dataclasses import replace

import pytest

from circuitgenome.sizer import SizingSpec
from circuitgenome.sizer.physics import equations as eq
from circuitgenome.sizer.physics import rnmc
from circuitgenome.sizer.physics.metrics import evaluate_metrics
from circuitgenome.sizer.physics.stage_chain import Stage, StageChain

CC1, CC2, CL = 0.5e-12, 0.125e-12, 2e-12
GM1, GM3, C2 = 169e-6, 1.73e-3, 160e-15
G = (5e-7, 1.5e-5, 3.7e-5)
PARASITICS = dict(c1_f=1e-15, c2_f=C2, g1=G[0], g2=G[1], g3=G[2],
                  mirror_pole_hz=840e6)


# --------------------------------------------------------------------------- #
# Stability condition gm3 < gm2·(1 + CL/Cc1)
# --------------------------------------------------------------------------- #
def damping(gm2, gm3, c2_f=0.0):
    """Full-model damping of the bare loop: no mirror, buffer or conductances."""
    return eq.rnmc_pole_damping(GM1, gm2, gm3, CC1, CC2, CL, c2_f=c2_f)


def test_fixture_sizing_violates_the_stability_condition():
    """gm3 ≈ 7.5·gm2 > 5·gm2 at Cc1 = CL/4: the pole pair is in the RHP."""
    assert GM3 > 0.23e-3 * (1 + CL / CC1)
    assert damping(0.23e-3, GM3, C2) < 0


def test_stability_boundary_is_one_plus_cl_over_cc1():
    gm2 = 1e-3
    assert damping(gm2, 4.99e-3) > 0
    assert damping(gm2, 5.01e-3) < 0


def test_min_gm2_hits_the_damping_target_exactly():
    """The closed form agrees with the full model on the bare loop."""
    gm2 = eq.rnmc_min_gm2(GM3, CC1, CC2, CL, C2, zeta=0.7)
    assert damping(gm2, GM3, C2) == pytest.approx(0.7, abs=1e-6)
    # Damping needs more than bare stability.
    assert gm2 > GM3 * CC1 / (CL + CC1)


def test_second_stage_output_parasitic_raises_the_gm2_floor():
    """The third stage's input gate on the Cc2 node slows the inner pair."""
    assert (eq.rnmc_min_gm2(GM3, CC1, CC2, CL, C2)
            > eq.rnmc_min_gm2(GM3, CC1, CC2, CL, 0.0))


# --------------------------------------------------------------------------- #
# Full-model phase margin
# --------------------------------------------------------------------------- #
def test_rhp_inner_pair_reports_zero_margin():
    """The fixture's as-built sizing: the old formula said ~59°, SPICE said RHP."""
    assert eq.phase_margin_three_stage_deg(GM1, 0.23e-3, GM3, CC1, CC2, CL) > 55
    assert eq.phase_margin_rnmc_deg(GM1, 0.23e-3, GM3, CC1, CC2, CL,
                                    **PARASITICS) == 0.0


def test_round_off_on_the_dominant_pole_is_not_a_rhp_pole():
    """With no output conductances the loop's DC gain is ~1e27, so its
    dominant pole (~4e-20 rad/s) sits 27 decades below the fastest one, far
    below what the eigensolver resolves beside it: it comes back as ±1e-17
    rad/s with a sign set by round-off (+6e-17 at gm2 = 10 mS on one
    LAPACK build), which used to read as an RHP pole and a 0° margin.  A
    real RHP pole, or a complex pair, still counts."""
    noise = [complex(6e-17, 0), complex(-1.76e9, 0), complex(-2.27e10, 0)]
    assert not eq._has_rhp_pole(noise)
    assert eq._has_rhp_pole([complex(1e3, 0), complex(-1e9, 0)])
    assert eq._has_rhp_pole([complex(-1e3, 0), complex(2e8, 9e8),
                             complex(2e8, -9e8)])
    for gm2 in (10e-3, 11.5e-3, 13.225e-3):
        assert eq.phase_margin_rnmc_deg(20e-6, gm2, 1e-3, CC1, CC2, CL) > 85


def test_second_zero_db_crossing_is_a_negative_margin():
    """A lightly damped pair lifts the gain back above 0 dB past −180°."""
    assert eq.rnmc_pole_damping(GM1, 0.72e-3, GM3, CC1, CC2, CL, **PARASITICS) > 0
    pm = eq.phase_margin_rnmc_deg(GM1, 0.72e-3, GM3, CC1, CC2, CL, **PARASITICS)
    assert pm < 0


def test_damped_design_has_a_sane_margin():
    """gm2 at its ceiling, gm3 capped: SPICE measured ~59° on this sizing."""
    pm = eq.phase_margin_rnmc_deg(GM1, 0.9e-3, 0.45e-3, CC1, CC2, CL,
                                  **{**PARASITICS, "c2_f": 1.4e-15, "c1_f": 160e-15})
    assert 50 < pm < 70


def test_far_nondominant_poles_approach_ninety_degrees():
    """With gm2, gm3 huge the loop is a pure integrator at crossover."""
    pm = eq.phase_margin_rnmc_deg(1e-5, 1.0, 1.0, CC1, CC2, CL)
    assert pm == pytest.approx(90.0, abs=1.0)


def test_no_crossing_returns_none():
    """A loop whose DC gain is below 0 dB has no margin to report."""
    assert eq.phase_margin_rnmc_deg(1e-6, 1e-6, 1e-6, CC1, CC2, CL,
                                    g1=1e-3, g2=1e-3, g3=1e-3) is None


_GF180 = (60e-6, 0.913e-3, 1.15e-3, 1.25e-12, 0.9375e-12, 5e-12)
_GF180_KW = dict(c1_f=161e-15, c2_f=57.5e-15, g1=1 / 1.33e6, g2=1 / 1.98e5,
                 g3=1 / 1.18e5, mirror_pole_hz=512e6)


@pytest.mark.parametrize("args, kw, zeta, pm", [
    pytest.param((GM1, 1.5e-3, GM3, CC1, CC2, CL),
                 dict(c1_f=1e-15, c2_f=C2, g1=G[0], g2=G[1], g3=G[2]),
                 0.292159, 82.2304, id="no-mirror"),
    pytest.param((GM1, 1.5e-3, GM3, CC1, CC2, CL), PARASITICS,
                 0.125160, 82.2211, id="mirror-lightly-damped"),
    pytest.param((GM1, 0.9e-3, 0.45e-3, CC1, CC2, CL),
                 {**PARASITICS, "c2_f": 1.4e-15, "c1_f": 160e-15},
                 0.617125, 58.8968, id="mirror-damped"),
    pytest.param((GM1, 0.72e-3, GM3, CC1, CC2, CL), PARASITICS,
                 0.039044, -94.5424, id="second-crossing"),
    pytest.param(_GF180, _GF180_KW, 0.564310, 78.5515, id="gf180"),
    pytest.param(_GF180, dict(_GF180_KW, buffer=(0.3e-3, 19.9e-15, 1e-5)),
                 -0.216649, 0.0, id="gf180-buffered-rhp"),
])
def test_full_model_values_are_pinned(args, kw, zeta, pm):
    """Damping and PM of the full model on the fixtures, frozen so a change
    to how the model is solved cannot move them unnoticed."""
    assert eq.rnmc_pole_damping(*args, **kw) == pytest.approx(zeta, abs=1e-6)
    assert eq.phase_margin_rnmc_deg(*args, **kw) == pytest.approx(pm, abs=1e-3)


def test_metrics_use_the_rnmc_model_for_rnmc_chains():
    stages = (Stage(GM1, 2e6), Stage(0.23e-3, 7e4), Stage(GM3, 3e4))
    spec = SizingSpec(vdd=1.0, vss=0.0, ibias=15e-6, cl=CL,
                      phase_margin_min_deg=60)
    kw = dict(stages=stages, cc_pf=0.5, cc2_pf=0.125)
    nmc = evaluate_metrics(StageChain(**kw), spec)[0]
    rn = evaluate_metrics(StageChain(**kw, compensation_scheme=rnmc.RNMC,
                                     node_caps_f=(1e-15, C2, 0.0)), spec)[0]
    assert nmc["phase_margin_deg"] > 55          # the optimistic two-pole model
    assert rn["phase_margin_deg"] == 0.0         # the RHP pair, reported honestly


# --------------------------------------------------------------------------- #
# design_rnmc: raise gm2, then Cc2, cap gm3 last
# --------------------------------------------------------------------------- #
def test_design_raises_gm2_first():
    """With headroom on gm2, only gm2 moves — Cc2 and gm3 are untouched."""
    d = rnmc.design_rnmc(20e-6, 0.23e-3, GM3, cc1_f=CC1, cc2_f=CC2, cl_f=CL,
                         gm2_max=20e-3, pm_min_deg=60, c2_f=C2)
    assert d.met
    assert d.gm2 > 0.23e-3
    assert d.cc2_f == CC2 and d.gm3 == GM3
    assert d.zeta >= rnmc.ZETA_TARGET - 1e-9 and d.pm_deg >= 60


def test_design_leaves_a_sound_design_alone():
    d = rnmc.design_rnmc(20e-6, 10e-3, 1e-3, cc1_f=CC1, cc2_f=CC2, cl_f=CL,
                         gm2_max=20e-3, pm_min_deg=60)
    assert d.met and (d.gm2, d.cc2_f, d.gm3) == (10e-3, CC2, 1e-3)


def test_design_caps_gm3_only_when_gm2_and_cc2_run_out():
    """The fixture: gm2 ceiling 0.9 mS cannot damp gm3 = 1.73 mS at any Cc2."""
    d = rnmc.design_rnmc(GM1, 0.23e-3, GM3, cc1_f=CC1, cc2_f=CC2, cl_f=CL,
                         gm2_max=0.9e-3, gm3_min=0.45e-3, pm_min_deg=60,
                         c1_f=1e-15, c2_f=C2, g=G, mirror_pole_hz=840e6)
    assert d.gm2 == pytest.approx(0.9e-3)
    assert d.gm3 < GM3
    assert d.zeta > 0
    assert d.pm_deg is not None and d.pm_deg > 45


def test_design_scores_parasitics_at_the_chosen_gm():
    """Raising gm2 widens the second stage's gate on the Cc node: the design
    must score its PM with that wider gate, not the one it started with."""
    def c1(gm2):
        return 200e-15 * gm2 / 0.9e-3        # ~200 fF at the gm2 ceiling

    d = rnmc.design_rnmc(GM1, 0.23e-3, GM3, cc1_f=CC1, cc2_f=CC2, cl_f=CL,
                         gm2_max=0.9e-3, gm3_min=0.45e-3, pm_min_deg=60,
                         c1_f=c1, c2_f=C2, g=G, mirror_pole_hz=840e6)
    expected = eq.phase_margin_rnmc_deg(
        GM1, d.gm2, d.gm3, CC1, d.cc2_f, CL, c1_f=c1(d.gm2),
        c2_f=C2 * d.gm3 / GM3, g1=G[0], g2=G[1], g3=G[2], mirror_pole_hz=840e6)
    assert d.pm_deg == pytest.approx(expected)


def test_output_follower_inside_the_cc1_loop_is_seen():
    """A buffered gf180 FD candidate that SPICE's settling gate caught ringing.

    Modelled with CL on the third-stage output (no buffer) the loop looks
    stable; with the source follower in place the third stage drives only the
    follower's gate, whose input turns negative-resistive above gm_f/CL, and
    the inner pair lands in the right half plane."""
    args = (60e-6, 0.913e-3, 1.15e-3, 1.25e-12, 0.9375e-12, 5e-12)
    kw = dict(c1_f=161e-15, c2_f=57.5e-15, g1=1 / 1.33e6, g2=1 / 1.98e5,
              g3=1 / 1.18e5, mirror_pole_hz=512e6)
    assert eq.rnmc_pole_damping(*args, **kw) > 0
    assert eq.phase_margin_rnmc_deg(*args, **kw) > 45
    buffered = dict(kw, buffer=(0.3e-3, 19.9e-15, 1e-5))
    assert eq.rnmc_pole_damping(*args, **buffered) < 0
    assert eq.phase_margin_rnmc_deg(*args, **buffered) == 0.0


# --------------------------------------------------------------------------- #
# Buffered RNMC: compensation resistors and the third-stage output (#266)
# --------------------------------------------------------------------------- #
# #266's gf180 FD design #7210 as SPICE's .op measures it: gm3 ≈ 1.05·gm2,
# 1.25 pF caps, a source follower driving the 5 pF load, and ~32 fF of drains
# on the third-stage output -- all that node carries at the inner loop's
# ~120 MHz, where the follower's output pole (9.6 MHz) has long since shorted
# CL away.
_BUF = (0.1e-3, 0.573e-3, 0.601e-3, 1.25e-12, 1.25e-12, 5e-12)
_BUF_KW = dict(c1_f=182.5e-15, c2_f=57.2e-15, g1=1e-9, g2=2.3e-6, g3=7.54e-6,
               mirror_pole_hz=747e6, buffer=(0.3014e-3, 5.38e-15, 40.4e-6),
               c3_f=32e-15)


def test_cc2_series_resistor_damps_the_buffered_pair():
    """SPICE: #7210 rings at ~120 MHz with bare caps and settles once ~1/gm
    sits in series with Cc2, but not with Cc1.  A bare Cc2 feeds the
    first-stage output forward into the second's (a right-half-plane zero
    near gm2/Cc2 ≈ 73 MHz); the resistor cancels it."""
    assert eq.rnmc_pole_damping(*_BUF, **_BUF_KW) < 0
    assert eq.phase_margin_rnmc_deg(*_BUF, **_BUF_KW) == 0.0
    assert eq.rnmc_pole_damping(*_BUF, **_BUF_KW, comp_r=(1700.0, 0.0)) < 0
    assert eq.rnmc_pole_damping(*_BUF, **_BUF_KW, comp_r=(0.0, 1700.0)) > 0.7
    assert eq.phase_margin_rnmc_deg(*_BUF, **_BUF_KW, comp_r=(0.0, 1700.0)) > 30


def test_third_stage_output_capacitance_damps_the_buffered_pair():
    """SPICE: +300 fF from each third-stage output to ground settles #7210,
    +100 fF still rings.  The model's pair moves left with every femtofarad
    and crosses into the left half plane near +100 fF -- a little early."""
    def zeta(c3):
        return eq.rnmc_pole_damping(*_BUF, **dict(_BUF_KW, c3_f=c3))
    assert zeta(32e-15) < 0 < zeta(332e-15)
    assert zeta(32e-15) < zeta(132e-15) < zeta(232e-15) < zeta(332e-15)


def test_comp_resistances_follow_the_sizing_rule_per_half():
    """Each half gets the resistor size_resistors would give its branch
    (≈ 1/gm3 behind a buffer), a bare cap 0; identical halves collapse."""
    cc1, cc2, cbuf, gm3 = 1.25e-12, 0.6e-12, 8e-15, 0.6e-3
    r1 = eq.compensation_resistor_ohm(cc1, cbuf, gm3)
    r2 = eq.compensation_resistor_ohm(cc2, cbuf, gm3)
    assert r1 == pytest.approx((cc1 + cbuf) / (gm3 * cc1))
    assert rnmc.comp_resistances(((False, True), (True, False)), gm3, cc1, cc2,
                                 cbuf) == [(0.0, r2), (r1, 0.0)]
    assert rnmc.comp_resistances(((True, True), (True, True)), gm3, cc1, cc2,
                                 cbuf) == [(r1, r2)]
    assert rnmc.comp_resistances((), gm3, cc1, cc2, cbuf) == [(0.0, 0.0)]


def test_rnmc_gbw_is_the_full_models_first_crossing():
    """Issue #274: the buffered RNMC loop crosses 0 dB well under
    gm1/(2π·Cc1) -- the pair near crossover and the follower's gain below 1
    both pull it, and SPICE measured ~20 % under -- while stages far faster
    than the crossover leave it there."""
    gm1, gm2, gm3, cc1, cc2, cl = _BUF
    gbw = eq.unity_gain_bw_rnmc_hz(*_BUF, **_BUF_KW, comp_r=(0.0, 1700.0))
    nominal = eq.unity_gain_bw(gm1, cc1)
    gm_f, _cgs, g_f = _BUF_KW["buffer"]
    assert gbw < eq.unity_gain_bw(gm1, cc1, gain=gm_f / (gm_f + g_f)) < nominal
    assert 0.6 * nominal < gbw
    fast = eq.unity_gain_bw_rnmc_hz(gm1, 100 * gm2, 100 * gm3, cc1, cc2, cl)
    assert fast == pytest.approx(nominal, rel=0.01)


def test_metrics_report_the_worse_half():
    """The halves of an FD amplifier can carry different compensation
    variants; one ringing half makes the whole amplifier ring."""
    gm1, gm2, gm3, cc1, cc2, cl = _BUF
    kw = _BUF_KW
    stages = (Stage(gm1, 1 / kw["g1"]), Stage(gm2, 1 / kw["g2"]),
              Stage(gm3, 1 / kw["g3"]))
    spec = SizingSpec(vdd=3.3, vss=0.0, ibias=20e-6, cl=cl)
    chain = StageChain(stages=stages, cc_pf=cc1 * 1e12, cc2_pf=cc2 * 1e12,
                       compensation_scheme=rnmc.RNMC,
                       node_caps_f=(kw["c1_f"], kw["c2_f"], kw["c3_f"]),
                       third_stage_mirror_pole_hz=kw["mirror_pole_hz"],
                       output_buffer=kw["buffer"])

    def pm(halves):
        return evaluate_metrics(replace(chain, comp_resistors=halves),
                                spec)[0]["phase_margin_deg"]
    assert pm(((False, True), (False, True))) > 30
    assert pm(((False, True), (False, False))) == 0.0


def test_unbuffered_rnmc_models_its_resistors_at_one_over_gm3():
    """Issue #269: without a buffer, size_resistors sizes RNMC compensation
    resistors at 1/gm3 (no load term), and the model applies the same value
    -- it no longer leaves them out."""
    gm3 = 0.6e-3
    assert rnmc.comp_resistances(((True, True),), gm3, CC1, CC2, 0.0) == \
        [(pytest.approx(1 / gm3), pytest.approx(1 / gm3))]
    stages = (Stage(GM1, 2e6), Stage(1.5e-3, 7e4), Stage(gm3, 3e4))
    spec = SizingSpec(vdd=1.0, vss=0.0, ibias=15e-6, cl=CL)
    chain = StageChain(stages=stages, cc_pf=CC1 * 1e12, cc2_pf=CC2 * 1e12,
                       compensation_scheme=rnmc.RNMC,
                       node_caps_f=(1e-15, C2, 0.0))

    pm = evaluate_metrics(replace(chain, comp_resistors=((True, False),)),
                          spec)[0]["phase_margin_deg"]
    expected = eq.phase_margin_rnmc_deg(
        GM1, 1.5e-3, gm3, CC1, CC2, CL, c1_f=1e-15, c2_f=C2,
        g1=1 / 2e6, g2=1 / 7e4, g3=1 / 3e4, comp_r=(1 / gm3, 0.0))
    assert pm == pytest.approx(expected)

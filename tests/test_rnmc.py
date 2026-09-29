"""Reversed-nested-Miller (RNMC) compensation: stability, PM model, sizing.

The numbers are the frozen ptm45 FD fixture's (``_FD_THREE_STAGE_NETLIST`` in
``test_spice_sim.py``): Cc1 = 0.5 pF, Cc2 = 0.125 pF, CL = 2 pF, the input
pair at gm1 ≈ 169 µS after bias repair, the second stage at its minimum width
(gm2 ≈ 0.23 mS) and the third stage at its ceiling (gm3 ≈ 1.73 mS) with a
~160 fF input gate on the second-stage output.  SPICE measured that sizing
with a right-half-plane response; raising gm2 to 0.72 mS alone left gain
coming back above 0 dB after the phase passed −180°.
"""
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
def test_fixture_sizing_violates_the_stability_condition():
    """gm3 ≈ 7.5·gm2 > 5·gm2 at Cc1 = CL/4: the inner pair is in the RHP."""
    assert not rnmc_stable(0.23e-3, GM3)
    assert eq.rnmc_inner_damping(0.23e-3, GM3, CC1, CC2, CL, C2) < 0


def test_stability_boundary_is_one_plus_cl_over_cc1():
    gm2 = 1e-3
    assert rnmc_stable(gm2, 4.99e-3)
    assert not rnmc_stable(gm2, 5.01e-3)


def rnmc_stable(gm2, gm3):
    return eq.rnmc_stable(gm2, gm3, CC1, CL)


def test_min_gm2_hits_the_damping_target_exactly():
    gm2 = eq.rnmc_min_gm2(GM3, CC1, CC2, CL, C2, zeta=0.7)
    assert eq.rnmc_inner_damping(gm2, GM3, CC1, CC2, CL, C2) == pytest.approx(0.7)
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


def test_second_zero_db_crossing_is_a_negative_margin():
    """A lightly damped pair lifts the gain back above 0 dB past −180°."""
    assert eq.rnmc_stable(0.72e-3, GM3, CC1, CL)
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
    assert eq.rnmc_stable(d.gm2, d.gm3, CC1, CL)
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
